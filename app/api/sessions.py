"""관리자 세션 쿠키: itsdangerous로 서명한 (관리자 id, 세션 버전, CSRF 토큰).

- HttpOnly · Secure · SameSite=Lax, 만료 `session_max_age_seconds`(기본 12시간).
- 로그인할 때마다 새 CSRF 토큰으로 재발급한다(세션 고정 방지).
- 서버 저장소가 없으므로 무효화는 admin_user.session_version 증가로 한다.
"""

import secrets
from dataclasses import dataclass

from fastapi import Response
from itsdangerous import BadSignature, URLSafeTimedSerializer

from app.config import Settings

SESSION_COOKIE = "gallery_admin"
CSRF_HEADER = "X-CSRF-Token"
_SALT = "admin-session-v1"


@dataclass(frozen=True)
class SessionData:
    admin_id: int
    version: int
    csrf_token: str


def serializer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.session_secret.get_secret_value(), salt=_SALT)


def new_session(admin_id: int, version: int) -> SessionData:
    return SessionData(admin_id=admin_id, version=version, csrf_token=secrets.token_urlsafe(32))


def encode(settings: Settings, data: SessionData) -> str:
    return serializer(settings).dumps({"uid": data.admin_id, "ver": data.version, "csrf": data.csrf_token})


def decode(settings: Settings, token: str) -> SessionData | None:
    """서명·만료가 유효하면 세션을, 아니면 None을 반환한다."""
    try:
        raw = serializer(settings).loads(token, max_age=settings.session_max_age_seconds)
    except BadSignature:  # SignatureExpired 포함
        return None
    if not isinstance(raw, dict):
        return None
    uid, ver, csrf = raw.get("uid"), raw.get("ver"), raw.get("csrf")
    if not isinstance(uid, int) or not isinstance(ver, int) or not isinstance(csrf, str):
        return None
    return SessionData(admin_id=uid, version=ver, csrf_token=csrf)


def set_cookie(response: Response, settings: Settings, data: SessionData) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        encode(settings, data),
        max_age=settings.session_max_age_seconds,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/api/admin",
    )


def clear_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/api/admin", httponly=True, secure=True, samesite="lax")
