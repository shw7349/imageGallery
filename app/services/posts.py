"""게시물 관리 (관리자)."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import ColumnElement, Select, func, select
from sqlalchemy.orm import Session, selectinload

from app.db import transaction
from app.errors import Conflict, NotFound, ValidationFailed
from app.models import Category, Photo, PhotoStatus, Post, PostStatus
from app.schemas.posts import PostSort
from app.services import uploads
from app.storage.s3 import S3Storage


@dataclass(frozen=True)
class PostRow:
    post: Post
    category_name: str
    photo_count: int


@dataclass(frozen=True)
class PostFields:
    category_id: int
    title: str
    body: str


_ORDER: dict[PostSort, tuple[ColumnElement[Any], ...]] = {  # Any: 정렬 컬럼 타입이 섞여 있음
    PostSort.CREATED: (Post.created_at.desc(), Post.id.desc()),
    PostSort.PUBLISHED: (Post.published_at.desc().nulls_last(), Post.id.desc()),
    PostSort.VIEWS: (Post.view_count.desc(), Post.id.desc()),
}


def _photo_count() -> Select[int, int]:
    return select(Photo.post_id, func.count().label("n")).group_by(Photo.post_id)


def list_posts(
    session: Session,
    *,
    page: int,
    size: int,
    status: PostStatus | None = None,
    category_id: int | None = None,
    sort: PostSort = PostSort.CREATED,
) -> tuple[list[PostRow], int]:
    filters = []
    if status is not None:
        filters.append(Post.status == status)
    if category_id is not None:
        filters.append(Post.category_id == category_id)

    total = session.scalar(select(func.count()).select_from(Post).where(*filters)) or 0
    counts = _photo_count().subquery()
    rows = session.execute(
        select(Post, Category.name, func.coalesce(counts.c.n, 0))
        .join(Category, Category.id == Post.category_id)
        .outerjoin(counts, counts.c.post_id == Post.id)
        .where(*filters)
        .order_by(*_ORDER[sort])
        .limit(size)
        .offset((page - 1) * size)
    ).all()
    return [PostRow(post=p, category_name=name, photo_count=int(n)) for p, name, n in rows], total


def _load(session: Session, post_id: int, *, for_update: bool = False) -> Post:
    query = select(Post).where(Post.id == post_id).options(selectinload(Post.photos))
    if for_update:
        query = query.with_for_update()
    post = session.scalar(query)
    if post is None:
        raise NotFound(f"게시물 {post_id}를 찾을 수 없습니다")
    return post


def _row(session: Session, post: Post) -> PostRow:
    name = session.scalar(select(Category.name).where(Category.id == post.category_id))
    return PostRow(post=post, category_name=name or "", photo_count=len(post.photos))


def get_post(session: Session, post_id: int) -> PostRow:
    return _row(session, _load(session, post_id))


def _ensure_category(session: Session, category_id: int) -> None:
    if session.get(Category, category_id) is None:
        raise ValidationFailed(
            f"존재하지 않는 카테고리입니다: {category_id}",
            errors=[{"loc": ["body", "categoryId"], "msg": "존재하지 않는 카테고리", "type": "not_found"}],
        )


def create_post(session: Session, fields: PostFields) -> PostRow:
    with transaction(session):
        _ensure_category(session, fields.category_id)
        post = Post(
            category_id=fields.category_id,
            title=fields.title,
            body=fields.body,
            status=PostStatus.DRAFT,
        )
        session.add(post)
        session.flush()
        post_id = post.id
    return get_post(session, post_id)


def update_post(session: Session, post_id: int, fields: PostFields) -> PostRow:
    with transaction(session):
        post = _load(session, post_id, for_update=True)
        _ensure_category(session, fields.category_id)
        post.category_id = fields.category_id
        post.title = fields.title
        post.body = fields.body
    session.expire_all()
    return get_post(session, post_id)


def change_status(session: Session, post_id: int, status: PostStatus) -> PostRow:
    """PUBLISHED 전환은 READY 사진이 있어야 한다. published_at은 처음 공개할 때만 기록한다(재공개 시 유지)."""
    with transaction(session):
        post = _load(session, post_id, for_update=True)
        if status is PostStatus.PUBLISHED and post.status is not PostStatus.PUBLISHED:
            ready = [p for p in post.photos if p.status is PhotoStatus.READY]
            if not ready:
                raise Conflict("처리가 끝난(READY) 사진이 1장 이상 있어야 공개할 수 있습니다")
            if post.published_at is None:
                post.published_at = datetime.now(UTC)
            if post.cover_photo_id is None:
                post.cover_photo_id = ready[0].id
        post.status = status
    session.expire_all()
    return get_post(session, post_id)


def reorder_photos(session: Session, post_id: int, photo_ids: list[int]) -> PostRow:
    """게시물의 사진 ID 전체를 받아 순서를 저장하고 첫 사진을 대표로 지정한다."""
    with transaction(session):
        post = _load(session, post_id, for_update=True)
        current = {p.id: p for p in post.photos}
        if set(photo_ids) != set(current):
            missing = sorted(set(current) - set(photo_ids))
            unknown = sorted(set(photo_ids) - set(current))
            raise ValidationFailed(
                "게시물의 사진 ID 전체를 정확히 보내야 합니다",
                missing_photo_ids=missing,
                unknown_photo_ids=unknown,
            )
        for index, photo_id in enumerate(photo_ids):
            current[photo_id].sort_order = index
        post.cover_photo_id = photo_ids[0]
    session.expire_all()
    return get_post(session, post_id)


def delete_post(session: Session, storage: S3Storage, post_id: int) -> None:
    """사진 S3 데이터(원본·파생물·진행 중 업로드)를 먼저 지우고 DB에서 게시물·사진을 지운다(cascade).

    S3 삭제가 중간에 실패하면 DB는 롤백되어 게시물이 남으므로 다시 삭제하면 된다(S3 삭제는 멱등).
    """
    with transaction(session):
        post = _load(session, post_id, for_update=True)
        for photo in post.photos:
            uploads.delete_photo_files(storage, photo)
        session.delete(post)
