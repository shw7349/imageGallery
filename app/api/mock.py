"""목업 모드 공통: 켜져 있으면 응답에 X-Mock: true를 붙이고, 꺼져 있으면 501."""

from fastapi import Response

from app.config import Settings
from app.errors import NotImplementedYet

MOCK_HEADER = "X-Mock"


def use_mock(settings: Settings, response: Response, step: str) -> bool:
    """목업이면 True(헤더 설정). 아니면 NotImplementedYet(501)."""
    if not settings.mock_api:
        raise NotImplementedYet(f"{step}단계에서 구현")
    response.headers[MOCK_HEADER] = "true"
    return True
