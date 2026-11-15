"""관리자 통계 API. 계약은 05단계, 구현은 10·11단계(그 전까지 MOCK_API면 목업)."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query, Response

from app.api.deps import AppSettings
from app.api.errors import problem_responses
from app.api.mock import use_mock
from app.mock import data as mock
from app.schemas.stats import DownloadsStatsOut, StatsPeriod, TopPostsOut, ViewsStatsOut

router = APIRouter(prefix="/stats", tags=["admin-stats"])


@router.get(
    "/views",
    response_model=ViewsStatsOut,
    summary="일별 조회수",
    description="from~to(양 끝 포함, 최대 366일)의 일별 조회수 합계.",
    responses=problem_responses(401, 422, 501),
)
def views(
    settings: AppSettings,
    response: Response,
    from_date: Annotated[date, Query(alias="from", examples=["2026-11-01"])],
    to_date: Annotated[date, Query(alias="to", examples=["2026-11-30"])],
) -> ViewsStatsOut:
    use_mock(settings, response, "10")
    return mock.views_stats(from_date, to_date)


@router.get(
    "/top",
    response_model=TopPostsOut,
    summary="조회수 상위 게시물",
    responses=problem_responses(401, 422, 501),
)
def top(
    settings: AppSettings,
    response: Response,
    days: Annotated[StatsPeriod, Query(description="최근 며칠")] = StatsPeriod.WEEK,
) -> TopPostsOut:
    use_mock(settings, response, "10")
    return mock.top_posts(days)


@router.get(
    "/downloads",
    response_model=DownloadsStatsOut,
    summary="원본 다운로드 상위 사진",
    responses=problem_responses(401, 422, 501),
)
def downloads(
    settings: AppSettings,
    response: Response,
    days: Annotated[StatsPeriod, Query(description="최근 며칠")] = StatsPeriod.MONTH,
) -> DownloadsStatsOut:
    use_mock(settings, response, "11")
    return mock.downloads_stats(days)
