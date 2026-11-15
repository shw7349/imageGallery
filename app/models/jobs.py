from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, str_enum, utc_timestamp
from app.models.enums import JobStatus


class PhotoJob(TimestampMixin, Base):
    __tablename__ = "photo_job"
    __table_args__ = (
        Index(None, "status", "next_run_at"),
        # 한 사진에 진행 중(PENDING/RUNNING) 작업은 하나만 허용 — 07단계 enqueue의 중복 방지
        Index(
            "uq_photo_job_active_photo",
            "photo_id",
            unique=True,
            postgresql_where=text("status IN ('PENDING', 'RUNNING')"),  # jobs/queue.py와 같아야 함
        ),
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    photo_id: Mapped[int] = mapped_column(ForeignKey("photo.id", ondelete="CASCADE"))
    status: Mapped[JobStatus] = mapped_column(
        str_enum(JobStatus, "status"), default=JobStatus.PENDING, server_default=JobStatus.PENDING.value
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    next_run_at: Mapped[datetime] = mapped_column(utc_timestamp(), server_default=func.now())
    locked_at: Mapped[datetime | None] = mapped_column(utc_timestamp())
    locked_by: Mapped[str | None] = mapped_column(String(100))
    last_error: Mapped[str | None] = mapped_column(Text)
