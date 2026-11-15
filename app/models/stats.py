from datetime import date

from sqlalchemy import CheckConstraint, Date, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


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
