from fastapi import APIRouter

from app.api.errors import problem_responses
from app.errors import NotImplementedYet
from app.schemas.public import PublicCategory

router = APIRouter()


@router.get(
    "/categories",
    response_model=list[PublicCategory],
    summary="카테고리 목록",
    responses=problem_responses(501),
)
def list_categories() -> list[PublicCategory]:
    raise NotImplementedYet("09단계에서 구현")
