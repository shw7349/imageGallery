"""관리자 통계 API. 날짜는 STATS_TIMEZONE(기본 Asia/Seoul) 기준."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import AppSettings, DbSession
from app.api.errors import problem_responses
from app.schemas.stats import (
    DailyViews,
    StatsPeriod,
    TopPost,
    TopPostsOut,
    ViewsStatsOut,
)
from app.services import views as views_service

router = APIRouter(prefix="/stats", tags=["admin-stats"])


@router.get(
    "/views",
    response_model=ViewsStatsOut,
    summary="일별 조회수",
    description="from~to(양 끝 포함, 최대 366일)의 일별 조회수 합계. "
    "postId를 주면 그 게시물의 날짜별 추이, 생략하면 사이트 전체.",
    responses=problem_responses(401, 404, 422),
)
def views(
    db: DbSession,
    from_date: Annotated[date, Query(alias="from", examples=["2026-11-01"])],
    to_date: Annotated[date, Query(alias="to", examples=["2026-11-30"])],
    post_id: Annotated[
        int | None, Query(alias="postId", ge=1, description="이 게시물의 조회수만. 생략하면 사이트 전체")
    ] = None,
) -> ViewsStatsOut:
    days = views_service.daily_views(db, from_date, to_date, post_id)
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
