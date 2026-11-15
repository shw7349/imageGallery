from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from app.api.deps import DbSession
from app.api.errors import problem_responses
from app.models import PostStatus
from app.schemas.pagination import MAX_PAGE_SIZE, Page
from app.schemas.posts import (
    AdminPhotoOut,
    AdminPostDetail,
    AdminPostSummary,
    PhotoOrderIn,
    PostIn,
    PostSort,
    PostStatusIn,
)
from app.services import posts as service

router = APIRouter(prefix="/posts", tags=["admin-posts"])


def _summary(row: service.PostRow) -> AdminPostSummary:
    return AdminPostSummary.model_validate(
        {**_post_fields(row), "photo_count": row.photo_count, "category_name": row.category_name}
    )


def _detail(row: service.PostRow) -> AdminPostDetail:
    return AdminPostDetail.model_validate(
        {
            **_post_fields(row),
            "photo_count": row.photo_count,
            "category_name": row.category_name,
            "body": row.post.body,
            "photos": [AdminPhotoOut.model_validate(p) for p in row.post.photos],
        }
    )


def _post_fields(row: service.PostRow) -> dict[str, object]:
    p = row.post
    return {
        "id": p.id,
        "category_id": p.category_id,
        "title": p.title,
        "status": p.status,
        "allow_original_download": p.allow_original_download,
        "cover_photo_id": p.cover_photo_id,
        "view_count": p.view_count,
        "published_at": p.published_at,
        "created_at": p.created_at,
        "updated_at": p.updated_at,
    }


def _fields(body: PostIn) -> service.PostFields:
    return service.PostFields(
        category_id=body.category_id,
        title=body.title,
        body=body.body,
        allow_original_download=body.allow_original_download,
    )


@router.get(
    "",
    summary="게시물 목록 (모든 상태)",
    response_model=Page[AdminPostSummary],
    responses=problem_responses(401, 422),
)
def list_posts(
    db: DbSession,
    page: Annotated[int, Query(ge=1, le=100_000)] = 1,
    size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 20,
    status_filter: Annotated[PostStatus | None, Query(alias="status")] = None,
    category_id: Annotated[int | None, Query(alias="categoryId", ge=1)] = None,
    sort: PostSort = PostSort.CREATED,
) -> Page[AdminPostSummary]:
    rows, total = service.list_posts(
        db, page=page, size=size, status=status_filter, category_id=category_id, sort=sort
    )
    return Page(items=[_summary(r) for r in rows], page=page, size=size, total=total)


@router.post(
    "",
    summary="게시물 생성 (DRAFT)",
    response_model=AdminPostDetail,
    status_code=status.HTTP_201_CREATED,
    responses=problem_responses(401, 403, 422),
)
def create_post(body: PostIn, db: DbSession) -> AdminPostDetail:
    return _detail(service.create_post(db, _fields(body)))


@router.get(
    "/{post_id}",
    summary="게시물 상세 (사진 포함)",
    response_model=AdminPostDetail,
    responses=problem_responses(401, 404),
)
def get_post(post_id: int, db: DbSession) -> AdminPostDetail:
    return _detail(service.get_post(db, post_id))


@router.put(
    "/{post_id}",
    summary="게시물 수정 (전체 교체)",
    response_model=AdminPostDetail,
    responses=problem_responses(401, 403, 404, 422),
)
def update_post(post_id: int, body: PostIn, db: DbSession) -> AdminPostDetail:
    return _detail(service.update_post(db, post_id, _fields(body)))


@router.delete(
    "/{post_id}",
    summary="게시물 삭제 (사진 포함)",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=problem_responses(401, 403, 404),
)
def delete_post(post_id: int, db: DbSession) -> Response:
    service.delete_post(db, post_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch(
    "/{post_id}/status",
    summary="공개 상태 변경",
    response_model=AdminPostDetail,
    responses=problem_responses(401, 403, 404, 409, 422),
)
def change_status(post_id: int, body: PostStatusIn, db: DbSession) -> AdminPostDetail:
    return _detail(service.change_status(db, post_id, body.status))


@router.put(
    "/{post_id}/photos/order",
    summary="사진 순서 변경 (첫 사진이 대표)",
    response_model=AdminPostDetail,
    responses=problem_responses(401, 403, 404, 422),
)
def reorder_photos(post_id: int, body: PhotoOrderIn, db: DbSession) -> AdminPostDetail:
    return _detail(service.reorder_photos(db, post_id, body.photo_ids))
