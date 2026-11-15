"""사진 변환 작업 큐 (PostgreSQL). 06단계는 enqueue만, claim/complete/fail은 07단계에서 구현한다."""

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import JobStatus, PhotoJob


def enqueue(session: Session, photo_id: int) -> int | None:
    """PENDING 작업을 만든다. 같은 사진에 진행 중(PENDING/RUNNING) 작업이 있으면 만들지 않고 None.

    호출자 트랜잭션 안에서 실행된다(업로드 완료와 작업 등록이 함께 커밋되도록).
    중복 방지는 부분 유니크 인덱스 uq_photo_job_active_photo가 보장한다(동시 호출에도 안전).
    """
    stmt = (
        insert(PhotoJob)
        .values(photo_id=photo_id, status=JobStatus.PENDING)
        .on_conflict_do_nothing(
            index_elements=[PhotoJob.photo_id],
            index_where=PhotoJob.status.in_([JobStatus.PENDING, JobStatus.RUNNING]),
        )
        .returning(PhotoJob.id)
    )
    return session.scalar(stmt)


def active_job_id(session: Session, photo_id: int) -> int | None:
    return session.scalar(
        select(PhotoJob.id).where(
            PhotoJob.photo_id == photo_id,
            PhotoJob.status.in_([JobStatus.PENDING, JobStatus.RUNNING]),
        )
    )
