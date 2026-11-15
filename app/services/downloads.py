"""원본 다운로드: 허용 확인 → 기록·횟수 제한 → 10분짜리 Presigned URL."""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import transaction
from app.errors import Forbidden, NotFound, TooManyRequests
from app.models import DownloadLog, Photo, PhotoStatus, Post, PostStatus
from app.storage.s3 import S3Storage

logger = logging.getLogger(__name__)

LIMIT_WINDOW = timedelta(hours=1)
DAY = timedelta(days=1)
TOP_LIMIT = 50


@dataclass(frozen=True)
class DownloadLimits:
    per_visitor_hour: int
    per_ip_hour: int
    total_per_day: int


def _check_limits(session: Session, visitor: str, ip_hash: str, limits: DownloadLimits) -> None:
    """방문자(쿠키)·IP·사이트 전체 순으로 확인. 쿠키를 지워도 IP 제한과 전체 상한에는 걸린다."""
    hour_ago = func.now() - LIMIT_WINDOW
    for column, value, limit in (
        (DownloadLog.visitor_hash, visitor, limits.per_visitor_hour),
        (DownloadLog.ip_hash, ip_hash, limits.per_ip_hour),
    ):
        count, oldest = session.execute(
            select(func.count(), func.min(DownloadLog.created_at)).where(
                column == value, DownloadLog.created_at > hour_ago
            )
        ).one()
        if int(count) >= limit:
            raise TooManyRequests(
                f"원본 다운로드는 1시간에 {limit}회까지입니다. 잠시 후 다시 시도하세요",
                retry_after_seconds=_retry_after(oldest),
            )
    today_total = session.scalar(
        select(func.count()).select_from(DownloadLog).where(DownloadLog.created_at > func.now() - DAY)
    )
    if int(today_total or 0) >= limits.total_per_day:
        logger.error("daily download cap reached", extra={"cap": limits.total_per_day})
        raise TooManyRequests(
            "오늘 원본 다운로드 한도에 도달했습니다. 내일 다시 시도하세요", retry_after_seconds=3600
        )


def original_url(
    session: Session,
    storage: S3Storage,
    photo_id: int,
    visitor: str,
    ip_hash: str,
    limits: DownloadLimits,
) -> str:
    """조건: 게시물 PUBLISHED + 원본 다운로드 허용 + 사진 READY.

    - 없거나 비공개·처리 전 사진: 404 (존재 여부를 드러내지 않는다)
    - 공개됐지만 다운로드 비허용: 403
    - 방문자·IP 시간당 한도 또는 사이트 하루 상한 초과: 429
    """
    with transaction(session):
        row = session.execute(
            select(Photo, Post)
            .join(Post, Post.id == Photo.post_id)
            .where(
                Photo.id == photo_id, Photo.status == PhotoStatus.READY, Post.status == PostStatus.PUBLISHED
            )
        ).first()
        if row is None:
            raise NotFound(f"사진 {photo_id}를 찾을 수 없습니다")
        photo, post = row
        if not post.allow_original_download:
            raise Forbidden("이 게시물은 원본 다운로드를 허용하지 않습니다")

        _check_limits(session, visitor, ip_hash, limits)
        session.add(DownloadLog(photo_id=photo.id, visitor_hash=visitor, ip_hash=ip_hash))
        key, filename = photo.original_key, photo.original_filename

    url = storage.presign_get(storage.originals_bucket, key, download_filename=filename)
    logger.info("original download issued", extra={"photo_id": photo_id})  # URL은 로그에 남기지 않는다
    return url


def _retry_after(oldest: datetime | None) -> int:
    """가장 오래된 기록이 창 밖으로 나갈 때까지의 초."""
    if oldest is None:
        return 60
    return max(1, int((oldest + LIMIT_WINDOW - datetime.now(UTC)).total_seconds()))


@dataclass(frozen=True)
class PhotoDownloadRow:
    photo_id: int
    post_id: int
    post_title: str
    original_filename: str
    downloads: int


def top_downloads(session: Session, days: int) -> list[PhotoDownloadRow]:
    """최근 days일 원본 다운로드 상위 50개 사진."""
    count = func.count(DownloadLog.id).label("n")
    rows = session.execute(
        select(Photo.id, Post.id, Post.title, Photo.original_filename, count)
        .join(Photo, Photo.id == DownloadLog.photo_id)
        .join(Post, Post.id == Photo.post_id)
        .where(DownloadLog.created_at > func.now() - timedelta(days=days))
        .group_by(Photo.id, Post.id, Post.title, Photo.original_filename)
        .order_by(count.desc(), Photo.id)
        .limit(TOP_LIMIT)
    ).all()
    return [
        PhotoDownloadRow(photo_id=ph, post_id=po, post_title=t, original_filename=f, downloads=int(n))
        for ph, po, t, f, n in rows
    ]
