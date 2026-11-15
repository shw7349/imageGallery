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
TOP_LIMIT = 50


def original_url(session: Session, storage: S3Storage, photo_id: int, visitor: str, per_hour: int) -> str:
    """조건: 게시물 PUBLISHED + 원본 다운로드 허용 + 사진 READY.

    - 없거나 비공개·처리 전 사진: 404 (존재 여부를 드러내지 않는다)
    - 공개됐지만 다운로드 비허용: 403
    - 방문자당 1시간 per_hour회 초과: 429
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

        recent = session.execute(
            select(func.count(), func.min(DownloadLog.created_at)).where(
                DownloadLog.visitor_hash == visitor,
                DownloadLog.created_at > func.now() - LIMIT_WINDOW,
            )
        ).one()
        if int(recent[0]) >= per_hour:
            raise TooManyRequests(
                f"원본 다운로드는 1시간에 {per_hour}회까지입니다. 잠시 후 다시 시도하세요",
                retry_after_seconds=_retry_after(recent[1]),
            )
        session.add(DownloadLog(photo_id=photo.id, visitor_hash=visitor))
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
