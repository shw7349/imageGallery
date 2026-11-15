"""공통 의존성."""

import hmac
import ipaddress
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.api import sessions
from app.config import Settings
from app.db import get_session
from app.errors import AuthenticationRequired, Forbidden, NotFound
from app.models import AdminUser
from app.services import auth as auth_service
from app.storage.s3 import S3Storage

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def get_settings_dep(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_storage(request: Request) -> S3Storage:
    storage: S3Storage = request.app.state.storage
    return storage


DbSession = Annotated[Session, Depends(get_session)]
AppSettings = Annotated[Settings, Depends(get_settings_dep)]
Storage = Annotated[S3Storage, Depends(get_storage)]


def require_admin_host(request: Request, settings: AppSettings) -> None:
    """관리자 API는 관리자 전용 도메인(ADMIN_HOSTS)으로 들어온 요청만 받는다.

    공개 사이트 도메인으로 /api/admin/*을 호출하면 없는 경로와 똑같이 404를 준다(로그인 포함).
    Host는 CloudFront가 정한 값이 그대로 전달돼야 한다(13단계: /api/*에 Host 전달 설정).
    """
    if not settings.admin_hosts:
        return
    host = request.headers.get("host", "").strip().lower().split(":")[0]
    if host not in settings.admin_hosts:
        raise NotFound()


@dataclass(frozen=True)
class AdminContext:
    user: AdminUser
    session: sessions.SessionData


def require_admin(request: Request, db: DbSession, settings: AppSettings) -> AdminContext:
    """관리자 세션 확인 + 변경 요청이면 CSRF 헤더 확인. /api/admin 보호 라우터 전체에 걸린다."""
    token = request.cookies.get(sessions.SESSION_COOKIE)
    data = sessions.decode(settings, token) if token else None
    if data is None:
        raise AuthenticationRequired()
    user = auth_service.get_active_admin(db, data.admin_id, data.version)
    if user is None:
        raise AuthenticationRequired("세션이 만료되었습니다. 다시 로그인하세요")

    if request.method not in SAFE_METHODS:
        sent = request.headers.get(sessions.CSRF_HEADER, "")
        if not sent or not hmac.compare_digest(sent, data.csrf_token):
            raise Forbidden("CSRF 토큰이 없거나 올바르지 않습니다")

    ctx = AdminContext(user=user, session=data)
    request.state.admin = ctx
    return ctx


def current_admin(request: Request) -> AdminContext:
    """보호 라우터 안에서 require_admin이 저장한 컨텍스트를 꺼낸다(중복 조회 없음)."""
    ctx: AdminContext = request.state.admin
    return ctx


Admin = Annotated[AdminContext, Depends(current_admin)]


UNKNOWN_IP = "0.0.0.0"  # noqa: S104  # 바인딩 주소가 아니라 "알 수 없음" 표시값


def client_ip(request: Request) -> str:
    """요청자 IP. 형식이 IP가 아니면(테스트 클라이언트 등) UNKNOWN_IP.

    프록시(Caddy·CloudFront) 뒤의 실제 IP는 uvicorn --proxy-headers 설정으로
    request.client에 반영한다(14단계).
    """
    host = request.client.host if request.client else ""
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        return UNKNOWN_IP
