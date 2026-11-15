"""프로세스 메모리 토큰 버킷(키별 분당 요청 수 제한).

uvicorn 워커가 N개면 실제 한도는 최대 N배가 된다(워커마다 따로 센다). 정확한 전역 제한이 필요해지면
DB나 CDN(WAF) 수준으로 옮긴다.
"""

import math
import threading
import time
from collections.abc import Callable


class RateLimiter:
    def __init__(
        self, per_minute: int, clock: Callable[[], float] = time.monotonic, max_keys: int = 50_000
    ) -> None:
        self.capacity = float(per_minute)
        self.refill_per_second = per_minute / 60.0
        self.clock = clock
        self.max_keys = max_keys
        self._buckets: dict[str, tuple[float, float]] = {}  # key → (tokens, updated_at)
        self._lock = threading.Lock()

    def check(self, key: str) -> int | None:
        """허용이면 None, 초과면 다시 시도할 때까지의 초."""
        now = self.clock()
        with self._lock:
            tokens, updated = self._buckets.get(key, (self.capacity, now))
            tokens = min(self.capacity, tokens + (now - updated) * self.refill_per_second)
            if tokens < 1:
                self._buckets[key] = (tokens, now)
                return max(1, math.ceil((1 - tokens) / self.refill_per_second))
            self._buckets[key] = (tokens - 1, now)
            if len(self._buckets) > self.max_keys:
                self._prune(now)
            return None

    def _prune(self, now: float) -> None:
        # 가득 찬(오래 안 쓴) 버킷은 지워도 동작이 같다
        full_after = self.capacity / self.refill_per_second
        for key in [k for k, (_, t) in self._buckets.items() if now - t >= full_after]:
            del self._buckets[key]
