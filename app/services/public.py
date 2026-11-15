"""방문자용 공개 조회. 공개 조건: 게시물 PUBLISHED + READY 사진 1장 이상. 그 외는 없는 것과 같다(404)."""

from dataclasses import dataclass
from typing import Any

from sqlalchemy import ColumnElement, Select, and_, func, or_, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import Session, aliased

from app.errors import NotFound
from app.models import Category, Photo, PhotoStatus, Post, PostStatus
from app.schemas.public import PublicSort


@dataclass(frozen=True)
class PublicPostRow:
    post: Post
    category_name: str
    ready_count: int
    cover_thumb_key: str | None


@dataclass(frozen=True)
class PublicPostWithPhotos:
    post: Post
    category_name: str
    photos: list[Photo]


def list_categories(session: Session) -> list[Category]:
    return list(session.scalars(select(Category).order_by(Category.sort_order, Category.id)))


def _ready_counts() -> Any:  # Any: 서브쿼리 객체
    return (
        select(Photo.post_id, func.count().label("n"))
        .where(Photo.status == PhotoStatus.READY)
        .group_by(Photo.post_id)
        .subquery()
    )


def _cover_thumbs() -> Any:  # Any: 서브쿼리 객체
    """게시물별 대표 썸네일: 대표 사진이 READY면 그것, 아니면 순서상 첫 READY 사진."""
    p = aliased(Post)
    return (
        select(Photo.post_id, Photo.thumb_key)
        .join(p, p.id == Photo.post_id)
        .where(Photo.status == PhotoStatus.READY)
        .ext(distinct_on(Photo.post_id))
        .order_by(Photo.post_id, (Photo.id == p.cover_photo_id).desc(), Photo.sort_order, Photo.id)
        .subquery()
    )


def escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


_ORDER: dict[PublicSort, tuple[ColumnElement[Any], ...]] = {  # Any: 정렬 컬럼 타입 혼합
    PublicSort.PUBLISHED: (Post.published_at.desc(), Post.id.desc()),
    PublicSort.VIEWS: (Post.view_count.desc(), Post.id.desc()),
}


def list_posts(
    session: Session,
    *,
    category_id: int | None,
    q: str | None,
    page: int,
    size: int,
    sort: PublicSort,
) -> tuple[list[PublicPostRow], int]:
    counts = _ready_counts()
    thumbs = _cover_thumbs()
    filters: list[ColumnElement[bool]] = [Post.status == PostStatus.PUBLISHED, counts.c.n > 0]
    if category_id is not None:
        filters.append(Post.category_id == category_id)
    if q:
        pattern = f"%{escape_like(q.strip())}%"
        filters.append(or_(Post.title.ilike(pattern, escape="\\"), Post.body.ilike(pattern, escape="\\")))

    base: Select[Any] = select(Post.id).join(counts, counts.c.post_id == Post.id).where(and_(*filters))
    total = session.scalar(select(func.count()).select_from(base.subquery())) or 0

    rows = session.execute(
        select(Post, Category.name, counts.c.n, thumbs.c.thumb_key)
        .join(Category, Category.id == Post.category_id)
        .join(counts, counts.c.post_id == Post.id)
        .outerjoin(thumbs, thumbs.c.post_id == Post.id)
        .where(and_(*filters))
        .order_by(*_ORDER[sort])
        .limit(size)
        .offset((page - 1) * size)
    ).all()
    return [
        PublicPostRow(post=p, category_name=name, ready_count=int(n), cover_thumb_key=thumb)
        for p, name, n, thumb in rows
    ], total


def get_post(session: Session, post_id: int) -> PublicPostWithPhotos:
    row = session.execute(
        select(Post, Category.name)
        .join(Category, Category.id == Post.category_id)
        .where(Post.id == post_id, Post.status == PostStatus.PUBLISHED)
    ).first()
    if row is None:
        raise NotFound(f"게시물 {post_id}를 찾을 수 없습니다")
    post, name = row
    photos = list(
        session.scalars(
            select(Photo)
            .where(Photo.post_id == post_id, Photo.status == PhotoStatus.READY)
            .order_by(Photo.sort_order, Photo.id)
        )
    )
    if not photos:
        raise NotFound(f"게시물 {post_id}를 찾을 수 없습니다")
    return PublicPostWithPhotos(post=post, category_name=name, photos=photos)


def require_public_post(session: Session, post_id: int) -> None:
    """공개 게시물이 아니면 NotFound(조회수 기록 등에서 사용)."""
    exists = session.scalar(
        select(Post.id)
        .where(Post.id == post_id, Post.status == PostStatus.PUBLISHED)
        .where(select(Photo.id).where(Photo.post_id == Post.id, Photo.status == PhotoStatus.READY).exists())
    )
    if exists is None:
        raise NotFound(f"게시물 {post_id}를 찾을 수 없습니다")
