from datetime import date, datetime

from sqlalchemy import BigInteger, CheckConstraint, Date, ForeignKey, Identity, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, utc_timestamp


class ViewDedupe(Base):
    """방문자당 하루 1회 집계용. `INSERT … ON CONFLICT DO NOTHING`으로 중복을 거른다. 30일 지나면 삭제."""

    __tablename__ = "view_dedupe"
    __table_args__ = (Index(None, "view_date"),)

    post_id: Mapped[int] = mapped_column(ForeignKey("post.id", ondelete="CASCADE"), primary_key=True)
    visitor_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    view_date: Mapped[date] = mapped_column(Date, primary_key=True)


class PostViewDaily(Base):
    __tablename__ = "post_view_daily"
    __table_args__ = (CheckConstraint("count >= 0", name="count_non_negative"),)

    post_id: Mapped[int] = mapped_column(ForeignKey("post.id", ondelete="CASCADE"), primary_key=True)
    view_date: Mapped[date] = mapped_column(Date, primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class DownloadLog(Base):
    __tablename__ = "download_log"
    __table_args__ = (
        # 11단계: 방문자별 시간당 다운로드 횟수 제한 조회
        Index(None, "visitor_hash", "created_at"),
        Index(None, "photo_id"),
        # IP별 시간당 제한, 사이트 전체 하루 상한 조회
        Index(None, "ip_hash", "created_at"),
        Index(None, "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    photo_id: Mapped[int] = mapped_column(ForeignKey("photo.id", ondelete="CASCADE"))
    visitor_hash: Mapped[str] = mapped_column(String(64))
    # HMAC(SESSION_SECRET, IP). 쿠키를 지워 방문자 제한을 우회하는 것을 막는다(IP 원문은 저장하지 않음).
    ip_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(utc_timestamp(), server_default=func.now())
