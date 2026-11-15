"""방문자용 공개 API 스키마. 이미지 URL은 전부 CDN 기준 절대 URL이다(docs/api.md "이미지 URL")."""

from datetime import datetime
from enum import StrEnum

from pydantic import Field

from app.schemas.base import CamelModel


class PublicSort(StrEnum):
    PUBLISHED = "published"
    VIEWS = "views"


class PublicCategory(CamelModel):
    id: int = Field(examples=[1])
    name: str = Field(examples=["풍경"])


class Exif(CamelModel):
    """촬영 정보. 원본에 없으면 null. 위치(GPS) 정보는 제공하지 않는다."""

    camera_make: str | None = Field(default=None, examples=["SONY"])
    camera_model: str | None = Field(default=None, examples=["ILCE-7RM5"])
    lens: str | None = Field(default=None, examples=["FE 24-70mm F2.8 GM II"])
    f_number: float | None = Field(default=None, description="조리개 값", examples=[8.0])
    exposure_time: str | None = Field(default=None, description="셔터 속도", examples=["1/250"])
    iso: int | None = Field(default=None, examples=[100])
    focal_length_mm: float | None = Field(default=None, examples=[35.0])
    taken_at: datetime | None = Field(default=None, description="촬영 시각(카메라 시계 기준)")


class PublicPostSummary(CamelModel):
    id: int = Field(examples=[1])
    title: str = Field(examples=["제주 오름 일출"])
    category_id: int = Field(examples=[1])
    category_name: str = Field(examples=["풍경"])
    thumb_url: str | None = Field(
        description="대표 사진 썸네일(긴 변 600px WebP)",
        examples=["https://cdn.example.com/derived/1/10/thumb.webp"],
    )
    photo_count: int = Field(description="공개된(READY) 사진 수", examples=[3])
    view_count: int = Field(examples=[128])
    published_at: datetime


class PublicPhoto(CamelModel):
    id: int = Field(examples=[10])
    width: int = Field(description="원본 픽셀 너비(회전 반영)", examples=[9504])
    height: int = Field(examples=[6336])
    thumb_url: str = Field(examples=["https://cdn.example.com/derived/1/10/thumb.webp"])
    preview_url: str = Field(
        description="긴 변 3840px WebP", examples=["https://cdn.example.com/derived/1/10/preview.webp"]
    )
    dzi_url: str = Field(
        description="딥줌(OpenSeadragon) DZI 파일. 타일은 같은 위치의 photo_files/ 아래",
        examples=["https://cdn.example.com/derived/1/10/photo.dzi"],
    )
    exif: Exif
    original_download_url: str | None = Field(
        description="원본 다운로드 API 경로. 게시물이 다운로드를 허용하지 않으면 null",
        examples=["/api/photos/10/original"],
    )


class PublicPostDetail(CamelModel):
    id: int = Field(examples=[1])
    title: str = Field(examples=["제주 오름 일출"])
    body: str = Field(examples=["새벽 5시, 새별오름에서."])
    category_id: int = Field(examples=[1])
    category_name: str = Field(examples=["풍경"])
    view_count: int = Field(examples=[128])
    published_at: datetime
    allow_original_download: bool
    photos: list[PublicPhoto] = Field(description="READY 사진만, 관리자가 정한 순서")
