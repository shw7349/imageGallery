"""방문자 식별 쿠키(vid). 로그인 없는 방문자의 조회수 중복 제거·다운로드 제한에 쓴다."""

import uuid
from dataclasses import dataclass

from fastapi import Request, Response

from app.config import Settings
from app.services.views import visitor_hash

VISITOR_COOKIE = "vid"
VISITOR_MAX_AGE = 365 * 24 * 3600


@dataclass(frozen=True)
class Visitor:
    vid: str
    hash: str
    is_new: bool


def _valid(value: str | None) -> bool:
    if not value:
        return False
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False


def get_visitor(request: Request, settings: Settings) -> Visitor:
    """쿠키가 있고 형식이 맞으면 그것, 아니면 새 UUID(응답에 set_visitor_cookie로 심는다)."""
    vid = request.cookies.get(VISITOR_COOKIE)
    is_new = not _valid(vid)
    if is_new or vid is None:
        vid = str(uuid.uuid4())
    return Visitor(vid=vid, hash=visitor_hash(settings.session_secret.get_secret_value(), vid), is_new=is_new)


def set_visitor_cookie(response: Response, visitor: Visitor) -> None:
    if visitor.is_new:
        response.set_cookie(
            VISITOR_COOKIE,
            visitor.vid,
            max_age=VISITOR_MAX_AGE,
            httponly=True,
            secure=True,
            samesite="lax",
            path="/api",
        )
