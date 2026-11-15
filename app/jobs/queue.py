"""사진 변환 작업 큐 (PostgreSQL 테이블 + FOR UPDATE SKIP LOCKED). ADR 0002.

상태: PENDING → (claim) RUNNING → (complete) DONE
                         └─(fail / 회수)→ 재시도 대기 PENDING … 최대 횟수 초과 → FAILED (+ photo FAILED)

- 시간 계산은 DB의 now()로 한다(워커·DB 서버 시계 차이 무시).
- claim은 짧은 트랜잭션으로 RUNNING을 커밋하고 바로 잠금을 푼다. 처리 중에는 행 잠금을 잡지 않으므로
  워커가 죽으면 reclaim_stale이 locked_at 기준으로 회수한다.
- 함수는 호출자가 연 트랜잭션 안에서 실행된다(커밋은 호출자 몫).
"""

import logging
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import JobStatus, Photo, PhotoJob, PhotoStatus

logger = logging.getLogger(__name__)

MAX_ERROR_LENGTH = 2000
# models/jobs.py의 uq_photo_job_active_photo 조건과 같아야 한다.
ACTIVE_JOB_PREDICATE = text("status IN ('PENDING', 'RUNNING')")


@dataclass(frozen=True)
class RetryPolicy:
    delays: tuple[timedelta, ...]  # 길이 = 최대 재시도 횟수

    @classmethod
    def from_seconds(cls, seconds: list[int]) -> "RetryPolicy":
        return cls(tuple(timedelta(seconds=s) for s in seconds))


@dataclass(frozen=True)
class ClaimedJob:
    id: int
    photo_id: int
    attempts: int


def enqueue(session: Session, photo_id: int) -> int | None:
    """PENDING 작업을 만든다. 같은 사진에 진행 중(PENDING/RUNNING) 작업이 있으면 만들지 않고 None.

    중복 방지는 부분 유니크 인덱스 uq_photo_job_active_photo가 보장한다(동시 호출에도 안전).
    """
    stmt = (
        insert(PhotoJob)
        .values(photo_id=photo_id, status=JobStatus.PENDING)
        .on_conflict_do_nothing(
            index_elements=[PhotoJob.photo_id],
            # 부분 인덱스 조건은 반드시 리터럴이어야 한다. 바인딩 파라미터로 보내면 psycopg가
            # 서버 측 prepared statement로 바꾼 뒤(같은 쿼리 6번째 실행부터) 인덱스를 찾지 못해 실패한다.
            index_where=ACTIVE_JOB_PREDICATE,
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


def claim(session: Session, worker_id: str) -> ClaimedJob | None:
    """실행할 작업 하나를 가져와 RUNNING으로 바꾼다. 다른 워커가 잡은 행은 건너뛴다(SKIP LOCKED)."""
    job = session.scalar(
        select(PhotoJob)
        .where(PhotoJob.status == JobStatus.PENDING, PhotoJob.next_run_at <= func.now())
        .order_by(PhotoJob.next_run_at, PhotoJob.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if job is None:
        return None
    job.status = JobStatus.RUNNING
    job.locked_at = func.now()  # SQL 식: DB 시간으로 기록
    job.locked_by = worker_id[:100]
    session.flush()
    return ClaimedJob(id=job.id, photo_id=job.photo_id, attempts=job.attempts)


def complete(session: Session, job_id: int) -> None:
    """작업 완료. 처리 중 사진(과 작업)이 삭제됐으면 아무것도 하지 않는다."""
    session.execute(
        update(PhotoJob)
        .where(PhotoJob.id == job_id, PhotoJob.status == JobStatus.RUNNING)
        .values(status=JobStatus.DONE, locked_at=None, locked_by=None, last_error=None)
    )


def fail(session: Session, job_id: int, error: str, policy: RetryPolicy) -> JobStatus | None:
    """실패 기록. 재시도가 남았으면 백오프 후 PENDING, 아니면 FAILED + 사진 FAILED.

    반환: 바뀐 상태(작업이 사라졌으면 None).
    """
    job = session.scalar(select(PhotoJob).where(PhotoJob.id == job_id).with_for_update())
    if job is None or job.status is not JobStatus.RUNNING:
        return None
    return _record_failure(session, job, error, policy)


def _record_failure(session: Session, job: PhotoJob, error: str, policy: RetryPolicy) -> JobStatus:
    job.attempts += 1
    job.last_error = error[:MAX_ERROR_LENGTH]
    job.locked_at = None
    job.locked_by = None
    if job.attempts <= len(policy.delays):
        job.status = JobStatus.PENDING
        job.next_run_at = func.now() + policy.delays[job.attempts - 1]  # SQL 식: DB 시간 기준
        logger.warning(
            "job failed, will retry",
            extra={"job_id": job.id, "photo_id": job.photo_id, "attempts": job.attempts},
        )
    else:
        job.status = JobStatus.FAILED
        session.execute(
            update(Photo)
            .where(Photo.id == job.photo_id)
            .values(status=PhotoStatus.FAILED, error_message=job.last_error)
        )
        logger.error(
            "job failed permanently",
            extra={"job_id": job.id, "photo_id": job.photo_id, "attempts": job.attempts},
        )
    session.flush()
    return job.status


def reclaim_stale(session: Session, stale_after: timedelta, policy: RetryPolicy) -> int:
    """RUNNING인데 오래된(워커가 죽은) 작업을 실패 1회로 처리해 되돌린다. 반환: 회수한 수.

    죽은 원인이 그 사진 자체(메모리 초과 등)일 수 있으므로 회수도 시도 횟수에 넣어 무한 반복을 막는다.
    """
    jobs = session.scalars(
        select(PhotoJob)
        .where(PhotoJob.status == JobStatus.RUNNING, PhotoJob.locked_at < func.now() - stale_after)
        .with_for_update(skip_locked=True)
    ).all()
    for job in jobs:
        logger.warning("reclaiming stale job", extra={"job_id": job.id, "locked_by": job.locked_by})
        _record_failure(session, job, f"워커 응답 없음(locked_by={job.locked_by})", policy)
    return len(jobs)
