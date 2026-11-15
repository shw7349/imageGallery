from fastapi import APIRouter, Request, Response, status
from sqlalchemy import Engine

from app.schemas.health import HealthResponse
from app.services.health import check_database

router = APIRouter(tags=["health"])


@router.get(
    "/healthz",
    summary="헬스 체크 (DB 연결 포함)",
    response_model=HealthResponse,
    responses={503: {"model": HealthResponse, "description": "의존 서비스 장애"}},
)
def healthz(request: Request, response: Response) -> HealthResponse:
    engine: Engine = request.app.state.engine
    db_ok = check_database(engine)
    response.headers["Cache-Control"] = "no-store"
    if not db_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthResponse(status="error", db="error")
    return HealthResponse(status="ok", db="ok")
