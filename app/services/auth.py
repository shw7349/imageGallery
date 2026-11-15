"""관리자 인증: 비밀번호 해시, 로그인 검증, 시도 기록, 잠금."""

import logging
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import ColumnElement, func, select, update
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import transaction
from app.errors import Conflict, InvalidCredentials, NotFound, TooManyRequests, ValidationFailed
from app.models import AdminUser, LoginAttempt

logger = logging.getLogger(__name__)

MIN_PASSWORD_LENGTH = 12

_hasher = PasswordHasher()
# 존재하지 않는 사용자에도 같은 시간이 걸리도록 검증할 더미 해시(사용자 존재 여부 노출 방지).
_DUMMY_HASH = _hasher.hash("dummy-password-for-timing-equalization")


@dataclass(frozen=True)
class LockPolicy:
    max_failures: int
    window: timedelta

    @classmethod
    def from_settings(cls, settings: Settings) -> "LockPolicy":
        return cls(settings.login_max_failures, timedelta(seconds=settings.login_lock_seconds))


def hash_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValidationFailed(f"비밀번호는 {MIN_PASSWORD_LENGTH}자 이상이어야 합니다")
    return _hasher.hash(password)


def _verify(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def _failures_since(
    session: Session, condition: ColumnElement[bool], since: datetime
) -> tuple[int, datetime | None]:
    row = session.execute(
        select(func.count(), func.min(LoginAttempt.created_at)).where(
            condition,
            LoginAttempt.succeeded.is_(False),
            LoginAttempt.created_at > since,
        )
    ).one()
    return int(row[0]), row[1]


def _check_lock(session: Session, username: str, ip: str, policy: LockPolicy, now: datetime) -> None:
    window_start = now - policy.window
    last_success = session.scalar(
        select(func.max(LoginAttempt.created_at)).where(
            LoginAttempt.username == username, LoginAttempt.succeeded.is_(True)
        )
    )
    # 사용자명 기준: 마지막 성공 이후의 실패만 센다(정상 로그인 후에는 다시 5회 기회).
    user_since = max(window_start, last_success) if last_success else window_start
    checks = (
        _failures_since(session, LoginAttempt.username == username, user_since),
        _failures_since(session, LoginAttempt.ip == ip, window_start),
    )
    for count, oldest in checks:
        if count >= policy.max_failures and oldest is not None:
            retry = max(1, math.ceil((oldest + policy.window - now).total_seconds()))
            raise TooManyRequests(
                "로그인 실패가 많아 잠시 잠겼습니다. 나중에 다시 시도하세요", retry_after_seconds=retry
            )


def authenticate(session: Session, username: str, password: str, ip: str, policy: LockPolicy) -> AdminUser:
    """성공 시 관리자를 반환한다. 실패 시 InvalidCredentials, 잠금 시 TooManyRequests."""
    now = datetime.now(UTC)
    with transaction(session):
        _check_lock(session, username, ip, policy, now)

        user = session.scalar(select(AdminUser).where(AdminUser.username == username))
        ok = _verify(user.password_hash if user else _DUMMY_HASH, password) and user is not None
        session.add(LoginAttempt(username=username[:50], ip=ip, succeeded=ok))
        if user is not None and ok and _hasher.check_needs_rehash(user.password_hash):
            user.password_hash = _hasher.hash(password)

    if not ok or user is None:
        logger.info("admin login failed", extra={"username": username[:50], "ip": ip})
        raise InvalidCredentials()
    logger.info("admin login succeeded", extra={"admin_id": user.id, "ip": ip})
    return user


def get_active_admin(session: Session, admin_id: int, session_version: int) -> AdminUser | None:
    """세션 쿠키의 (id, version)이 현재 값과 같을 때만 관리자를 반환한다."""
    user = session.get(AdminUser, admin_id)
    if user is None or user.session_version != session_version:
        return None
    return user


def revoke_sessions(session: Session, admin_id: int) -> None:
    """해당 관리자의 모든 세션 쿠키를 무효화한다(로그아웃)."""
    with transaction(session):
        session.execute(
            update(AdminUser)
            .where(AdminUser.id == admin_id)
            .values(session_version=AdminUser.session_version + 1)
        )


def create_admin(session: Session, username: str, password: str) -> AdminUser:
    username = username.strip()
    if not 1 <= len(username) <= 50:
        raise ValidationFailed("사용자명은 1~50자여야 합니다")
    with transaction(session):
        if session.scalar(select(AdminUser.id).where(AdminUser.username == username)) is not None:
            raise Conflict(f"이미 존재하는 사용자명입니다: {username}")
        user = AdminUser(username=username, password_hash=hash_password(password))
        session.add(user)
    return user


def reset_password(session: Session, username: str, password: str) -> None:
    """비밀번호를 바꾸고 기존 세션을 모두 무효화한다."""
    with transaction(session):
        user = session.scalar(select(AdminUser).where(AdminUser.username == username))
        if user is None:
            raise NotFound(f"사용자를 찾을 수 없습니다: {username}")
        user.password_hash = hash_password(password)
        user.session_version += 1
