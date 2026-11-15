from fastapi import APIRouter, Request, Response
from fastapi.responses import RedirectResponse

from app.api.deps import AppSettings, DbSession, Storage
from app.api.errors import problem_responses
from app.api.visitor import get_visitor, set_visitor_cookie
from app.services import downloads as service

router = APIRouter()


@router.get(
    "/photos/{photo_id}/original",
    status_code=302,
    response_class=Response,
    summary="원본 다운로드",
    description="게시물이 원본 다운로드를 허용하면 10분짜리 S3 URL로 302 리다이렉트한다. "
    "브라우저에서 <a href>로 열면 원래 파일명으로 저장된다.",
    responses={
        302: {
            "description": "Location 헤더의 URL로 이동",
            "headers": {"Location": {"schema": {"type": "string"}}},
        },
        **problem_responses(403, 404, 429, 503),
    },
)
def download_original(
    photo_id: int, request: Request, db: DbSession, storage: Storage, settings: AppSettings
) -> Response:
    visitor = get_visitor(request, settings)
    url = service.original_url(db, storage, photo_id, visitor.hash, settings.downloads_per_hour)
    # 서명된 URL은 캐시되면 안 된다(만료·방문자별 기록)
    response = RedirectResponse(url, status_code=302, headers={"Cache-Control": "no-store"})
    set_visitor_cookie(response, visitor)
    return response
