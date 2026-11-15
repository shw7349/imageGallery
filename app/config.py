"""애플리케이션 설정. 모든 설정은 환경변수(또는 .env)에서만 읽는다."""

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Environment(StrEnum):
    LOCAL = "local"
    TEST = "test"
    PROD = "prod"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    env: Environment = Environment.LOCAL
    log_level: str = "INFO"

    # DB
    database_url: str = Field(description="SQLAlchemy URL, 예: postgresql+psycopg://user:pw@host:5432/db")

    # S3 (로컬은 MinIO). 운영은 endpoint·키를 비우고 IAM 역할을 사용한다.
    s3_endpoint_url: str | None = None
    s3_region: str = "ap-northeast-2"
    s3_access_key_id: SecretStr | None = None
    s3_secret_access_key: SecretStr | None = None
    s3_originals_bucket: str
    s3_derived_bucket: str
    cdn_base_url: str

    # 보안
    session_secret: SecretStr = Field(min_length=32)
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)
    session_max_age_seconds: int = Field(default=12 * 3600, ge=60)
    login_max_failures: int = Field(default=5, ge=1)
    login_lock_seconds: int = Field(default=15 * 60, ge=1)

    # 업로드 (06단계). 파트 크기 16MB, 최대 파일 크기 1GB. S3 최소 파트 크기는 5MB(마지막 파트 제외).
    upload_max_bytes: int = Field(default=1024**3, ge=1)
    upload_part_size: int = Field(default=16 * 1024**2, ge=5 * 1024**2)

    # 프론트 개발용 목업 모드: 아직 구현 전인 엔드포인트가 계약대로 가짜 데이터를 돌려준다(X-Mock 헤더).
    # 운영(ENV=prod)에서는 켤 수 없다.
    mock_api: bool = False

    # 워커·작업 큐 (07단계)
    worker_concurrency: int = Field(default=1, ge=1, le=8)
    worker_poll_seconds: float = Field(default=2.0, gt=0)
    worker_idle_max_seconds: float = Field(default=30.0, gt=0, description="작업이 없을 때 최대 대기")
    worker_reclaim_interval_seconds: float = Field(default=60.0, gt=0)
    # 실패 후 재시도 대기(1분, 5분, 15분). 길이 = 최대 재시도 횟수. 넘으면 FAILED.
    job_retry_delays_seconds: Annotated[list[int], NoDecode] = Field(default_factory=lambda: [60, 300, 900])
    # RUNNING인데 이 시간 동안 끝나지 않으면 워커가 죽은 것으로 보고 회수한다.
    job_stale_after_seconds: int = Field(default=30 * 60, ge=1)
    # 원본 다운로드·변환용 로컬 디렉터리. 300MB 원본 + 파생물이 들어갈 여유가 필요하다.
    work_dir: Path

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        """`CORS_ORIGINS=https://a.com,https://b.com` 형식의 쉼표 구분 문자열을 허용한다."""
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @model_validator(mode="after")
    def _no_mock_in_prod(self) -> "Settings":
        if self.mock_api and self.env is Environment.PROD:
            raise ValueError("MOCK_API는 운영(ENV=prod)에서 켤 수 없습니다")
        return self

    @field_validator("job_retry_delays_seconds", mode="before")
    @classmethod
    def _split_delays(cls, value: object) -> object:
        """`JOB_RETRY_DELAYS_SECONDS=60,300,900` 형식 허용."""
        if isinstance(value, str):
            return [int(v) for v in value.split(",") if v.strip()]
        return value

    @field_validator("s3_endpoint_url", mode="before")
    @classmethod
    def _empty_to_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value


@lru_cache
def get_settings() -> Settings:
    # 필수 값은 환경변수·.env에서 채워지므로 인자 없이 생성한다.
    return Settings()
