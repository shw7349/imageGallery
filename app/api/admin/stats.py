"""관리자 통계 API. 날짜는 STATS_TIMEZONE(기본 Asia/Seoul) 기준."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query, Response

from app.api.deps import AppSettings, DbSession
from app.api.errors import problem_responses
from app.api.mock import use_mock
from app.mock import data as mock
from app.schemas.stats import DailyViews, DownloadsStatsOut, StatsPeriod, TopPost, TopPostsOut, ViewsStatsOut
from app.services import views as views_service

router = APIRouter(prefix="/stats", tags=["admin-stats"])


@router.get(
    "/views",
    response_model=ViewsStatsOut,
    summary="일별 조회수",
    description="from~to(양 끝 포함, 최대 366일)의 일별 조회수 합계.",
    responses=problem_responses(401, 422),
)
def views(
    db: DbSession,
    from_date: Annotated[date, Query(alias="from", examples=["2026-11-01"])],
    to_date: Annotated[date, Query(alias="to", examples=["2026-11-30"])],
) -> ViewsStatsOut:
    days = views_service.daily_views(db, from_date, to_date)
    return ViewsStatsOut(
        from_date=from_date,
        to_date=to_date,
        total=sum(d.views for d in days),
        days=[DailyViews(date=d.day, views=d.views) for d in days],
    )


@router.get(
    "/top",
    response_model=TopPostsOut,
    summary="조회수 상위 게시물",
    responses=problem_responses(401, 422),
)
def top(
    db: DbSession,
    settings: AppSettings,
    days: Annotated[StatsPeriod, Query(description="최근 며칠(오늘 포함)")] = StatsPeriod.WEEK,
) -> TopPostsOut:
    rows = views_service.top_posts(db, int(days), views_service.today_in(settings.stats_timezone))
    return TopPostsOut(
        days=int(days), items=[TopPost(post_id=r.post_id, title=r.title, views=r.views) for r in rows]
    )


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
