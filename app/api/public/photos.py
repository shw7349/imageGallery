from fastapi import APIRouter, Response

from app.api.errors import problem_responses
from app.errors import NotImplementedYet

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
def download_original(photo_id: int) -> Response:
    raise NotImplementedYet("11단계에서 구현")
