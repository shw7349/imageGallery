"""사진 변환 워커. 실행: `python -m app.jobs.worker` (API와 같은 이미지, 실행 명령만 다름).

- WORKER_CONCURRENCY개의 스레드가 각자 claim → 처리 → complete/fail을 반복한다.
- 작업이 없으면 폴링 간격을 2초에서 최대 30초까지 점점 늘린다(작업이 생기면 다시 2초).
- 메인 스레드는 주기적으로 reclaim_stale(죽은 워커의 작업 회수)을 실행한다.
- SIGTERM/SIGINT: 새 작업은 받지 않고, 진행 중인 작업을 끝낸 뒤 종료한다.
"""

import logging
import os
import signal
import socket
import sys
import threading
from datetime import timedelta
from types import FrameType

from app.config import Settings, get_settings
from app.db import SessionFactory, create_db_engine, create_session_factory, session_scope
from app.imaging import derivatives
from app.jobs import queue
from app.jobs.processing import PhotoProcessor, make_processor
from app.logging import setup_logging
from app.storage.s3 import S3Storage

logger = logging.getLogger(__name__)


class Worker:
    def __init__(
        self,
        factory: SessionFactory,
        settings: Settings,
        process: PhotoProcessor,
        name: str | None = None,
    ) -> None:
        self.factory = factory
        self.settings = settings
        self.process = process
        self.name = name or f"{socket.gethostname()}:{os.getpid()}"
        self.policy = queue.RetryPolicy.from_seconds(settings.job_retry_delays_seconds)
        self.stop_event = threading.Event()
        self.processed = 0
        self._lock = threading.Lock()

    def stop(self) -> None:
        if not self.stop_event.is_set():
            logger.info("worker stopping: finishing running jobs", extra={"worker": self.name})
        self.stop_event.set()

    # ── 한 번 처리 (테스트에서도 직접 호출) ─────────────────────

    def run_once(self, slot: int = 0) -> bool:
        """작업 하나를 처리했으면 True, 할 일이 없으면 False."""
        worker_id = f"{self.name}#{slot}"
        with session_scope(self.factory) as session:
            job = queue.claim(session, worker_id)
        if job is None:
            return False

        log = {"job_id": job.id, "photo_id": job.photo_id, "attempt": job.attempts + 1, "worker": worker_id}
        logger.info("job started", extra=log)
        try:
            self.process(job.photo_id)
        except Exception as exc:  # 어떤 실패든 작업 실패로 기록·재시도해야 한다(삼키지 않음)
            logger.exception("job failed", extra=log)
            with session_scope(self.factory) as session:
                queue.fail(session, job.id, f"{type(exc).__name__}: {exc}", self.policy)
        else:
            with session_scope(self.factory) as session:
                queue.complete(session, job.id)
            logger.info("job done", extra=log)
        with self._lock:
            self.processed += 1
        return True

    def reclaim(self) -> int:
        with session_scope(self.factory) as session:
            return queue.reclaim_stale(
                session, timedelta(seconds=self.settings.job_stale_after_seconds), self.policy
            )

    # ── 루프 ────────────────────────────────────────────

    def _loop(self, slot: int) -> None:
        poll = self.settings.worker_poll_seconds
        idle = poll
        while not self.stop_event.is_set():
            try:
                worked = self.run_once(slot)
            except Exception:  # DB 일시 장애 등: 로그 후 대기했다가 계속(스레드가 죽으면 안 됨)
                logger.exception("worker loop error", extra={"slot": slot})
                worked = False
            if worked:
                idle = poll
                continue
            self.stop_event.wait(idle)
            idle = min(idle * 2, self.settings.worker_idle_max_seconds)

    def run(self) -> None:
        logger.info(
            "worker started",
            extra={"worker": self.name, "concurrency": self.settings.worker_concurrency},
        )
        threads = [
            threading.Thread(target=self._loop, args=(i,), name=f"worker-{i}", daemon=False)
            for i in range(self.settings.worker_concurrency)
        ]
        for t in threads:
            t.start()
        while True:
            try:
                self.reclaim()
            except Exception:  # 회수 실패는 다음 주기에 다시 시도
                logger.exception("reclaim failed")
            if self.stop_event.wait(self.settings.worker_reclaim_interval_seconds):
                break
        for t in threads:
            t.join()
        logger.info("worker stopped", extra={"worker": self.name, "processed": self.processed})


def main() -> int:
    settings = get_settings()
    setup_logging(settings.log_level)
    engine = create_db_engine(settings)
    factory = create_session_factory(engine)
    derivatives.configure(settings.vips_concurrency, settings.vips_cache_max_mb)
    worker = Worker(factory, settings, make_processor(factory, S3Storage(settings), settings))

    def handle(signum: int, _frame: FrameType | None) -> None:
        logger.info("signal received", extra={"signal": signal.Signals(signum).name})
        worker.stop()

    signal.signal(signal.SIGTERM, handle)
    signal.signal(signal.SIGINT, handle)
    try:
        worker.run()
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
