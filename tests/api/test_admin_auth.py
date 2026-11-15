import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner, URLSafeTimedSerializer
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app.api import sessions
from app.config import Settings
from app.errors import Conflict, InvalidCredentials, TooManyRequests, ValidationFailed
from app.models import AdminUser, LoginAttempt
from app.services import auth as auth_service
from tests.conftest import AdminClient, AdminCredentials

LOGIN = "/api/admin/auth/login"
ME = "/api/admin/auth/me"
LOGOUT = "/api/admin/auth/logout"


def _login(client: TestClient, username: str, password: str) -> object:
    return client.post(LOGIN, json={"username": username, "password": password})


# ── 로그인 ───────────────────────────────────────────────


def test_login_success_sets_secure_cookie(client: TestClient, admin_credentials: AdminCredentials) -> None:
    res = client.post(
        LOGIN, json={"username": admin_credentials.username, "password": admin_credentials.password}
    )
    assert res.status_code == 200
    body = res.json()
    assert body["username"] == "admin"
    assert len(body["csrfToken"]) >= 32

    cookie = res.headers["set-cookie"].lower()
    assert sessions.SESSION_COOKIE in cookie
    for attr in ("httponly", "secure", "samesite=lax", "path=/api/admin", "max-age=43200"):
        assert attr in cookie
    assert res.headers["cache-control"] == "no-store"


def test_me_after_login(admin: AdminClient) -> None:
    res = admin.http.get(ME)
    assert res.status_code == 200
    assert res.json() == {"username": "admin", "csrfToken": admin.csrf}


def test_wrong_password_and_unknown_user_look_the_same(
    client: TestClient, admin_credentials: AdminCredentials
) -> None:
    wrong = client.post(LOGIN, json={"username": "admin", "password": "wrong-password"})
    unknown = client.post(LOGIN, json={"username": "nobody", "password": "wrong-password"})
    for res in (wrong, unknown):
        assert res.status_code == 401
        assert res.headers["content-type"] == "application/problem+json"
        assert res.json()["code"] == "invalid_credentials"
    assert wrong.json()["title"] == unknown.json()["title"]
    assert "set-cookie" not in wrong.headers


def test_login_validation_error_is_problem_json(client: TestClient) -> None:
    res = client.post(LOGIN, json={"username": ""})
    assert res.status_code == 422
    assert res.headers["content-type"] == "application/problem+json"
    body = res.json()
    assert body["code"] == "validation_failed"
    locs = {tuple(e["loc"]) for e in body["errors"]}
    assert ("body", "password") in locs


def test_login_relogin_issues_new_csrf(client: TestClient, admin_credentials: AdminCredentials) -> None:
    creds = {"username": admin_credentials.username, "password": admin_credentials.password}
    first = client.post(LOGIN, json=creds).json()["csrfToken"]
    second = client.post(LOGIN, json=creds).json()["csrfToken"]
    assert first != second


# ── 잠금 ────────────────────────────────────────────────


def test_lockout_after_five_failures_even_with_correct_password(
    client: TestClient, admin_credentials: AdminCredentials
) -> None:
    for _ in range(5):
        assert client.post(LOGIN, json={"username": "admin", "password": "wrong"}).status_code == 401
    res = client.post(LOGIN, json={"username": "admin", "password": admin_credentials.password})
    assert res.status_code == 429
    assert res.headers["content-type"] == "application/problem+json"
    assert 0 < int(res.headers["retry-after"]) <= 15 * 60


def test_lockout_by_ip_across_usernames(client: TestClient, admin_credentials: AdminCredentials) -> None:
    for i in range(5):
        client.post(LOGIN, json={"username": f"guess{i}", "password": "wrong"})
    res = client.post(LOGIN, json={"username": "admin", "password": admin_credentials.password})
    assert res.status_code == 429


def _policy() -> auth_service.LockPolicy:
    return auth_service.LockPolicy(max_failures=5, window=timedelta(minutes=15))


def test_success_resets_username_counter(db: Session) -> None:
    auth_service.create_admin(db, "owner", "correct-password-123")
    ips = iter(f"10.0.0.{i}" for i in range(1, 100))  # IP 잠금과 분리하기 위해 매번 다른 IP
    for _ in range(4):
        with pytest.raises(InvalidCredentials):
            auth_service.authenticate(db, "owner", "bad", next(ips), _policy())
    auth_service.authenticate(db, "owner", "correct-password-123", next(ips), _policy())
    for _ in range(4):
        with pytest.raises(InvalidCredentials):
            auth_service.authenticate(db, "owner", "bad", next(ips), _policy())
    assert auth_service.authenticate(db, "owner", "correct-password-123", next(ips), _policy())


def test_old_failures_outside_window_are_ignored(db: Session) -> None:
    auth_service.create_admin(db, "owner", "correct-password-123")
    old = datetime.now(UTC) - timedelta(minutes=16)
    for _ in range(5):
        db.add(LoginAttempt(username="owner", ip="10.1.1.1", succeeded=False, created_at=old))
    db.flush()
    assert auth_service.authenticate(db, "owner", "correct-password-123", "10.1.1.1", _policy())


