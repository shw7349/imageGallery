"""로컬 MinIO에 개발·테스트용 버킷을 만든다 (`make up`에서 호출). 운영 버킷은 Terraform이 만든다."""

import sys
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError, EndpointConnectionError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402

TEST_BUCKETS = ("gallery-test-originals", "gallery-test-derived")


def main() -> int:
    settings = get_settings()
    if not settings.s3_endpoint_url:
        print("S3_ENDPOINT_URL이 비어 있습니다. 로컬(MinIO) 전용 스크립트입니다.")
        return 1
    s3 = boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        region_name=settings.s3_region,
        aws_access_key_id=settings.s3_access_key_id.get_secret_value() if settings.s3_access_key_id else None,
        aws_secret_access_key=(
            settings.s3_secret_access_key.get_secret_value() if settings.s3_secret_access_key else None
        ),
    )
    for attempt in range(30):
        try:
            existing = {b["Name"] for b in s3.list_buckets().get("Buckets", [])}
            break
        except (EndpointConnectionError, ClientError):
            if attempt == 29:
                raise
            time.sleep(1)
    for name in (settings.s3_originals_bucket, settings.s3_derived_bucket, *TEST_BUCKETS):
        if name in existing:
            print(f"exists  {name}")
        else:
            s3.create_bucket(Bucket=name)
            print(f"created {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
