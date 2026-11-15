from fastapi import APIRouter, Response

from app.api.deps import DbSession
from app.api.errors import problem_responses
from app.api.public.cache import PUBLIC_CACHE
from app.schemas.public import PublicCategory
from app.services import public as service

router = APIRouter()


@router.get(
    "/categories",
    response_model=list[PublicCategory],
    summary="카테고리 목록",
    responses=problem_responses(),
)
def list_categories(db: DbSession, response: Response) -> list[PublicCategory]:
    response.headers["Cache-Control"] = PUBLIC_CACHE
    return [PublicCategory.model_validate(c) for c in service.list_categories(db)]
