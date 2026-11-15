"""요청 본문 크기 제한(순수 ASGI 미들웨어).

사진 바이트는 S3로 직접 가므로 API가 받는 본문은 작은 JSON뿐이다. 큰 본문을 메모리에 읽어 들이기 전에 끊는다.
- Content-Length가 한도를 넘으면 본문을 읽지 않고 바로 413
- Content-Length가 없거나(chunked) 거짓이면 실제로 읽은 바이트를 세다가 한도를 넘는 순간 413
"""

import contextlib
import json

from starlette.types import ASGIApp, Message, Receive, Scope, Send

PROBLEM_JSON = b"application/problem+json"


class _BodyTooLarge(Exception):
    pass


class MaxBodySizeMiddleware:
    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared = dict(scope["headers"]).get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > self.max_bytes:
            await self._reject(scope, send)
            return

        received = 0
        rejected = False

        async def limited_receive() -> Message:
            nonlocal received, rejected
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes and not rejected:
                    # 한도를 넘는 순간 여기서 바로 413을 보낸다. (예외만 던지면 프레임워크가
                    # "본문 파싱 오류" 400으로 바꿔 버린다.) 이후 앱이 보내는 응답은 버린다.
                    rejected = True
                    await self._reject(scope, send)
                    raise _BodyTooLarge
            return message

        async def guarded_send(message: Message) -> None:
            if not rejected:
                await send(message)

        with contextlib.suppress(_BodyTooLarge):  # 이미 413으로 응답했다
            await self.app(scope, limited_receive, guarded_send)

    async def _reject(self, scope: Scope, send: Send) -> None:
        body = json.dumps(
            {
                "type": "https://gallery.example/problems/payload_too_large",
                "title": "요청 본문이 너무 큽니다",
                "status": 413,
                "code": "payload_too_large",
                "detail": f"본문은 최대 {self.max_bytes // 1024}KB입니다",
                "instance": scope.get("path", ""),
            },
            ensure_ascii=False,
        ).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", PROBLEM_JSON),
                    (b"content-length", str(len(body)).encode()),
                    (b"connection", b"close"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
