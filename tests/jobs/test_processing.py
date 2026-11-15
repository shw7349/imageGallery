"""실제 처리기(make_processor) 통합 테스트: Postgres + MinIO + pyvips + exiftool."""

import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import pyvips
from sqlalchemy import Engine, select, text

from app.config import Settings
from app.db import SessionFactory, create_session_factory, session_scope
from app.imaging import pipeline
from app.jobs import processing, queue
from app.jobs.worker import Worker
from app.models import Category, JobStatus, Photo, PhotoJob, PhotoStatus, Post
from app.storage.s3 import S3Storage
from tests.conftest import _clean_buckets, _truncate_all


@pytest.fixture
def factory(engine: Engine, storage: S3Storage) -> Iterator[SessionFactory]:
    yield create_session_factory(engine)
    _truncate_all(engine)
    _clean_buckets(storage)


@pytest.fixture
def proc_settings(settings: Settings, tmp_path: Path) -> Settings:
    return settings.model_copy(update={"work_dir": tmp_path / "work"})


def _jpeg(path: Path, width: int = 1800, height: int = 1200) -> Path:
    xyz = pyvips.Image.xyz(width, height)
    r = xyz[0] * (255 / width)
    r.bandjoin([xyz[1] * (255 / height), r]).cast("uchar").copy(interpretation="srgb").jpegsave(str(path))
    exiftool = shutil.which("exiftool")
    if exiftool:
        subprocess.run(  # noqa: S603
            [exiftool, "-overwrite_original", "-Make=SONY", "-ISO=200", "-GPSLatitude=33.3", str(path)],
            check=True,
            capture_output=True,
        )
    return path


def _photo_with_original(factory: SessionFactory, storage: S3Storage, local: Path | None) -> tuple[int, int]:
    """(post_id, photo_id). local이 있으면 S3 원본으로 올린다."""
    with session_scope(factory) as s:
        cat = Category(name="c")
        s.add(cat)
        s.flush()
        post = Post(category_id=cat.id, title="t")
        s.add(post)
        s.flush()
        photo = Photo(
            post_id=post.id,
            original_key="",
            original_filename="shot.jpg",
            size_bytes=local.stat().st_size if local else 1,
            content_type="image/jpeg",
            status=PhotoStatus.UPLOADED,
        )
        s.add(photo)
        s.flush()
        photo.original_key = f"originals/{post.id}/{photo.id}/original.jpg"
        queue.enqueue(s, photo.id)
        ids = (post.id, photo.id)
    if local:
        storage.client.upload_file(
            str(local), storage.originals_bucket, f"originals/{ids[0]}/{ids[1]}/original.jpg"
        )
    return ids


def _worker(factory: SessionFactory, storage: S3Storage, settings: Settings) -> Worker:
    return Worker(factory, settings, processing.make_processor(factory, storage, settings), name="t")


def _photo(factory: SessionFactory, photo_id: int) -> Photo | None:
    with factory() as s:
        return s.get(Photo, photo_id)


def _job_status(factory: SessionFactory, photo_id: int) -> tuple[JobStatus, int, str | None]:
    with factory() as s:
        job = s.scalar(select(PhotoJob).where(PhotoJob.photo_id == photo_id))
        assert job is not None
        return job.status, job.attempts, job.last_error


def _head(storage: S3Storage, key: str) -> dict[str, Any]:
    res: dict[str, Any] = dict(storage.client.head_object(Bucket=storage.derived_bucket, Key=key))
    return res


def test_processes_photo_end_to_end(
    factory: SessionFactory, storage: S3Storage, proc_settings: Settings, tmp_path: Path
) -> None:
    post_id, photo_id = _photo_with_original(factory, storage, _jpeg(tmp_path / "shot.jpg"))
    assert _worker(factory, storage, proc_settings).run_once() is True

    photo = _photo(factory, photo_id)
    assert photo is not None
    assert photo.status is PhotoStatus.READY and photo.error_message is None
    assert (photo.width, photo.height) == (1800, 1200)
    prefix = f"derived/{post_id}/{photo_id}/"
    assert (photo.thumb_key, photo.preview_key, photo.dzi_key) == (
        prefix + "thumb.webp",
        prefix + "preview.webp",
        prefix + "photo.dzi",
    )
    assert photo.exif is not None
    if shutil.which("exiftool"):
        assert photo.exif["camera_make"] == "SONY" and photo.exif["iso"] == 200
    assert not any("gps" in k.lower() for k in photo.exif)

    thumb = _head(storage, prefix + "thumb.webp")
    assert thumb["ContentType"] == "image/webp"
    assert thumb["CacheControl"] == "public, max-age=31536000, immutable"
    assert _head(storage, prefix + "photo.dzi")["ContentType"] == "application/xml"
    assert _head(storage, prefix + "photo_files/0/0_0.webp")["ContentType"] == "image/webp"

    assert _job_status(factory, photo_id)[0] is JobStatus.DONE
    assert list((proc_settings.work_dir).iterdir()) == []  # 임시 디렉터리 정리


