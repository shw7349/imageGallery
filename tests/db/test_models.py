"""모델 기본 CRUD와 DB 제약(실제 Postgres)."""

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Category, JobStatus, Photo, PhotoJob, PhotoStatus, Post, PostStatus


def _post(db: Session, title: str = "제목") -> Post:
    cat = Category(name=f"cat-{title}")
    db.add(cat)
    db.flush()
    post = Post(category_id=cat.id, title=title)
    db.add(post)
    db.flush()
    return post


def _photo(db: Session, post: Post, order: int = 0) -> Photo:
    photo = Photo(
        post_id=post.id,
        sort_order=order,
        original_key=f"originals/{post.id}/x/original.jpg",
        original_filename="사진.jpg",
        size_bytes=300 * 1024 * 1024,
        content_type="image/jpeg",
    )
    db.add(photo)
    db.flush()
    return photo


def test_post_defaults(db: Session) -> None:
    post = _post(db)
    db.refresh(post)
    assert post.status is PostStatus.DRAFT
    assert post.view_count == 0
    assert post.allow_original_download is False
    assert post.created_at.tzinfo is not None
    assert post.published_at is None


def test_crud_roundtrip(db: Session) -> None:
    post = _post(db)
    _photo(db, post, 1)
    _photo(db, post, 0)
    db.expire_all()

    loaded = db.get(Post, post.id)
    assert loaded is not None
    assert [p.sort_order for p in loaded.photos] == [0, 1]
    assert all(p.status is PhotoStatus.UPLOADING for p in loaded.photos)

    loaded.title = "수정"
    db.flush()
    assert db.scalar(select(Post.title).where(Post.id == post.id)) == "수정"


def test_delete_post_cascades_photos_and_jobs(db: Session) -> None:
    post = _post(db)
    photo = _photo(db, post)
    db.add(PhotoJob(photo_id=photo.id))
    db.flush()

    db.execute(text("DELETE FROM post WHERE id = :id"), {"id": post.id})
    assert db.scalar(text("SELECT count(*) FROM photo WHERE post_id = :id"), {"id": post.id}) == 0
    assert db.scalar(text("SELECT count(*) FROM photo_job WHERE photo_id = :id"), {"id": photo.id}) == 0


def test_cover_photo_set_null_when_photo_deleted(db: Session) -> None:
    post = _post(db)
    photo = _photo(db, post)
    post.cover_photo_id = photo.id
    db.flush()

    db.execute(text("DELETE FROM photo WHERE id = :id"), {"id": photo.id})
    assert db.scalar(text("SELECT cover_photo_id FROM post WHERE id = :id"), {"id": post.id}) is None


def test_category_with_posts_cannot_be_deleted(db: Session) -> None:
    post = _post(db)
    with pytest.raises(IntegrityError):
        db.execute(text("DELETE FROM category WHERE id = :id"), {"id": post.category_id})


def test_category_name_unique(db: Session) -> None:
    db.add(Category(name="풍경"))
    db.flush()
    db.add(Category(name="풍경"))
    with pytest.raises(IntegrityError):
        db.flush()


def test_status_check_constraint_rejects_unknown_value(db: Session) -> None:
    post = _post(db)
    with pytest.raises(IntegrityError):
        db.execute(text("UPDATE post SET status = 'BOGUS' WHERE id = :id"), {"id": post.id})


def test_only_one_active_job_per_photo(db: Session) -> None:
    photo = _photo(db, _post(db))
    db.add(PhotoJob(photo_id=photo.id, status=JobStatus.DONE))
    db.add(PhotoJob(photo_id=photo.id, status=JobStatus.DONE))
    db.add(PhotoJob(photo_id=photo.id, status=JobStatus.PENDING))
    db.flush()  # 완료된 작업은 여러 개여도 된다

    db.add(PhotoJob(photo_id=photo.id, status=JobStatus.RUNNING))
    with pytest.raises(IntegrityError):
        db.flush()


def test_size_bytes_must_be_positive(db: Session) -> None:
    post = _post(db)
    db.add(
        Photo(
            post_id=post.id,
            original_key="k",
            original_filename="f",
            size_bytes=0,
            content_type="image/jpeg",
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()
