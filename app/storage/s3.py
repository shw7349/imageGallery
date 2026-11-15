"""S3 접근 래퍼. 앱에서 boto3를 쓰는 유일한 곳이다(로컬은 MinIO, 운영은 AWS S3).

- 파일 바이트를 메모리에 통째로 올리지 않는다. 업로드는 Presigned URL(브라우저 → S3 직행),
  다운로드·업로드는 디스크 경로 기반(upload_file/download_file, 스트리밍).
- Presigned URL 전체를 로그에 남기지 않는다(서명 포함).
"""

import logging
import mimetypes
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.config import Settings

if TYPE_CHECKING:
    from mypy_boto3_s3.client import S3Client
else:  # 런타임에는 스텁 패키지가 없다(개발 의존성)
    S3Client = Any

logger = logging.getLogger(__name__)

PART_URL_TTL = timedelta(hours=1)
_DELETE_BATCH = 1000


class StorageError(Exception):
    """S3 호출 실패(네트워크·권한 등). 서비스가 도메인 예외로 바꾸거나 그대로 올린다."""


class UploadNotFound(StorageError):
    """멀티파트 업로드가 없다(완료·중단·만료됨)."""


@dataclass(frozen=True)
class PartInfo:
    part_number: int
    etag: str
    size: int


@dataclass(frozen=True)
class PresignedPart:
    part_number: int
    url: str
    expires_at: datetime


def _code(exc: ClientError) -> str:
    return str(exc.response.get("Error", {}).get("Code", ""))


