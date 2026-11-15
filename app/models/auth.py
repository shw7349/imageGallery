from datetime import datetime

from sqlalchemy import BigInteger, Boolean, Identity, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, utc_timestamp


class AdminUser(Base):
    __tablename__ = "admin_user"

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    # 세션 쿠키에 함께 서명된다. 로그아웃·비밀번호 재설정 시 +1 → 기존 쿠키 전부 무효.
    session_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
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
