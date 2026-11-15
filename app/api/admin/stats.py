"""관리자 통계 API. 계약은 05단계, 구현은 10·11단계."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.errors import problem_responses
from app.errors import NotImplementedYet
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
    from_date: Annotated[date, Query(alias="from", examples=["2026-11-01"])],
    to_date: Annotated[date, Query(alias="to", examples=["2026-11-30"])],
) -> ViewsStatsOut:
    raise NotImplementedYet("10단계에서 구현")


@router.get(
    "/top",
    response_model=TopPostsOut,
    summary="조회수 상위 게시물",
    responses=problem_responses(401, 422, 501),
)
def top(days: Annotated[StatsPeriod, Query(description="최근 며칠")] = StatsPeriod.WEEK) -> TopPostsOut:
    raise NotImplementedYet("10단계에서 구현")


@router.get(
    "/downloads",
    response_model=DownloadsStatsOut,
    summary="원본 다운로드 상위 사진",
    responses=problem_responses(401, 422, 501),
)
def downloads(
    days: Annotated[StatsPeriod, Query(description="최근 며칠")] = StatsPeriod.MONTH,
) -> DownloadsStatsOut:
    raise NotImplementedYet("11단계에서 구현")
