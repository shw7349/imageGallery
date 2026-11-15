"""작업 큐·워커 (실제 Postgres, 커밋되는 데이터 → 테스트마다 테이블 비움)."""

import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from datetime import timedelta

import pytest
from sqlalchemy import Engine, select, text, update
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import SessionFactory, create_session_factory, session_scope
from app.jobs import queue
from app.jobs.worker import Worker
from app.models import Category, JobStatus, Photo, PhotoJob, PhotoStatus, Post
from tests.conftest import _truncate_all

POLICY = queue.RetryPolicy.from_seconds([60, 300, 900])


def make_dummy_processor(factory: SessionFactory) -> Callable[[int], None]:
    """워커 동작만 검사하기 위한 처리기: 사진을 READY로 바꾼다(이미지 처리는 tests/imaging에서)."""

    def process(photo_id: int) -> None:
        with session_scope(factory) as s:
            s.execute(update(Photo).where(Photo.id == photo_id).values(status=PhotoStatus.READY))

    return process


@pytest.fixture
def factory(engine: Engine) -> Iterator[SessionFactory]:
    yield create_session_factory(engine)
    _truncate_all(engine)


@pytest.fixture
def fast_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "worker_poll_seconds": 0.02,
            "worker_idle_max_seconds": 0.05,
            "worker_reclaim_interval_seconds": 0.05,
        }
    )


def _photos(factory: SessionFactory, n: int, status: PhotoStatus = PhotoStatus.UPLOADED) -> list[int]:
    with session_scope(factory) as s:
        cat = Category(name=f"c{time.monotonic_ns()}")
        s.add(cat)
        s.flush()
        post = Post(category_id=cat.id, title="t")
        s.add(post)
        s.flush()
        photos = [
            Photo(
                post_id=post.id,
                sort_order=i,
                original_key=f"originals/{post.id}/{i}/original.jpg",
                original_filename="a.jpg",
                size_bytes=1,
                content_type="image/jpeg",
                status=status,
            )
            for i in range(n)
        ]
        s.add_all(photos)
        s.flush()
        return [p.id for p in photos]


def _enqueue(factory: SessionFactory, photo_ids: list[int]) -> list[int]:
    with session_scope(factory) as s:
        return [jid for pid in photo_ids if (jid := queue.enqueue(s, pid)) is not None]


def _job(factory: SessionFactory, job_id: int) -> PhotoJob:
    with factory() as s:
        job = s.get(PhotoJob, job_id)
        assert job is not None
        return job


def _photo(factory: SessionFactory, photo_id: int) -> Photo | None:
    with factory() as s:
        return s.get(Photo, photo_id)


def _make_due(factory: SessionFactory, job_id: int) -> None:
    with session_scope(factory) as s:
        s.execute(
            update(PhotoJob)
            .where(PhotoJob.id == job_id)
            .values(next_run_at=text("now() - interval '1 second'"))
        )


# ── enqueue · claim ─────────────────────────────────────


def test_enqueue_skips_when_active_job_exists(factory: SessionFactory) -> None:
    [pid] = _photos(factory, 1)
    [jid] = _enqueue(factory, [pid])
    assert _enqueue(factory, [pid]) == []  # PENDING 있음

    with session_scope(factory) as s:
        job = queue.claim(s, "w")
        assert job is not None
    assert _enqueue(factory, [pid]) == []  # RUNNING 있음

    with session_scope(factory) as s:
        queue.complete(s, jid)
    assert len(_enqueue(factory, [pid])) == 1  # 끝났으면 다시 등록 가능


def test_claim_sets_running_and_skips_future_jobs(factory: SessionFactory) -> None:
    pids = _photos(factory, 2)
    first, second = _enqueue(factory, pids)
    with session_scope(factory) as s:
        s.execute(
            update(PhotoJob)
            .where(PhotoJob.id == second)
            .values(next_run_at=text("now() + interval '1 hour'"))
        )

    with session_scope(factory) as s:
        claimed = queue.claim(s, "host:1#0")
        assert claimed is not None and claimed.id == first
        assert queue.claim(s, "host:1#0") is None  # 남은 건 아직 때가 아님

    job = _job(factory, first)
    assert job.status is JobStatus.RUNNING
    assert job.locked_by == "host:1#0" and job.locked_at is not None


