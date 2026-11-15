from fastapi import APIRouter, Request, Response, status

from app.api import sessions
from app.api.deps import Admin, AppSettings, DbSession, client_ip
from app.api.errors import problem_responses
from app.schemas.auth import AdminSession, LoginRequest
from app.services import auth as auth_service

# 로그인만 인증 없이 열려 있다. 나머지는 protected 라우터(require_admin)에 등록된다.
public_router = APIRouter(prefix="/auth", tags=["admin-auth"])
router = APIRouter(prefix="/auth", tags=["admin-auth"])


@public_router.post(
    "/login",
    response_model=AdminSession,
    responses=problem_responses(401, 422, 429),
)
def login(
    body: LoginRequest, request: Request, response: Response, db: DbSession, settings: AppSettings
) -> AdminSession:
    policy = auth_service.LockPolicy.from_settings(settings)
    user = auth_service.authenticate(db, body.username, body.password, client_ip(request), policy)
    data = sessions.new_session(user.id, user.session_version)
    sessions.set_cookie(response, settings, data)
    return AdminSession(username=user.username, csrf_token=data.csrf_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, responses=problem_responses(401, 403))
def logout(admin: Admin, db: DbSession) -> Response:
    auth_service.revoke_sessions(db, admin.user.id)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    sessions.clear_cookie(response)
    return response


@router.get("/me", response_model=AdminSession, responses=problem_responses(401))
def me(admin: Admin) -> AdminSession:
    return AdminSession(username=admin.user.username, csrf_token=admin.session.csrf_token)
