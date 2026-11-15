"""사진 변환 작업 관련 관리자 동작."""

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import transaction
from app.errors import Conflict, NotFound
from app.jobs import queue
from app.models import Photo, PhotoStatus

logger = logging.getLogger(__name__)


def retry_photo(session: Session, photo_id: int) -> Photo:
    """FAILED 사진의 변환을 다시 등록한다(시도 횟수는 새 작업으로 초기화)."""
    with transaction(session):
        photo = session.scalar(select(Photo).where(Photo.id == photo_id).with_for_update())
        if photo is None:
            raise NotFound(f"사진 {photo_id}를 찾을 수 없습니다")
        if photo.status is not PhotoStatus.FAILED:
            raise Conflict(f"실패한(FAILED) 사진만 재처리할 수 있습니다(현재 상태: {photo.status.value})")
        photo.status = PhotoStatus.UPLOADED
        photo.error_message = None
        queue.enqueue(session, photo.id)
    logger.info("photo retry requested", extra={"photo_id": photo_id})
    return photo