class S3Storage:
    def __init__(self, settings: Settings, client: S3Client | None = None) -> None:
        self.originals_bucket = settings.s3_originals_bucket
        self.derived_bucket = settings.s3_derived_bucket
        self.client: S3Client = client or self._make_client(settings)

    @staticmethod
    def _make_client(settings: Settings) -> S3Client:
        config = Config(
            signature_version="s3v4",
            retries={"max_attempts": 5, "mode": "standard"},
            # 기본 체크섬 자동 계산을 끈다: Presigned 파트 PUT·MinIO 호환성 유지
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
            s3={"addressing_style": "path"} if settings.s3_endpoint_url else {},
            max_pool_connections=32,
        )
        kwargs: dict[str, Any] = {}  # Any: boto3 client 인자
        if settings.s3_access_key_id and settings.s3_secret_access_key:
            kwargs["aws_access_key_id"] = settings.s3_access_key_id.get_secret_value()
            kwargs["aws_secret_access_key"] = settings.s3_secret_access_key.get_secret_value()
        client: S3Client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            region_name=settings.s3_region,
            config=config,
            **kwargs,
        )
        return client

    # ── 멀티파트 업로드 ─────────────────────────────────────

    def create_multipart_upload(self, key: str, content_type: str) -> str:
        try:
            res = self.client.create_multipart_upload(
                Bucket=self.originals_bucket, Key=key, ContentType=content_type
            )
        except (ClientError, BotoCoreError) as exc:
            raise StorageError(f"create_multipart_upload failed: {key}") from exc
        return res["UploadId"]

    def presign_upload_part(self, key: str, upload_id: str, part_number: int) -> PresignedPart:
        expires_at = datetime.now(UTC) + PART_URL_TTL
        url = self.client.generate_presigned_url(
            "upload_part",
            Params={
                "Bucket": self.originals_bucket,
                "Key": key,
                "UploadId": upload_id,
                "PartNumber": part_number,
            },
            ExpiresIn=int(PART_URL_TTL.total_seconds()),
            HttpMethod="PUT",
        )
        return PresignedPart(part_number=part_number, url=url, expires_at=expires_at)

    def list_parts(self, key: str, upload_id: str) -> list[PartInfo]:
        """올라간 파트 전체(페이지네이션 처리, 파트 번호 오름차순)."""
        parts: list[PartInfo] = []
        marker = 0
        while True:
            try:
                res = self.client.list_parts(
                    Bucket=self.originals_bucket,
                    Key=key,
                    UploadId=upload_id,
                    PartNumberMarker=marker,
                    MaxParts=1000,
                )
            except ClientError as exc:
                if _code(exc) == "NoSuchUpload":
                    raise UploadNotFound(key) from exc
                raise StorageError(f"list_parts failed: {key}") from exc
            except BotoCoreError as exc:
                raise StorageError(f"list_parts failed: {key}") from exc
            for p in res.get("Parts", []):
                parts.append(PartInfo(part_number=p["PartNumber"], etag=p["ETag"], size=p["Size"]))
            if not res.get("IsTruncated"):
                break
            marker = int(res.get("NextPartNumberMarker", 0))
        parts.sort(key=lambda p: p.part_number)
        return parts

    def complete_multipart_upload(self, key: str, upload_id: str, parts: list[PartInfo]) -> None:
        try:
            self.client.complete_multipart_upload(
                Bucket=self.originals_bucket,
                Key=key,
                UploadId=upload_id,
                MultipartUpload={"Parts": [{"PartNumber": p.part_number, "ETag": p.etag} for p in parts]},
            )
        except ClientError as exc:
            if _code(exc) == "NoSuchUpload":
                raise UploadNotFound(key) from exc
            raise StorageError(f"complete_multipart_upload failed: {key} ({_code(exc)})") from exc
        except BotoCoreError as exc:
            raise StorageError(f"complete_multipart_upload failed: {key}") from exc

    def abort_multipart_upload(self, key: str, upload_id: str) -> None:
        """이미 없는 업로드면 조용히 넘어간다(멱등)."""
        try:
            self.client.abort_multipart_upload(Bucket=self.originals_bucket, Key=key, UploadId=upload_id)
        except ClientError as exc:
            if _code(exc) != "NoSuchUpload":
                raise StorageError(f"abort_multipart_upload failed: {key}") from exc
        except BotoCoreError as exc:
            raise StorageError(f"abort_multipart_upload failed: {key}") from exc

    # ── 객체 ────────────────────────────────────────────

    def head_object(self, bucket: str, key: str) -> int | None:
        """객체 크기(바이트). 없으면 None."""
        try:
            res = self.client.head_object(Bucket=bucket, Key=key)
        except ClientError as exc:
            if _code(exc) in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise StorageError(f"head_object failed: {key}") from exc
        except BotoCoreError as exc:
            raise StorageError(f"head_object failed: {key}") from exc
        return int(res["ContentLength"])

    def delete_object(self, bucket: str, key: str) -> None:
        try:
            self.client.delete_object(Bucket=bucket, Key=key)
        except (ClientError, BotoCoreError) as exc:
            raise StorageError(f"delete_object failed: {key}") from exc

    def _iter_keys(self, bucket: str, prefix: str) -> Iterator[str]:
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                yield obj["Key"]

    def delete_prefix(self, bucket: str, prefix: str) -> int:
        """접두사 아래 객체를 모두 지우고 지운 개수를 반환한다."""
        if not prefix or not prefix.endswith("/"):
            raise ValueError(f"prefix는 '/'로 끝나야 합니다(전체 삭제 방지): {prefix!r}")
        deleted = 0
        batch: list[str] = []
        try:
            for key in self._iter_keys(bucket, prefix):
                batch.append(key)
                if len(batch) == _DELETE_BATCH:
                    deleted += self._delete_batch(bucket, batch)
                    batch = []
            if batch:
                deleted += self._delete_batch(bucket, batch)
        except (ClientError, BotoCoreError) as exc:
            raise StorageError(f"delete_prefix failed: {prefix}") from exc
        return deleted

    def _delete_batch(self, bucket: str, keys: list[str]) -> int:
        res = self.client.delete_objects(
            Bucket=bucket, Delete={"Objects": [{"Key": k} for k in keys], "Quiet": True}
        )
        errors = res.get("Errors", [])
        if errors:
            raise StorageError(f"delete_objects: {len(errors)} errors, first={errors[0].get('Key')}")
        return len(keys)

    def download_file(self, bucket: str, key: str, dest: Path) -> None:
        """디스크로 스트리밍 다운로드(멀티파트 병렬, 메모리 사용 일정)."""
        try:
            self.client.download_file(bucket, key, str(dest))
        except (ClientError, BotoCoreError) as exc:
            raise StorageError(f"download_file failed: {key}") from exc

    def upload_directory(
        self,
        local_dir: Path,
        bucket: str,
        prefix: str,
        *,
        cache_control: str | None = None,
        content_type_for: Callable[[Path], str] | None = None,
        concurrency: int = 8,
        attempts: int = 3,
    ) -> int:
        """디렉터리 전체를 prefix 아래로 올린다(딥줌 타일 수천 개용). 실패 파일은 지수 백오프로 재시도."""
        files = [p for p in local_dir.rglob("*") if p.is_file()]

        def guess(path: Path) -> str:
            if content_type_for:
                return content_type_for(path)
            return mimetypes.guess_type(path.name)[0] or "application/octet-stream"

        def upload(path: Path) -> None:
            key = prefix + path.relative_to(local_dir).as_posix()
            extra: dict[str, str] = {"ContentType": guess(path)}
            if cache_control:
                extra["CacheControl"] = cache_control
            for attempt in range(1, attempts + 1):
                try:
                    self.client.upload_file(str(path), bucket, key, ExtraArgs=extra)
                    return
                except (ClientError, BotoCoreError) as exc:
                    if attempt == attempts:
                        raise StorageError(f"upload failed after {attempts} attempts: {key}") from exc
                    time.sleep(0.5 * 2 ** (attempt - 1))

        with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
            futures = [pool.submit(upload, f) for f in files]
            for future in as_completed(futures):
                future.result()
        return len(files)

    def abort_all_uploads(self, prefix: str = "") -> int:
        """진행 중인 멀티파트 업로드를 모두 중단한다(테스트·정리용)."""
        aborted = 0
        res = self.client.list_multipart_uploads(Bucket=self.originals_bucket, Prefix=prefix)
        for up in res.get("Uploads", []):
            self.abort_multipart_upload(up["Key"], up["UploadId"])
            aborted += 1
        return aborted
