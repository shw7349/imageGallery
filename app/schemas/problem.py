from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Problem(BaseModel):
    """RFC 7807 오류 응답. 모든 오류는 `application/problem+json` 이 형식으로 온다."""

    model_config = ConfigDict(extra="allow")

    type: str = Field(examples=["https://gallery.example/problems/not_found"])
    title: str = Field(examples=["대상을 찾을 수 없습니다"])
    status: int = Field(examples=[404])
    code: str = Field(description="기계가 읽는 오류 식별자", examples=["not_found"])
    detail: str | None = Field(default=None, description="사람이 읽는 추가 설명")
    instance: str | None = Field(default=None, description="요청 경로", examples=["/api/admin/posts/42"])
    errors: list[dict[str, Any]] | None = Field(  # Any: 검증 오류 항목(loc/msg/type)
        default=None, description="422 검증 오류일 때 필드별 오류 목록"
    )
