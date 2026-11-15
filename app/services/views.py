"""조회수 집계와 통계. ADR 0001: 중복 제거는 메모리가 아니라 DB(INSERT … ON CONFLICT)로 한다."""

import hashlib
import hmac
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db import transaction
from app.errors import ValidationFailed
from app.models import Post, PostViewDaily, ViewDedupe
from app.services import public

logger = logging.getLogger(__name__)

MAX_STATS_DAYS = 366
TOP_LIMIT = 20

# 흔한 크롤러·미리보기 봇. 완벽하지 않으며 사람이 아닌 트래픽을 대략 거른다.
_BOT_PATTERN = re.compile(
    r"bot|crawl|spider|slurp|facebookexternalhit|embedly|quora link preview|"
    r"preview|headless|python-requests|curl|wget|httpx|go-http-client|okhttp|java/",
    re.IGNORECASE,
)


def visitor_hash(secret: str, vid: str) -> str:
    """쿠키 값 그대로 저장하지 않는다(유출돼도 쿠키를 재구성할 수 없게)."""
    return hmac.new(secret.encode(), vid.encode(), hashlib.sha256).hexdigest()


def is_bot(user_agent: str) -> bool:
    return not user_agent.strip() or bool(_BOT_PATTERN.search(user_agent))


def today_in(tz: str, now: datetime | None = None) -> date:
    return (now or datetime.now(ZoneInfo(tz))).astimezone(ZoneInfo(tz)).date()


def record_view(session: Session, post_id: int, visitor: str, day: date) -> bool:
    """방문자당 하루 1회 집계. 새로 센 경우 True. 공개 게시물이 아니면 NotFound."""
    with transaction(session):
        public.require_public_post(session, post_id)
        inserted = session.scalar(
            insert(ViewDedupe)
            .values(post_id=post_id, visitor_hash=visitor, view_date=day)
            .on_conflict_do_nothing()
            .returning(ViewDedupe.post_id)
        )
        if inserted is None:
            return False
        session.execute(update(Post).where(Post.id == post_id).values(view_count=Post.view_count + 1))
        stmt = insert(PostViewDaily).values(post_id=post_id, view_date=day, count=1)
        session.execute(
            stmt.on_conflict_do_update(
                index_elements=[PostViewDaily.post_id, PostViewDaily.view_date],
                set_={"count": PostViewDaily.count + 1},
            )
        )
    return True


@dataclass(frozen=True)
class DailyTotal:
    day: date
    views: int


def daily_views(session: Session, start: date, end: date) -> list[DailyTotal]:
    """start~end(양 끝 포함)의 일별 합계. 조회가 없는 날도 0으로 채운다."""
    if end < start:
        raise ValidationFailed("to는 from과 같거나 이후여야 합니다")
    span = (end - start).days + 1
    if span > MAX_STATS_DAYS:
        raise ValidationFailed(f"기간은 최대 {MAX_STATS_DAYS}일입니다")
    rows = session.execute(
        select(PostViewDaily.view_date, func.sum(PostViewDaily.count))
        .where(PostViewDaily.view_date.between(start, end))
        .group_by(PostViewDaily.view_date)
    ).all()
    by_day = {d: int(n) for d, n in rows}
    return [
        DailyTotal(day=start + timedelta(days=i), views=by_day.get(start + timedelta(days=i), 0))
        for i in range(span)
    ]


@dataclass(frozen=True)
class TopPostRow:
    post_id: int
    title: str
    views: int


def top_posts(session: Session, days: int, today: date) -> list[TopPostRow]:
    """최근 days일(오늘 포함) 조회수 상위 20개."""
    start = today - timedelta(days=days - 1)
    total = func.sum(PostViewDaily.count).label("views")
    rows = session.execute(
        select(Post.id, Post.title, total)
        .join(PostViewDaily, PostViewDaily.post_id == Post.id)
        .where(PostViewDaily.view_date.between(start, today))
        .group_by(Post.id, Post.title)
        .order_by(total.desc(), Post.id)
        .limit(TOP_LIMIT)
    ).all()
    return [TopPostRow(post_id=i, title=t, views=int(v)) for i, t, v in rows]


def purge_dedupe(session: Session, today: date, retention_days: int) -> int:
    """retention_days보다 오래된 중복 제거 기록 삭제(어제·오늘 판정에는 영향 없음). 반환: 삭제 행 수."""
    with transaction(session):
        result = session.execute(
            delete(ViewDedupe).where(ViewDedupe.view_date < today - timedelta(days=retention_days))
        )
    deleted = int(result.rowcount or 0)  # type: ignore[attr-defined]  # CursorResult.rowcount
    if deleted:
        logger.info("view_dedupe purged", extra={"rows": deleted})
    return deleted
