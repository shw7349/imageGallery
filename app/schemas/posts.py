from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import Field, StringConstraints, field_validator

from app.models import PhotoStatus, PostStatus
from app.schemas.base import CamelModel

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class PostSort(StrEnum):
    CREATED = "created"
    PUBLISHED = "published"
    VIEWS = "views"


class PostIn(CamelModel):
    """생성·수정 요청 (PUT은 전체 교체)."""

    category_id: int = Field(ge=1, examples=[1])
    title: Title = Field(examples=["제주 오름 일출"])
    body: str = Field(default="", max_length=20_000, examples=["새벽 5시, 새별오름에서."])


class PostStatusIn(CamelModel):
    status: PostStatus = Field(
        description="PUBLISHED 전환은 READY 사진이 1장 이상이어야 한다", examples=["PUBLISHED"]
    )


class PhotoOrderIn(CamelModel):
    photo_ids: list[int] = Field(
        min_length=1,
        max_length=1000,
        description="게시물의 사진 ID 전체를 원하는 순서로. 첫 사진이 대표(cover)가 된다",
        examples=[[12, 10, 11]],
    )

    @field_validator("photo_ids")
    @classmethod
    def _no_duplicates(cls, value: list[int]) -> list[int]:
        if len(set(value)) != len(value):
            raise ValueError("사진 ID가 중복되었습니다")
        return value


class AdminPhotoOut(CamelModel):
    id: int = Field(examples=[10])
    post_id: int = Field(examples=[1])
    sort_order: int = Field(examples=[0])
    status: PhotoStatus = Field(examples=["READY"])
    original_filename: str = Field(examples=["DSC01234.ARW"])
    size_bytes: int = Field(examples=[314572800])
    content_type: str = Field(examples=["image/x-sony-arw"])
    width: int | None = Field(examples=[9504])
    height: int | None = Field(examples=[6336])
    error_message: str | None = Field(description="FAILED일 때 원인", examples=[None])
    created_at: datetime


class AdminPostSummary(CamelModel):
    id: int = Field(examples=[1])
    category_id: int = Field(examples=[1])
    category_name: str = Field(examples=["풍경"])
    title: str = Field(examples=["제주 오름 일출"])
    status: PostStatus = Field(examples=["DRAFT"])
    cover_photo_id: int | None = Field(examples=[10])
    photo_count: int = Field(examples=[3])
    view_count: int = Field(examples=[0])
    published_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AdminPostDetail(AdminPostSummary):
    body: str = Field(examples=["새벽 5시, 새별오름에서."])
    photos: list[AdminPhotoOut]
