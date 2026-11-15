from fastapi import APIRouter, Response, status

from app.api.deps import DbSession
from app.api.errors import problem_responses
from app.schemas.categories import CategoryIn, CategoryOut
from app.services import categories as service

router = APIRouter(prefix="/categories", tags=["admin-posts"])


@router.get("", response_model=list[CategoryOut], responses=problem_responses(401))
def list_categories(db: DbSession) -> list[CategoryOut]:
    return [CategoryOut.model_validate(c) for c in service.list_categories(db)]


@router.post(
    "",
    response_model=CategoryOut,
    status_code=status.HTTP_201_CREATED,
    responses=problem_responses(401, 403, 409, 422),
)
def create_category(body: CategoryIn, db: DbSession) -> CategoryOut:
    return CategoryOut.model_validate(service.create_category(db, body.name, body.sort_order))


@router.put(
    "/{category_id}", response_model=CategoryOut, responses=problem_responses(401, 403, 404, 409, 422)
)
def update_category(category_id: int, body: CategoryIn, db: DbSession) -> CategoryOut:
    return CategoryOut.model_validate(service.update_category(db, category_id, body.name, body.sort_order))


@router.delete(
    "/{category_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=problem_responses(401, 403, 404, 409),
)
def delete_category(category_id: int, db: DbSession) -> Response:
    service.delete_category(db, category_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
