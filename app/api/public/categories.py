from fastapi import APIRouter, Response

from app.api.deps import AppSettings
from app.api.errors import problem_responses
from app.api.mock import use_mock
from app.mock import data as mock
from app.schemas.public import PublicCategory

router = APIRouter()


@router.get(
    "/categories",
    response_model=list[PublicCategory],
    summary="카테고리 목록",
    responses=problem_responses(501),
)
def list_categories(settings: AppSettings, response: Response) -> list[PublicCategory]:
    use_mock(settings, response, "09")
    return mock.categories()
