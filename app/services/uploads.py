"""사진 업로드(S3 멀티파트) 서비스.

파일 바이트는 이 프로세스를 지나가지 않는다. 여기서는 레코드 관리, URL 발급, S3 상태 대조만 한다.
절차: start → presign_parts → (브라우저가 S3에 PUT) → [list_parts로 이어 올리기] → complete
"""

import logging
import math
from dataclasses import dataclass
from pathlib import PurePosixPath

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import transaction
from app.errors import Conflict, NotFound, ValidationFailed
from app.jobs import queue
from app.models import Photo, PhotoStatus, Post
from app.storage.s3 import PartInfo, PresignedPart, S3Storage, UploadNotFound

logger = logging.getLogger(__name__)

MAX_PARTS = 10_000  # S3 제한

# 확장자 → 저장할 Content-Type. 클라이언트가 보낸 contentType은 참고만 하고 신뢰하지 않는다.
CONTENT_TYPES: dict[str, str] = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "tif": "image/tiff",
    "tiff": "image/tiff",
    "png": "image/png",
    "arw": "image/x-sony-arw",
    "cr2": "image/x-canon-cr2",
    "cr3": "image/x-canon-cr3",
    "nef": "image/x-nikon-nef",
    "raf": "image/x-fuji-raf",
    "orf": "image/x-olympus-orf",
    "rw2": "image/x-panasonic-rw2",
    "dng": "image/x-adobe-dng",
}


@dataclass(frozen=True)
class UploadLimits:
    max_bytes: int
    part_size: int


@dataclass(frozen=True)
class StartedUpload:
    photo: Photo
    upload_id: str
    part_size: int
    part_count: int


def original_key(post_id: int, photo_id: int, ext: str) -> str:
    """원본 키. 사용자 파일명은 키에 쓰지 않는다(경로 조작·인코딩 문제 방지, DB에만 저장)."""
    return f"originals/{post_id}/{photo_id}/original.{ext}"


def derived_prefix(post_id: int, photo_id: int) -> str:
    return f"derived/{post_id}/{photo_id}/"


def _extension(filename: str) -> str:
    ext = PurePosixPath(filename.replace("\\", "/")).suffix.lower().lstrip(".")
    if ext not in CONTENT_TYPES:
        raise ValidationFailed(
            f"지원하지 않는 파일 형식입니다: .{ext or '(없음)'}",
            errors=[{"loc": ["body", "filename"], "msg": "지원하지 않는 확장자", "type": "extension"}],
        )
    return ext


def part_count(size_bytes: int, part_size: int) -> int:
    return max(1, math.ceil(size_bytes / part_size))


def _expected_part_size(photo: Photo, part_number: int) -> int:
    part_size = _part_size(photo)
    count = part_count(photo.size_bytes, part_size)
    if part_number < count:
        return part_size
    return photo.size_bytes - part_size * (count - 1)


def _part_size(photo: Photo) -> int:
    if photo.upload_part_size is None:
        raise Conflict("업로드 정보가 없는 사진입니다")
    return photo.upload_part_size


def _get_photo(session: Session, photo_id: int, *, for_update: bool = False) -> Photo:
    query = select(Photo).where(Photo.id == photo_id)
    if for_update:
        query = query.with_for_update()
    photo = session.scalar(query)
    if photo is None:
        raise NotFound(f"사진 {photo_id}를 찾을 수 없습니다")
    return photo


def _require_uploading(photo: Photo) -> str:
    if photo.status is not PhotoStatus.UPLOADING or not photo.s3_upload_id:
        raise Conflict(f"업로드 중인 사진이 아닙니다(현재 상태: {photo.status.value})")
    return photo.s3_upload_id


def _norm_etag(etag: str) -> str:
    return etag.strip().strip('"')


# ── 시작 ────────────────────────────────────────────────


