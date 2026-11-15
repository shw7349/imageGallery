from datetime import datetime

from sqlalchemy import BigInteger, Boolean, Identity, Index, String, func
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, utc_timestamp


class AdminUser(Base):
    __tablename__ = "admin_user"

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(utc_timestamp(), server_default=func.now())


class LoginAttempt(Base):
    __tablename__ = "login_attempt"
    __table_args__ = (
        # 03단계 잠금 판정: 사용자명·IP별 최근 실패 조회
        Index(None, "username", "created_at"),
        Index(None, "ip", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    username: Mapped[str] = mapped_column(String(50))
    ip: Mapped[str] = mapped_column(INET)
    succeeded: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(utc_timestamp(), server_default=func.now())
