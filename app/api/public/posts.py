from typing import Annotated

from fastapi import APIRouter, Query, Response

from app.api.deps import AppSettings, DbSession
from app.api.errors import problem_responses
from app.api.public.cache import PUBLIC_CACHE
from app.schemas.pagination import MAX_PAGE_SIZE, Page
from app.schemas.public import Exif, PublicPhoto, PublicPostDetail, PublicPostSummary, PublicSort
from app.services import public as service
from app.services.urls import cdn_url, original_download_path

router = APIRouter()


@router.get(
    "/posts",
    response_model=Page[PublicPostSummary],
    summary="공개 게시물 목록",
    description="공개(PUBLISHED) 상태이고 READY 사진이 1장 이상인 게시물만.",
    responses=problem_responses(422),
)
def list_posts(
    db: DbSession,
    settings: AppSettings,
    response: Response,
    category: Annotated[int | None, Query(ge=1, description="카테고리 ID")] = None,
    q: Annotated[str | None, Query(min_length=1, max_length=100, description="제목·본문 검색어")] = None,
    page: Annotated[int, Query(ge=1, le=100_000)] = 1,
    size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 20,
    sort: PublicSort = PublicSort.PUBLISHED,
) -> Page[PublicPostSummary]:
    rows, total = service.list_posts(db, category_id=category, q=q, page=page, size=size, sort=sort)
    response.headers["Cache-Control"] = PUBLIC_CACHE
    items = [
        PublicPostSummary(
            id=r.post.id,
            title=r.post.title,
            category_id=r.post.category_id,
            category_name=r.category_name,
            thumb_url=cdn_url(settings.cdn_base_url, r.cover_thumb_key) if r.cover_thumb_key else None,
            photo_count=r.ready_count,
            view_count=r.post.view_count,
            published_at=r.post.published_at or r.post.created_at,  # 공개 게시물은 항상 값이 있다
        )
        for r in rows
    ]
    return Page(items=items, page=page, size=size, total=total)


@router.get(
    "/posts/{post_id}",
    response_model=PublicPostDetail,
    summary="공개 게시물 상세",
    description="비공개·없는 게시물은 똑같이 404.",
    responses=problem_responses(404),
)
def get_post(post_id: int, db: DbSession, settings: AppSettings, response: Response) -> PublicPostDetail:
    found = service.get_post(db, post_id)
    post = found.post
    cdn = settings.cdn_base_url
    photos = [
        PublicPhoto(
            id=p.id,
            width=p.width or 0,
            height=p.height or 0,
            thumb_url=cdn_url(cdn, p.thumb_key or ""),
            preview_url=cdn_url(cdn, p.preview_key or ""),
            dzi_url=cdn_url(cdn, p.dzi_key or ""),
            exif=Exif.model_validate(p.exif or {}),
            original_download_url=original_download_path(p.id) if post.allow_original_download else None,
        )
        for p in found.photos
    ]
    response.headers["Cache-Control"] = PUBLIC_CACHE
    return PublicPostDetail(
        id=post.id,
        title=post.title,
        body=post.body,
        category_id=post.category_id,
        category_name=found.category_name,
        view_count=post.view_count,
        published_at=post.published_at or post.created_at,
        allow_original_download=post.allow_original_download,
        photos=photos,
    )