def start_upload(
    session: Session,
    storage: S3Storage,
    limits: UploadLimits,
    *,
    post_id: int,
    filename: str,
    size_bytes: int,
) -> StartedUpload:
    ext = _extension(filename)
    if size_bytes > limits.max_bytes:
        raise ValidationFailed(
            f"파일이 너무 큽니다(최대 {limits.max_bytes // 1024**2}MB)",
            errors=[{"loc": ["body", "sizeBytes"], "msg": "최대 크기 초과", "type": "too_large"}],
        )
    count = part_count(size_bytes, limits.part_size)
    if count > MAX_PARTS:
        raise ValidationFailed("파트 수가 S3 제한(10,000)을 넘습니다")

    with transaction(session):
        post = session.scalar(select(Post).where(Post.id == post_id).with_for_update())
        if post is None:
            raise NotFound(f"게시물 {post_id}를 찾을 수 없습니다")
        next_order = session.scalar(
            select(func.coalesce(func.max(Photo.sort_order) + 1, 0)).where(Photo.post_id == post_id)
        )
        photo = Photo(
            post_id=post_id,
            sort_order=int(next_order or 0),
            original_key="",  # id를 알아야 키를 만들 수 있어 flush 후 채운다
            original_filename=filename,
            size_bytes=size_bytes,
            content_type=CONTENT_TYPES[ext],
            status=PhotoStatus.UPLOADING,
            upload_part_size=limits.part_size,
        )
        session.add(photo)
        session.flush()
        photo.original_key = original_key(post_id, photo.id, ext)
        # S3 호출이 실패하면 트랜잭션이 롤백되어 레코드도 남지 않는다.
        # 반대로 S3 생성 후 커밋이 실패하면 고아 멀티파트가 남는데, 버킷 수명 주기(7일)가 정리한다.
        photo.s3_upload_id = storage.create_multipart_upload(photo.original_key, photo.content_type)

    logger.info(
        "upload started",
        extra={"photo_id": photo.id, "post_id": post_id, "size_bytes": size_bytes, "parts": count},
    )
    return StartedUpload(
        photo=photo, upload_id=photo.s3_upload_id, part_size=limits.part_size, part_count=count
    )


# ── 파트 URL · 파트 조회 ─────────────────────────────────


def presign_parts(
    session: Session, storage: S3Storage, photo_id: int, part_numbers: list[int]
) -> list[PresignedPart]:
    photo = _get_photo(session, photo_id)
    upload_id = _require_uploading(photo)
    count = part_count(photo.size_bytes, _part_size(photo))
    out_of_range = sorted(n for n in part_numbers if not 1 <= n <= count)
    if out_of_range:
        raise ValidationFailed(
            f"파트 번호는 1~{count} 범위여야 합니다",
            errors=[{"loc": ["body", "partNumbers"], "msg": f"범위 밖: {out_of_range}", "type": "range"}],
        )
    return [storage.presign_upload_part(photo.original_key, upload_id, n) for n in sorted(part_numbers)]


@dataclass(frozen=True)
class UploadProgress:
    part_size: int
    part_count: int
    parts: list[PartInfo]


def list_uploaded_parts(session: Session, storage: S3Storage, photo_id: int) -> UploadProgress:
    photo = _get_photo(session, photo_id)
    upload_id = _require_uploading(photo)
    try:
        parts = storage.list_parts(photo.original_key, upload_id)
    except UploadNotFound as exc:
        raise Conflict("S3에 업로드가 없습니다(만료·중단됨). 사진을 삭제하고 다시 올리세요") from exc
    part_size = _part_size(photo)
    return UploadProgress(
        part_size=part_size, part_count=part_count(photo.size_bytes, part_size), parts=parts
    )


# ── 완료 ────────────────────────────────────────────────


