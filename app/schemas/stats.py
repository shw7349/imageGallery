from datetime import date
from enum import IntEnum

from pydantic import Field

from app.schemas.base import CamelModel


class StatsPeriod(IntEnum):
    """통계 기간(최근 며칠)."""

    WEEK = 7
    MONTH = 30
    QUARTER = 90


class DailyViews(CamelModel):
    date: date
    views: int = Field(examples=[37])


class ViewsStatsOut(CamelModel):
    from_date: date = Field(alias="from")
    to_date: date = Field(alias="to")
    total: int = Field(examples=[512])
    days: list[DailyViews] = Field(description="from~to 모든 날짜(조회 0인 날 포함), 날짜 오름차순")


class TopPost(CamelModel):
    post_id: int = Field(examples=[1])
    title: str = Field(examples=["제주 오름 일출"])
    views: int = Field(description="기간 내 조회수", examples=[120])


class TopPostsOut(CamelModel):
    days: int = Field(examples=[7])
    items: list[TopPost] = Field(description="기간 내 조회수 상위 20개")


class PhotoDownloads(CamelModel):
    photo_id: int = Field(examples=[10])
    post_id: int = Field(examples=[1])
    post_title: str = Field(examples=["제주 오름 일출"])
    original_filename: str = Field(examples=["DSC01234.ARW"])
    downloads: int = Field(examples=[5])


class DownloadsStatsOut(CamelModel):
    days: int = Field(examples=[30])
    items: list[PhotoDownloads] = Field(description="기간 내 원본 다운로드 상위 50개 사진")
