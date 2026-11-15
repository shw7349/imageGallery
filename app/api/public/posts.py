from typing import Annotated

from fastapi import APIRouter, Query

from app.api.errors import problem_responses
from app.errors import NotImplementedYet
from app.schemas.pagination import MAX_PAGE_SIZE, Page
from app.schemas.public import PublicPostDetail, PublicPostSummary, PublicSort

router = APIRouter()


@router.get(
    "/posts",
    response_model=Page[PublicPostSummary],
    summary="공개 게시물 목록",
    description="공개(PUBLISHED) 상태이고 READY 사진이 1장 이상인 게시물만.",
    responses=problem_responses(422, 501),
)
def list_posts(
    category: Annotated[int | None, Query(ge=1, description="카테고리 ID")] = None,
    q: Annotated[str | None, Query(min_length=1, max_length=100, description="제목·본문 검색어")] = None,
    page: Annotated[int, Query(ge=1, le=100_000)] = 1,
    size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 20,
    sort: PublicSort = PublicSort.PUBLISHED,
) -> Page[PublicPostSummary]:
    raise NotImplementedYet("09단계에서 구현")


@router.get(
    "/posts/{post_id}",
    response_model=PublicPostDetail,
    summary="공개 게시물 상세",
    description="비공개·없는 게시물은 똑같이 404.",
    responses=problem_responses(404, 501),
)
def get_post(post_id: int) -> PublicPostDetail:
    raise NotImplementedYet("09단계에서 구현")
