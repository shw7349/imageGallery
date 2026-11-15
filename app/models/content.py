from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, str_enum, utc_timestamp
from app.models.enums import PhotoStatus, PostStatus


class Category(Base):
    __tablename__ = "category"

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(String(50), unique=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class Post(TimestampMixin, Base):
    __tablename__ = "post"
    __table_args__ = (
        Index(None, "category_id", "status"),
        CheckConstraint("view_count >= 0", name="view_count_non_negative"),
    )

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    # 게시물이 있는 카테고리는 삭제할 수 없다(04단계에서 409).
    category_id: Mapped[int] = mapped_column(ForeignKey("category.id", ondelete="RESTRICT"))
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, default="", server_default="")
    status: Mapped[PostStatus] = mapped_column(
        str_enum(PostStatus, "status"), default=PostStatus.DRAFT, server_default=PostStatus.DRAFT.value
    )
    allow_original_download: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # photo ↔ post 순환 참조라 use_alter로 테이블 생성 후 FK를 건다.
    cover_photo_id: Mapped[int | None] = mapped_column(
        ForeignKey("photo.id", ondelete="SET NULL", use_alter=True)
    )
    view_count: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    published_at: Mapped[datetime | None] = mapped_column(utc_timestamp())

    photos: Mapped[list["Photo"]] = relationship(
        back_populates="post",
        foreign_keys="Photo.post_id",
        order_by="Photo.sort_order",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


# 공개 목록(최신 공개순) 조회용. DESC 표현식으로 선언해야 Alembic 비교가 DB 반영 결과와 일치한다.
Index("ix_post_status_published_at", Post.status, Post.published_at.desc())


class Photo(TimestampMixin, Base):
    __tablename__ = "photo"
    __table_args__ = (
        Index(None, "post_id", "sort_order"),
        CheckConstraint("size_bytes > 0", name="size_bytes_positive"),
    )

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("post.id", ondelete="CASCADE"))
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    original_key: Mapped[str] = mapped_column(String(512))
    original_filename: Mapped[str] = mapped_column(String(255))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    content_type: Mapped[str] = mapped_column(String(100))
    s3_upload_id: Mapped[str | None] = mapped_column(String(1024))
    # 업로드 시작 시점의 파트 크기. 설정이 바뀌어도 진행 중 업로드의 파트 계산이 어긋나지 않게 저장한다.
    upload_part_size: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[PhotoStatus] = mapped_column(
        str_enum(PhotoStatus, "status"),
        default=PhotoStatus.UPLOADING,
        server_default=PhotoStatus.UPLOADING.value,
    )
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    thumb_key: Mapped[str | None] = mapped_column(String(512))
    preview_key: Mapped[str | None] = mapped_column(String(512))
    dzi_key: Mapped[str | None] = mapped_column(String(512))
    exif: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # Any: EXIF 값은 문자열·숫자 혼합
    error_message: Mapped[str | None] = mapped_column(Text)

    post: Mapped[Post] = relationship(back_populates="photos", foreign_keys=[post_id])
