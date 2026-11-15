"""관리자 사진 업로드(S3 멀티파트) 스키마. 절차는 docs/api.md "멀티파트 업로드" 참고."""

from datetime import datetime
from typing import Annotated

from pydantic import Field, StringConstraints, field_validator

from app.schemas.base import CamelModel

Filename = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
ETag = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
PartNumber = Annotated[int, Field(ge=1, le=10_000)]

ALLOWED_EXTENSIONS = (
    "jpg", "jpeg", "tif", "tiff", "png",
    "arw", "cr2", "cr3", "nef", "raf", "orf", "rw2", "dng",
)  # fmt: skip


class UploadStartIn(CamelModel):
    filename: Filename = Field(
        description=f"원본 파일명. 확장자 허용: {', '.join(ALLOWED_EXTENSIONS)}", examples=["DSC01234.ARW"]
    )
    size_bytes: int = Field(gt=0, description="파일 크기(바이트). 최대 1GB(서버 설정)", examples=[314572800])
    content_type: str = Field(
        min_length=1,
        max_length=100,
        description="브라우저가 알려준 MIME 타입. 모르면 application/octet-stream",
        examples=["image/x-sony-arw"],
    )


class UploadStartOut(CamelModel):
    photo_id: int = Field(examples=[10])
    upload_id: str = Field(description="S3 멀티파트 업로드 ID(참고용)", examples=["2~abcDEF..."])
    part_size: int = Field(description="마지막 파트를 제외한 각 파트 크기(바이트)", examples=[16777216])
    part_count: int = Field(description="전체 파트 수 = ceil(sizeBytes / partSize)", examples=[19])


class PartUrlsIn(CamelModel):
    part_numbers: list[PartNumber] = Field(
        min_length=1,
        max_length=100,
        description="URL을 받을 파트 번호(1부터). 한 번에 최대 100개",
        examples=[[1, 2, 3, 4]],
    )

    @field_validator("part_numbers")
    @classmethod
    def _unique(cls, value: list[int]) -> list[int]:
        if len(set(value)) != len(value):
            raise ValueError("파트 번호가 중복되었습니다")
        return value


class PartUrl(CamelModel):
    part_number: int = Field(examples=[1])
    url: str = Field(
        description="이 URL로 파트 바이트를 그대로 PUT한다",
        examples=["https://s3.../original.arw?partNumber=1&uploadId=...&X-Amz-Signature=..."],
    )
    expires_at: datetime = Field(description="URL 만료 시각(발급 후 1시간)")


class PartUrlsOut(CamelModel):
    urls: list[PartUrl]


class UploadedPart(CamelModel):
    part_number: int = Field(examples=[1])
    etag: str = Field(
        description="S3가 돌려준 ETag(따옴표 포함 그대로)", examples=['"9b2cf535f27731c974343645a3985328"']
    )
    size_bytes: int = Field(examples=[16777216])


class UploadedPartsOut(CamelModel):
    part_size: int = Field(examples=[16777216])
    part_count: int = Field(examples=[19])
    parts: list[UploadedPart] = Field(description="이미 S3에 올라간 파트(파트 번호 오름차순)")


class CompletePart(CamelModel):
    part_number: PartNumber
    etag: ETag = Field(examples=['"9b2cf535f27731c974343645a3985328"'])


class CompleteIn(CamelModel):
    parts: list[CompletePart] = Field(
        min_length=1, max_length=10_000, description="모든 파트의 번호와 ETag(1..partCount 전부)"
    )
