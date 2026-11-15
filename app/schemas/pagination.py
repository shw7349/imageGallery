from pydantic import Field

from app.schemas.base import CamelModel

MAX_PAGE_SIZE = 100


class Page[T](CamelModel):
    """목록 응답 공통 형식. page는 1부터 시작한다."""

    items: list[T]
    page: int = Field(ge=1, examples=[1])
    size: int = Field(ge=1, le=MAX_PAGE_SIZE, examples=[20])
    total: int = Field(ge=0, description="필터 조건에 맞는 전체 개수", examples=[42])
