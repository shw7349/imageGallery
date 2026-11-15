"""originals 버킷에 CORS(scripts/s3_cors.json)를 적용한다. 운영은 Terraform(13단계)이 같은 규칙을 관리한다.

사용: python scripts/apply_s3_cors.py --origin https://admin.example.com [--origin ...]
MinIO는 버킷별 CORS를 지원하지 않으므로 docker-compose.yml의 MINIO_API_CORS_ALLOW_ORIGIN을 쓴다.
"""

import argparse
import json
import sys
from pathlib import Path

from botocore.exceptions import ClientError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.storage.s3 import S3Storage  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--origin", action="append", required=True, help="관리자 화면 출처(여러 번 가능)")
    args = parser.parse_args()

    rules = json.loads((Path(__file__).parent / "s3_cors.json").read_text(encoding="utf-8"))
    for rule in rules["CORSRules"]:
        rule["AllowedOrigins"] = args.origin

    storage = S3Storage(get_settings())
    try:
        storage.client.put_bucket_cors(Bucket=storage.originals_bucket, CORSConfiguration=rules)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "NotImplemented":
            print("이 S3 호환 서버(MinIO)는 버킷 CORS를 지원하지 않습니다.")
            print("docker-compose.yml의 MINIO_API_CORS_ALLOW_ORIGIN을 쓰세요.")
            return 1
        raise
    print(f"applied CORS to {storage.originals_bucket}: {args.origin}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
