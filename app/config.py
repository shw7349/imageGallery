"""애플리케이션 설정. 모든 설정은 환경변수(또는 .env)에서만 읽는다."""

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, SecretStr, field_validator
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

    # 워커
    worker_concurrency: int = Field(default=1, ge=1, le=8)
    # 원본 다운로드·변환용 로컬 디렉터리. 300MB 원본 + 파생물이 들어갈 여유가 필요하다.
    work_dir: Path

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        """`CORS_ORIGINS=https://a.com,https://b.com` 형식의 쉼표 구분 문자열을 허용한다."""
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
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
