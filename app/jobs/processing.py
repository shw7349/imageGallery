"""사진 처리(변환) 진입점. 워커가 작업 하나마다 process_photo(photo_id)를 호출한다.

07단계: 상태만 전환하는 더미(UPLOADED → PROCESSING → READY). 08단계에서 실제 파이프라인으로 교체한다.
처리 중 예외를 던지면 워커가 작업 실패로 기록하고 재시도한다.
"""

import logging
from collections.abc import Callable

from sqlalchemy import select

from app.db import SessionFactory, session_scope
from app.models import Photo, PhotoStatus

logger = logging.getLogger(__name__)

PhotoProcessor = Callable[[int], None]


def _set_status(factory: SessionFactory, photo_id: int, status: PhotoStatus) -> bool:
    """사진이 있으면 상태를 바꾸고 True. 처리 중 삭제됐으면 False."""
    with session_scope(factory) as session:
        photo = session.scalar(select(Photo).where(Photo.id == photo_id).with_for_update())
        if photo is None:
            return False
        photo.status = status
        if status is not PhotoStatus.FAILED:
            photo.error_message = None
        return True


def make_dummy_processor(factory: SessionFactory) -> PhotoProcessor:
    def process_photo(photo_id: int) -> None:
        if not _set_status(factory, photo_id, PhotoStatus.PROCESSING):
            logger.info("photo deleted before processing", extra={"photo_id": photo_id})
            return
        logger.warning("dummy processor: marking READY without derivatives", extra={"photo_id": photo_id})
        _set_status(factory, photo_id, PhotoStatus.READY)

    return process_photo
