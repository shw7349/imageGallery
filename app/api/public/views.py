from fastapi import APIRouter, Request, Response, status

from app.api.deps import AppSettings, DbSession, client_ip
from app.api.errors import problem_responses
from app.api.ratelimit import RateLimiter
from app.api.visitor import get_visitor, set_visitor_cookie
from app.errors import TooManyRequests
from app.services import public
from app.services import views as service

router = APIRouter()


@router.post(
    "/posts/{post_id}/views",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="조회수 기록",
    description="상세 화면을 열 때 1회 호출한다. 방문자(vid 쿠키)당 하루 1회만 집계되며, "
    "중복 여부와 관계없이 항상 204를 돌려준다.",
    responses=problem_responses(404, 429),
)
def record_view(post_id: int, request: Request, db: DbSession, settings: AppSettings) -> Response:
    limiter: RateLimiter = request.app.state.view_limiter
    retry_after = limiter.check(client_ip(request))
    if retry_after is not None:
        raise TooManyRequests(retry_after_seconds=retry_after)

    visitor = get_visitor(request, settings)
    if service.is_bot(request.headers.get("user-agent", "")):
        public.require_public_post(db, post_id)  # 봇은 세지 않는다(응답은 같다)
    else:
        service.record_view(db, post_id, visitor.hash, service.today_in(settings.stats_timezone))

    response = Response(status_code=status.HTTP_204_NO_CONTENT, headers={"Cache-Control": "no-store"})
    set_visitor_cookie(response, visitor)
    return response
