"""테스트 데이터 생성 헬퍼. 업로드 API(06단계) 없이 사진 레코드를 직접 만든다."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.models import Category, Photo, PhotoStatus, Post, PostStatus


def insert_photo(
    engine: Engine,
    post_id: int,
    *,
    status: PhotoStatus = PhotoStatus.READY,
    sort_order: int = 0,
    filename: str = "DSC0001.jpg",
) -> int:
    with Session(engine) as s:
        photo = Photo(
            post_id=post_id,
            sort_order=sort_order,
            original_key=f"originals/{post_id}/factory/original.jpg",
            original_filename=filename,
            size_bytes=1024,
            content_type="image/jpeg",
            status=status,
        )
        s.add(photo)
        s.flush()
        if status is PhotoStatus.READY:  # 처리 완료된 사진처럼 파생물 키·크기·EXIF를 채운다
            prefix = f"derived/{post_id}/{photo.id}/"
            photo.thumb_key = prefix + "thumb.webp"
            photo.preview_key = prefix + "preview.webp"
            photo.dzi_key = prefix + "photo.dzi"
            photo.width, photo.height = 6000, 4000
            photo.exif = {"camera_make": "SONY", "iso": 100, "taken_at": "2026-11-01T06:12:30+09:00"}
        s.commit()
        return photo.id


def insert_post(
    engine: Engine,
    category_id: int,
    *,
    title: str = "제목",
    body: str = "",
    status: PostStatus = PostStatus.PUBLISHED,
    views: int = 0,
    published_days_ago: int = 0,
) -> int:
    with Session(engine) as s:
        post = Post(
            category_id=category_id,
            title=title,
            body=body,
            status=status,
            view_count=views,
            published_at=datetime.now(UTC) - timedelta(days=published_days_ago)
            if status is not PostStatus.DRAFT
            else None,
        )
        s.add(post)
        s.commit()
        return post.id


def insert_category(engine: Engine, name: str, sort_order: int = 0) -> int:
    with Session(engine) as s:
        cat = Category(name=name, sort_order=sort_order)
        s.add(cat)
        s.commit()
        return cat.id