def _verify_parts(photo: Photo, client_parts: dict[int, str], s3_parts: list[PartInfo]) -> None:
    count = part_count(photo.size_bytes, _part_size(photo))
    expected = set(range(1, count + 1))
    by_number = {p.part_number: p for p in s3_parts}

    missing_client = sorted(expected - set(client_parts))
    unknown = sorted(set(client_parts) - expected)
    missing_s3 = sorted(expected - set(by_number))
    mismatched = sorted(
        n
        for n, etag in client_parts.items()
        if n in by_number and _norm_etag(etag) != _norm_etag(by_number[n].etag)
    )
    wrong_size = sorted(
        n for n, p in by_number.items() if n in expected and p.size != _expected_part_size(photo, n)
    )
    if missing_client or unknown or missing_s3 or mismatched or wrong_size:
        raise ValidationFailed(
            "업로드된 파트가 요청과 일치하지 않습니다. GET .../parts로 확인 후 빠진 파트를 다시 올리세요",
            missing_part_numbers=sorted(set(missing_client) | set(missing_s3)),
            unknown_part_numbers=unknown,
            mismatched_etag_part_numbers=mismatched,
            wrong_size_part_numbers=wrong_size,
        )


def complete_upload(
    session: Session, storage: S3Storage, photo_id: int, client_parts: dict[int, str]
) -> Photo:
    """S3 파트와 대조 → 완료 → 실제 크기 확인 → UPLOADED + 변환 작업 등록.

    멱등: 이미 완료된 사진이면 현재 상태를 그대로 돌려준다. S3 완료 후 커밋 전에 실패했던 경우도
    다시 호출하면 S3 객체를 확인해 마무리한다.
    """
    with transaction(session):
        photo = _get_photo(session, photo_id, for_update=True)  # 동시 완료 요청 직렬화
        if photo.status is not PhotoStatus.UPLOADING:
            return photo
        upload_id = _require_uploading(photo)

        try:
            s3_parts = storage.list_parts(photo.original_key, upload_id)
        except UploadNotFound:
            s3_parts = None

        if s3_parts is None:
            # 업로드가 이미 완료되었는데 DB 반영 전에 실패한 경우 → 객체가 있으면 마무리
            size = storage.head_object(storage.originals_bucket, photo.original_key)
            if size is None:
                raise Conflict("S3에 업로드가 없습니다(만료·중단됨). 사진을 삭제하고 다시 올리세요")
        else:
            _verify_parts(photo, client_parts, s3_parts)
            storage.complete_multipart_upload(photo.original_key, upload_id, s3_parts)
            size = storage.head_object(storage.originals_bucket, photo.original_key)

        if size != photo.size_bytes:
            photo.status = PhotoStatus.FAILED
            photo.error_message = f"업로드된 파일 크기({size})가 신고한 크기({photo.size_bytes})와 다릅니다"
            photo.s3_upload_id = None
            logger.warning("upload size mismatch", extra={"photo_id": photo.id, "actual": size})
            return photo

        photo.status = PhotoStatus.UPLOADED
        photo.s3_upload_id = None
        queue.enqueue(session, photo.id)

    logger.info("upload completed", extra={"photo_id": photo.id, "size_bytes": photo.size_bytes})
    return photo


# ── 조회 · 삭제 ──────────────────────────────────────────


def get_photo(session: Session, photo_id: int) -> Photo:
    return _get_photo(session, photo_id)


def delete_photo_files(storage: S3Storage, photo: Photo) -> None:
    """사진의 S3 데이터 정리: 진행 중 업로드 중단, 원본·파생물 삭제. 여러 번 호출해도 안전."""
    if photo.s3_upload_id:
        storage.abort_multipart_upload(photo.original_key, photo.s3_upload_id)
    if photo.original_key:
        storage.delete_object(storage.originals_bucket, photo.original_key)
    storage.delete_prefix(storage.derived_bucket, derived_prefix(photo.post_id, photo.id))


def delete_photo(session: Session, storage: S3Storage, photo_id: int) -> None:
    """S3 먼저 지우고 레코드를 지운다. S3 삭제가 실패하면 레코드가 남아 다시 시도할 수 있다."""
    with transaction(session):
        photo = _get_photo(session, photo_id, for_update=True)
        delete_photo_files(storage, photo)
        session.delete(photo)
    logger.info("photo deleted", extra={"photo_id": photo_id})
