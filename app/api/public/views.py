from fastapi import APIRouter, Response, status

from app.api.errors import problem_responses
from app.errors import NotImplementedYet

router = APIRouter()


@router.post(
    "/posts/{post_id}/views",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="조회수 기록",
    description="상세 화면을 열 때 1회 호출한다. 방문자(vid 쿠키)당 하루 1회만 집계되며, "
    "중복 여부와 관계없이 항상 204를 돌려준다.",
    responses=problem_responses(404, 429, 501),
)
def record_view(post_id: int) -> Response:
    raise NotImplementedYet("10단계에서 구현")
