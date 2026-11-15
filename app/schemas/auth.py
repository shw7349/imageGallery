from pydantic import Field

from app.schemas.base import CamelModel


class LoginRequest(CamelModel):
    username: str = Field(min_length=1, max_length=50, examples=["admin"])
    password: str = Field(min_length=1, max_length=256, examples=["correct horse battery staple"])


class AdminSession(CamelModel):
    """로그인·`/auth/me` 응답. 변경 요청 시 `csrfToken`을 `X-CSRF-Token` 헤더로 보내야 한다."""

    username: str = Field(examples=["admin"])
    csrf_token: str = Field(description="관리자 변경 요청(POST/PUT/PATCH/DELETE)의 X-CSRF-Token 헤더 값")