def test_missing_original_fails_job_and_cleans_up(
    factory: SessionFactory, storage: S3Storage, proc_settings: Settings
) -> None:
    _, photo_id = _photo_with_original(factory, storage, None)
    _worker(factory, storage, proc_settings).run_once()
    status, attempts, error = _job_status(factory, photo_id)
    assert (status, attempts) == (JobStatus.PENDING, 1)  # 재시도 대기
    assert error and "StorageError" in error
    assert list(proc_settings.work_dir.iterdir()) == []


def test_insufficient_disk_is_retried_later(
    factory: SessionFactory,
    storage: S3Storage,
    proc_settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, photo_id = _photo_with_original(factory, storage, _jpeg(tmp_path / "shot.jpg"))
    monkeypatch.setattr(processing.shutil, "disk_usage", lambda _p: SimpleNamespace(free=1))
    _worker(factory, storage, proc_settings).run_once()
    status, _, error = _job_status(factory, photo_id)
    assert status is JobStatus.PENDING and error and "디스크" in error


def test_photo_deleted_during_processing_removes_derived(
    factory: SessionFactory,
    storage: S3Storage,
    proc_settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    post_id, photo_id = _photo_with_original(factory, storage, _jpeg(tmp_path / "shot.jpg"))
    real = pipeline.process

    def delete_midway(original: Path, work: Path) -> pipeline.PipelineResult:
        result = real(original, work)
        with session_scope(factory) as s:
            s.execute(text("DELETE FROM photo WHERE id = :id"), {"id": photo_id})
        return result

    monkeypatch.setattr(processing.pipeline, "process", delete_midway)
    assert _worker(factory, storage, proc_settings).run_once() is True
    assert storage.head_object(storage.derived_bucket, f"derived/{post_id}/{photo_id}/thumb.webp") is None


def test_reprocessing_replaces_previous_derived(
    factory: SessionFactory, storage: S3Storage, proc_settings: Settings, tmp_path: Path
) -> None:
    post_id, photo_id = _photo_with_original(factory, storage, _jpeg(tmp_path / "shot.jpg"))
    stale = f"derived/{post_id}/{photo_id}/photo_files/99/stale.webp"
    storage.client.put_object(Bucket=storage.derived_bucket, Key=stale, Body=b"old")
    _worker(factory, storage, proc_settings).run_once()
    assert storage.head_object(storage.derived_bucket, stale) is None


def test_corrupt_original_eventually_marks_photo_failed(
    factory: SessionFactory, storage: S3Storage, proc_settings: Settings, tmp_path: Path
) -> None:
    broken = tmp_path / "broken.jpg"
    broken.write_bytes(b"\xff\xd8\xff\xe0 definitely not a jpeg" * 10)
    _, photo_id = _photo_with_original(factory, storage, broken)
    settings = proc_settings.model_copy(update={"job_retry_delays_seconds": [0, 0]})
    worker = _worker(factory, storage, settings)
    for _ in range(3):
        assert worker.run_once() is True
    photo = _photo(factory, photo_id)
    assert photo is not None and photo.status is PhotoStatus.FAILED
    assert photo.error_message and photo.error_message.startswith("ImagingError")


def test_disk_estimate() -> None:
    mb = 1024 * 1024
    assert processing.required_disk_bytes(300 * mb, raw=False) == 900 * mb + processing.DISK_HEADROOM_BYTES
    assert processing.required_disk_bytes(60 * mb, raw=True) == 480 * mb + processing.DISK_HEADROOM_BYTES