def test_locked_attempts_are_not_recorded(db: Session) -> None:
    auth_service.create_admin(db, "owner", "correct-password-123")
    for _ in range(5):
        with pytest.raises(InvalidCredentials):
            auth_service.authenticate(db, "owner", "bad", "10.2.2.2", _policy())
    with pytest.raises(TooManyRequests):
        auth_service.authenticate(db, "owner", "bad", "10.2.2.2", _policy())
    assert db.scalar(select(func.count()).select_from(LoginAttempt)) == 5


# ── 세션 ────────────────────────────────────────────────


def test_me_without_cookie_is_401(client: TestClient) -> None:
    res = client.get(ME)
    assert res.status_code == 401
    assert res.json()["code"] == "authentication_required"


def test_tampered_cookie_is_401(admin: AdminClient) -> None:
    token = admin.http.cookies.get(sessions.SESSION_COOKIE)
    assert token
    admin.http.cookies.clear()
    admin.http.cookies.set(sessions.SESSION_COOKIE, token[:-2] + "xx", path="/api/admin")
    assert admin.http.get(ME).status_code == 401


class _PastSigner(TimestampSigner):
    """13시간 전에 서명한 것처럼 만든다(만료 12시간)."""

    def get_timestamp(self) -> int:
        return int(time.time()) - 13 * 3600


def test_expired_session_is_401(client: TestClient, settings: Settings, engine: Engine) -> None:
    with Session(engine) as s:
        user = auth_service.create_admin(s, "admin", "correct-password-123")
        uid, ver = user.id, user.session_version
    secret = settings.session_secret.get_secret_value()
    fresh = sessions.serializer(settings).dumps({"uid": uid, "ver": ver, "csrf": "x"})
    expired = URLSafeTimedSerializer(secret, salt="admin-session-v1", signer=_PastSigner).dumps(
        {"uid": uid, "ver": ver, "csrf": "x"}
    )
    client.cookies.set(sessions.SESSION_COOKIE, fresh, path="/api/admin")
    assert client.get(ME).status_code == 200  # 같은 방식으로 만든 유효한 쿠키는 통과(대조군)

    client.cookies.clear()
    client.cookies.set(sessions.SESSION_COOKIE, expired, path="/api/admin")
    assert client.get(ME).status_code == 401


def test_logout_revokes_cookie_even_if_replayed(admin: AdminClient) -> None:
    old_token = admin.http.cookies.get(sessions.SESSION_COOKIE)
    res = admin.http.post(LOGOUT, headers=admin.headers())
    assert res.status_code == 204
    assert admin.http.get(ME).status_code == 401

    # 탈취한 옛 쿠키를 다시 보내도 거부된다(session_version 증가).
    assert old_token
    admin.http.cookies.set(sessions.SESSION_COOKIE, old_token, path="/api/admin")
    assert admin.http.get(ME).status_code == 401


# ── CSRF ────────────────────────────────────────────────


def test_mutation_without_csrf_is_403(admin: AdminClient) -> None:
    res = admin.http.post(LOGOUT)
    assert res.status_code == 403
    assert res.json()["code"] == "forbidden"
    assert admin.http.get(ME).status_code == 200  # 로그아웃되지 않았다


def test_mutation_with_wrong_csrf_is_403(admin: AdminClient) -> None:
    assert admin.http.post(LOGOUT, headers={"X-CSRF-Token": "wrong"}).status_code == 403


def test_safe_method_does_not_need_csrf(admin: AdminClient) -> None:
    assert admin.http.get(ME).status_code == 200


# ── 계정 관리 ────────────────────────────────────────────


def test_create_admin_rejects_short_password_and_duplicates(db: Session) -> None:
    with pytest.raises(ValidationFailed):
        auth_service.create_admin(db, "owner", "short")
    auth_service.create_admin(db, "owner", "long-enough-password")
    with pytest.raises(Conflict):
        auth_service.create_admin(db, "owner", "long-enough-password")


def test_password_is_hashed_with_argon2(db: Session) -> None:
    user = auth_service.create_admin(db, "owner", "long-enough-password")
    assert user.password_hash.startswith("$argon2id$")
    assert "long-enough-password" not in user.password_hash


def test_reset_password_invalidates_sessions(db: Session) -> None:
    user = auth_service.create_admin(db, "owner", "long-enough-password")
    version = user.session_version
    auth_service.reset_password(db, "owner", "another-long-password")
    db.refresh(user)
    assert user.session_version == version + 1
    assert auth_service.get_active_admin(db, user.id, version) is None
    assert auth_service.authenticate(db, "owner", "another-long-password", "10.3.3.3", _policy())


def test_cli_create_admin(settings: Settings, engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    from app import cli

    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli.getpass, "getpass", lambda _prompt: "cli-password-1234")
    try:
        assert cli.main(["create-admin", "--username", "cli-admin"]) == 0
        assert cli.main(["create-admin", "--username", "cli-admin"]) == 1  # 중복
        with Session(engine) as s:
            assert s.scalar(select(AdminUser).where(AdminUser.username == "cli-admin")) is not None
    finally:
        with engine.begin() as conn:
            conn.execute(AdminUser.__table__.delete().where(AdminUser.username == "cli-admin"))
