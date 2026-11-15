from fastapi import APIRouter, Response
from fastapi.responses import RedirectResponse

from app.api.deps import AppSettings
from app.api.errors import problem_responses
from app.api.mock import MOCK_HEADER, use_mock
from app.mock import data as mock

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
        **problem_responses(403, 404, 429, 501),
    },
)
def download_original(photo_id: int, settings: AppSettings, response: Response) -> Response:
    use_mock(settings, response, "11")
    url = mock.original_download_url(settings.cdn_base_url, photo_id)
    return RedirectResponse(url, status_code=302, headers={MOCK_HEADER: "true"})