def test_concurrent_claims_never_duplicate(factory: SessionFactory) -> None:
    job_ids = set(_enqueue(factory, _photos(factory, 60)))
    claimed: list[int] = []
    lock = threading.Lock()
    barrier = threading.Barrier(8)

    def grab(n: int) -> None:
        barrier.wait()
        while True:
            with session_scope(factory) as s:
                job = queue.claim(s, f"t{n}")
            if job is None:
                return
            with lock:
                claimed.append(job.id)

    threads = [threading.Thread(target=grab, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(claimed) == sorted(job_ids)  # 전부, 정확히 한 번씩


# ── 실패 · 재시도 ────────────────────────────────────────


def test_fail_backs_off_then_fails_permanently(factory: SessionFactory) -> None:
    [pid] = _photos(factory, 1)
    [jid] = _enqueue(factory, [pid])

    for attempt, delay in enumerate([60, 300, 900], start=1):
        with session_scope(factory) as s:
            assert queue.claim(s, "w") is not None
            assert queue.fail(s, jid, f"boom {attempt}", POLICY) is JobStatus.PENDING
        with factory() as s:
            remaining = s.scalar(
                select(text("extract(epoch from next_run_at - now())"))
                .select_from(PhotoJob)
                .where(PhotoJob.id == jid)
            )
        assert delay - 5 < float(remaining) <= delay  # 백오프 1분·5분·15분
        with session_scope(factory) as s:
            assert queue.claim(s, "w") is None  # 대기 중에는 가져가지 않는다
        _make_due(factory, jid)

    with session_scope(factory) as s:
        assert queue.claim(s, "w") is not None
        assert queue.fail(s, jid, "final boom", POLICY) is JobStatus.FAILED

    job = _job(factory, jid)
    assert (job.status, job.attempts, job.last_error) == (JobStatus.FAILED, 4, "final boom")
    photo = _photo(factory, pid)
    assert photo is not None
    assert (photo.status, photo.error_message) == (PhotoStatus.FAILED, "final boom")


def test_fail_or_complete_after_photo_deleted_is_noop(factory: SessionFactory) -> None:
    [pid] = _photos(factory, 1)
    [jid] = _enqueue(factory, [pid])
    with session_scope(factory) as s:
        queue.claim(s, "w")
    with session_scope(factory) as s:
        s.execute(text("DELETE FROM photo WHERE id = :id"), {"id": pid})
    with session_scope(factory) as s:
        assert queue.fail(s, jid, "x", POLICY) is None
        queue.complete(s, jid)  # 예외 없음


# ── 회수 ────────────────────────────────────────────────


def test_reclaim_stale_counts_as_attempt(factory: SessionFactory) -> None:
    stale_pid, fresh_pid = _photos(factory, 2)
    stale, fresh = _enqueue(factory, [stale_pid, fresh_pid])
    with session_scope(factory) as s:
        queue.claim(s, "dead-worker")
        queue.claim(s, "live-worker")
        s.execute(
            update(PhotoJob)
            .where(PhotoJob.id == stale)
            .values(locked_at=text("now() - interval '31 minutes'"))
        )

    with session_scope(factory) as s:
        assert queue.reclaim_stale(s, timedelta(minutes=30), POLICY) == 1

    job = _job(factory, stale)
    assert (job.status, job.attempts, job.locked_by) == (JobStatus.PENDING, 1, None)
    assert job.last_error and "dead-worker" in job.last_error
    assert _job(factory, fresh).status is JobStatus.RUNNING


def test_reclaim_gives_up_after_max_attempts(factory: SessionFactory) -> None:
    [pid] = _photos(factory, 1)
    [jid] = _enqueue(factory, [pid])
    with session_scope(factory) as s:
        s.execute(update(PhotoJob).where(PhotoJob.id == jid).values(attempts=3))
        queue.claim(s, "w")
        s.execute(
            update(PhotoJob).where(PhotoJob.id == jid).values(locked_at=text("now() - interval '1 hour'"))
        )
    with session_scope(factory) as s:
        queue.reclaim_stale(s, timedelta(minutes=30), POLICY)
    assert _job(factory, jid).status is JobStatus.FAILED
    photo = _photo(factory, pid)
    assert photo is not None and photo.status is PhotoStatus.FAILED


# ── 워커 ────────────────────────────────────────────────


def test_worker_processes_with_dummy_processor(factory: SessionFactory, settings: Settings) -> None:
    [pid] = _photos(factory, 1)
    [jid] = _enqueue(factory, [pid])
    worker = Worker(factory, settings, make_dummy_processor(factory), name="test")
    assert worker.run_once() is True
    assert worker.run_once() is False
    assert _job(factory, jid).status is JobStatus.DONE
    photo = _photo(factory, pid)
    assert photo is not None and photo.status is PhotoStatus.READY


def test_worker_records_processor_exception(factory: SessionFactory, settings: Settings) -> None:
    [pid] = _photos(factory, 1)
    [jid] = _enqueue(factory, [pid])

    def broken(_photo_id: int) -> None:
        raise RuntimeError("libvips exploded")

    Worker(factory, settings, broken, name="test").run_once()
    job = _job(factory, jid)
    assert (job.status, job.attempts) == (JobStatus.PENDING, 1)
    assert job.last_error == "RuntimeError: libvips exploded"


def test_worker_handles_photo_deleted_before_processing(factory: SessionFactory, settings: Settings) -> None:
    [pid] = _photos(factory, 1)
    _enqueue(factory, [pid])
    deleting = make_dummy_processor(factory)

    def delete_then_process(photo_id: int) -> None:
        with session_scope(factory) as s:
            s.execute(text("DELETE FROM photo WHERE id = :id"), {"id": photo_id})
        deleting(photo_id)

    assert Worker(factory, settings, delete_then_process, name="test").run_once() is True
    with factory() as s:
        assert s.scalar(select(PhotoJob.id)) is None  # cascade로 작업도 사라짐


def test_stop_finishes_running_job_before_exit(factory: SessionFactory, fast_settings: Settings) -> None:
    pids = _photos(factory, 2)
    job_ids = _enqueue(factory, pids)
    started, release = threading.Event(), threading.Event()
    done: list[int] = []

    def slow(photo_id: int) -> None:
        started.set()
        release.wait(5)
        done.append(photo_id)

    worker = Worker(factory, fast_settings, slow, name="test")
    runner = threading.Thread(target=worker.run)
    runner.start()
    assert started.wait(5)

    worker.stop()
    time.sleep(0.2)
    assert runner.is_alive()  # 진행 중 작업을 기다린다
    release.set()
    runner.join(5)
    assert not runner.is_alive()

    assert len(done) == 1  # 새 작업은 받지 않았다
    statuses = sorted(_job(factory, j).status.value for j in job_ids)
    assert statuses == ["DONE", "PENDING"]


def test_worker_loop_reclaims_stale_jobs(factory: SessionFactory, fast_settings: Settings) -> None:
    [pid] = _photos(factory, 1)
    [jid] = _enqueue(factory, [pid])
    with session_scope(factory) as s:
        queue.claim(s, "dead")
        s.execute(
            update(PhotoJob).where(PhotoJob.id == jid).values(locked_at=text("now() - interval '2 hours'"))
        )
    settings = fast_settings.model_copy(update={"job_retry_delays_seconds": [0, 0, 0]})
    worker = Worker(factory, settings, make_dummy_processor(factory), name="test")
    runner = threading.Thread(target=worker.run)
    runner.start()
    deadline = time.monotonic() + 5
    while _job(factory, jid).status is not JobStatus.DONE and time.monotonic() < deadline:
        time.sleep(0.05)
    worker.stop()
    runner.join(5)
    job = _job(factory, jid)
    assert (job.status, job.attempts) == (JobStatus.DONE, 1)


def test_sigterm_stops_worker_process(settings: Settings) -> None:
    """실제 프로세스(python -m app.jobs.worker)에 SIGTERM → 정상 종료(코드 0)."""
    env = {
        **os.environ,
        "DATABASE_URL": settings.database_url,
        "WORKER_POLL_SECONDS": "0.05",
        "WORKER_RECLAIM_INTERVAL_SECONDS": "0.1",
    }
    proc = subprocess.Popen(  # noqa: S603  # 고정 인자, 테스트용
        [sys.executable, "-m", "app.jobs.worker"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        assert proc.stdout is not None
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            line = proc.stdout.readline()
            if "worker started" in line:
                break
        else:
            pytest.fail("워커가 시작되지 않았습니다")
        proc.send_signal(signal.SIGTERM)
        out, _ = proc.communicate(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill()
    assert proc.returncode == 0
    assert "worker stopped" in out


# ── 재처리 API ──────────────────────────────────────────


def test_retry_api(admin: object, factory: SessionFactory) -> None:
    from tests.conftest import AdminClient

    assert isinstance(admin, AdminClient)
    failed_pid, ok_pid = _photos(factory, 1, PhotoStatus.FAILED)[0], _photos(factory, 1, PhotoStatus.READY)[0]
    with session_scope(factory) as s:
        s.execute(update(Photo).where(Photo.id == failed_pid).values(error_message="boom"))

    res = admin.http.post(f"/api/admin/photos/{failed_pid}/retry", headers=admin.headers())
    assert res.status_code == 200
    assert (res.json()["status"], res.json()["errorMessage"]) == ("UPLOADED", None)
    with factory() as s:
        assert s.scalar(select(PhotoJob.status).where(PhotoJob.photo_id == failed_pid)) is JobStatus.PENDING

    assert admin.http.post(f"/api/admin/photos/{ok_pid}/retry", headers=admin.headers()).status_code == 409
    assert admin.http.post("/api/admin/photos/999/retry", headers=admin.headers()).status_code == 404
    assert admin.http.post(f"/api/admin/photos/{failed_pid}/retry").status_code == 403  # CSRF
    _ = Session  # 미사용 import 방지


def test_enqueue_survives_server_side_prepared_statements(factory: SessionFactory) -> None:
    """회귀: 부분 인덱스 ON CONFLICT 조건이 파라미터면 psycopg가 prepare한 뒤(6회째부터) 실패했다."""
    pids = _photos(factory, 12)
    with session_scope(factory) as s:  # 같은 연결에서 12번 연속 실행
        ids = [queue.enqueue(s, pid) for pid in pids]
        again = [queue.enqueue(s, pid) for pid in pids]
    assert all(ids) and again == [None] * 12
