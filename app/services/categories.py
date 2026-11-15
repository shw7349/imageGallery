"""카테고리 관리."""

from sqlalchemy import exists, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import transaction
from app.errors import Conflict, NotFound
from app.models import Category, Post


def list_categories(session: Session) -> list[Category]:
    return list(session.scalars(select(Category).order_by(Category.sort_order, Category.id)))


def get_category(session: Session, category_id: int) -> Category:
    category = session.get(Category, category_id)
    if category is None:
        raise NotFound(f"카테고리 {category_id}를 찾을 수 없습니다")
    return category


def _ensure_name_free(session: Session, name: str, exclude_id: int | None = None) -> None:
    query = select(Category.id).where(Category.name == name)
    if exclude_id is not None:
        query = query.where(Category.id != exclude_id)
    if session.scalar(query) is not None:
        raise Conflict(f"같은 이름의 카테고리가 있습니다: {name}")


def _flush_or_conflict(session: Session, name: str) -> None:
    try:
        session.flush()
    except IntegrityError as exc:  # 동시 요청으로 유니크 제약에 걸린 경우
        raise Conflict(f"같은 이름의 카테고리가 있습니다: {name}") from exc


def create_category(session: Session, name: str, sort_order: int) -> Category:
    with transaction(session):
        _ensure_name_free(session, name)
        category = Category(name=name, sort_order=sort_order)
        session.add(category)
        _flush_or_conflict(session, name)
    return category


def update_category(session: Session, category_id: int, name: str, sort_order: int) -> Category:
    with transaction(session):
        category = get_category(session, category_id)
        _ensure_name_free(session, name, exclude_id=category_id)
        category.name = name
        category.sort_order = sort_order
        _flush_or_conflict(session, name)
    return category


def delete_category(session: Session, category_id: int) -> None:
    with transaction(session):
        category = get_category(session, category_id)
        if session.scalar(select(exists().where(Post.category_id == category_id))):
            raise Conflict("게시물이 있는 카테고리는 삭제할 수 없습니다. 게시물을 먼저 옮기거나 삭제하세요")
        session.delete(category)
