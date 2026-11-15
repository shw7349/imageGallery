"""사진 처리 진입점: 워커가 작업 하나마다 process_photo(photo_id)를 호출한다.

흐름: PROCESSING 전환 → 디스크 여유 확인 → S3 원본을 WORK_DIR로 스트리밍 다운로드 → 파이프라인 →
      파생물을 S3 derived/{postId}/{photoId}/에 업로드(1년 immutable 캐시) → 사진 READY
- 임시 디렉터리는 성공·실패와 관계없이 지운다(finally).
- 처리 중 사진이 삭제되면 올린 파생물을 지우고 조용히 끝낸다.
- 예외를 던지면 워커가 작업 실패로 기록하고 재시도한다(최종 실패 시 사진 FAILED + error_message).
"""

import logging
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select

from app.config import Settings
from app.db import SessionFactory, session_scope
from app.imaging import pipeline
from app.imaging.derivatives import DZI_BASENAME, PREVIEW_NAME, THUMB_NAME
from app.imaging.errors import ImagingError
from app.imaging.raw import is_raw
from app.models import Photo, PhotoStatus
from app.services.uploads import derived_prefix
from app.storage.s3 import S3Storage

logger = logging.getLogger(__name__)

PhotoProcessor = Callable[[int], None]

IMMUTABLE_CACHE = "public, max-age=31536000, immutable"
CONTENT_TYPES = {".webp": "image/webp", ".dzi": "application/xml"}
DISK_HEADROOM_BYTES = 512 * 1024 * 1024


class InsufficientDisk(ImagingError):
    """작업 디렉터리 여유 공간 부족(나중에 재시도)."""


@dataclass(frozen=True)
class _Target:
    post_id: int
    original_key: str
    size_bytes: int
    ext: str


def required_disk_bytes(size_bytes: int, raw: bool) -> int:
    """원본 + 중간 파일 + 결과물에 필요한 대략의 공간.

    RAW는 16bit 현상 TIFF가 원본의 약 6배까지 커질 수 있다. 일반 이미지는 원본 + 타일(원본의 1/3 안팎).
    """
    factor = 8 if raw else 3
    return size_bytes * factor + DISK_HEADROOM_BYTES


def _content_type(path: Path) -> str:
    return CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")


def _begin(factory: SessionFactory, photo_id: int) -> _Target | None:
    with session_scope(factory) as session:
        photo = session.scalar(select(Photo).where(Photo.id == photo_id).with_for_update())
        if photo is None:
            return None
        if photo.status not in (PhotoStatus.UPLOADED, PhotoStatus.PROCESSING):
            logger.warning(
                "unexpected photo status, skipping", extra={"photo_id": photo_id, "status": photo.status}
            )
            return None
        photo.status = PhotoStatus.PROCESSING
        return _Target(
            post_id=photo.post_id,
            original_key=photo.original_key,
            size_bytes=photo.size_bytes,
            ext=photo.original_key.rsplit(".", 1)[-1],
        )


def _finish(factory: SessionFactory, photo_id: int, prefix: str, result: pipeline.PipelineResult) -> bool:
    with session_scope(factory) as session:
        photo = session.scalar(select(Photo).where(Photo.id == photo_id).with_for_update())
        if photo is None:
            return False
        photo.thumb_key = prefix + THUMB_NAME
        photo.preview_key = prefix + PREVIEW_NAME
        photo.dzi_key = prefix + f"{DZI_BASENAME}.dzi"
        photo.width = result.width
        photo.height = result.height
        photo.exif = result.exif
        photo.error_message = None
        photo.status = PhotoStatus.READY
        return True


def make_processor(factory: SessionFactory, storage: S3Storage, settings: Settings) -> PhotoProcessor:
    def process_photo(photo_id: int) -> None:
        target = _begin(factory, photo_id)
        if target is None:
            return

        settings.work_dir.mkdir(parents=True, exist_ok=True)
        need = required_disk_bytes(target.size_bytes, is_raw(Path(f"x.{target.ext}")))
        free = shutil.disk_usage(settings.work_dir).free
        if free < need:
            raise InsufficientDisk(
                f"작업 디스크 여유 부족: 필요 {need // 1024**2}MB, 남음 {free // 1024**2}MB"
            )

        work = Path(tempfile.mkdtemp(prefix=f"photo-{photo_id}-", dir=settings.work_dir))
        try:
            original = work / f"original.{target.ext}"
            storage.download_file(storage.originals_bucket, target.original_key, original)
            result = pipeline.process(original, work)

            prefix = derived_prefix(target.post_id, photo_id)
            storage.delete_prefix(storage.derived_bucket, prefix)  # 재처리: 이전 시도의 잔여물 제거
            uploaded = storage.upload_directory(
                result.out_dir,
                storage.derived_bucket,
                prefix,
                cache_control=IMMUTABLE_CACHE,
                content_type_for=_content_type,
            )
            if not _finish(factory, photo_id, prefix, result):
                storage.delete_prefix(storage.derived_bucket, prefix)
                logger.info("photo deleted during processing, derived removed", extra={"photo_id": photo_id})
                return
            logger.info("photo ready", extra={"photo_id": photo_id, "files": uploaded})
        finally:
            shutil.rmtree(work, ignore_errors=True)

    return process_photo
