"""애플리케이션 설정. 모든 설정은 환경변수(또는 .env)에서만 읽는다."""

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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
    # 관리자 API(/api/admin/*)를 허용할 Host 목록(관리자 전용 도메인). 다른 Host로 오면 404.
    # 비우면 제한 없음(로컬 개발용). 운영(ENV=prod)에서는 반드시 설정해야 한다.
    admin_hosts: Annotated[list[str], NoDecode] = Field(default_factory=list)
    session_max_age_seconds: int = Field(default=12 * 3600, ge=60)
    # 로그인 잠금: 같은 IP에서 login_lock_seconds 안에 login_max_failures번 실패하면 그 IP만 잠근다.
    # 사용자명 전체 잠금은 여러 IP를 쓰는 대입 공격용 안전장치(임계값이 높아 남이 관리자를 쉽게 잠글 수 없다).
    login_max_failures: int = Field(default=5, ge=1)
    login_max_failures_per_username: int = Field(default=100, ge=1)
    login_lock_seconds: int = Field(default=15 * 60, ge=1)

    # 업로드 (06단계). 파트 크기 16MB, 최대 파일 크기 1GB. S3 최소 파트 크기는 5MB(마지막 파트 제외).
    upload_max_bytes: int = Field(default=1024**3, ge=1)
    upload_part_size: int = Field(default=16 * 1024**2, ge=5 * 1024**2)

    # 조회수·통계 (10단계). 일별 집계 기준 시간대, IP당 분당 조회수 기록 요청 한도, 중복 제거 기록 보관 일수
    stats_timezone: str = "Asia/Seoul"
    views_rate_per_minute: int = Field(default=60, ge=1)
    view_dedupe_retention_days: int = Field(default=30, ge=2)
    # 원본 다운로드(11단계): 방문자(vid)당 1시간 최대 횟수
    downloads_per_hour: int = Field(default=20, ge=1)
    # 쿠키를 지우면 방문자 제한이 초기화되므로 IP 기준 제한과 사이트 전체 하루 상한을 함께 둔다.
    # 하루 상한은 전송 요금 폭주를 막는 차단기다(300MB × 500회 = 150GB).
    downloads_per_hour_per_ip: int = Field(default=40, ge=1)
    downloads_per_day_total: int = Field(default=500, ge=1)

    # 요청 본문 최대 크기. 사진은 S3로 직접 올리므로 API가 받는 본문은 JSON뿐이다
    # (가장 큰 정상 요청: 파트 1만 개의 업로드 완료 ≈ 700KB).
    max_request_body_bytes: int = Field(default=1024 * 1024, ge=1024)

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
    # libvips 제한(08단계): 스레드 수와 연산 캐시. 워커 메모리 상한을 예측 가능하게 한다.
    vips_concurrency: int = Field(default=2, ge=1, le=16)
    vips_cache_max_mb: int = Field(default=100, ge=0)

    @field_validator("admin_hosts", mode="before")
    @classmethod
    def _split_hosts(cls, value: object) -> object:
        """`ADMIN_HOSTS=admin.example.com,admin2.example.com` 형식. 소문자로 맞추고 포트는 뺀다."""
        if isinstance(value, str):
            value = [item for item in value.split(",")]
        if isinstance(value, list):
            return [str(h).strip().lower().split(":")[0] for h in value if str(h).strip()]
        return value

    @model_validator(mode="after")
    def _prod_requirements(self) -> "Settings":
        if self.env is not Environment.PROD:
            return self
        if not self.admin_hosts:
            raise ValueError("운영(ENV=prod)에서는 ADMIN_HOSTS(관리자 전용 도메인)를 설정해야 합니다")
        secret = self.session_secret.get_secret_value()
        if "change-me" in secret.lower() or len(set(secret)) < 12:
            raise ValueError(
                "운영(ENV=prod)에서는 예시·단순한 SESSION_SECRET을 쓸 수 없습니다. "
                'python3 -c "import secrets; print(secrets.token_urlsafe(48))"로 새로 만드세요'
            )
        return self

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        """`CORS_ORIGINS=https://a.com,https://b.com` 형식의 쉼표 구분 문자열을 허용한다."""
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("stats_timezone")
    @classmethod
    def _valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"알 수 없는 시간대: {value}") from exc
        return value

    @field_validator("job_retry_delays_seconds", mode="before")
    @classmethod
    def _split_delays(cls, value: object) -> object:
        """`JOB_RETRY_DELAYS_SECONDS=60,300,900` 형식의 쉼표 구분 문자열을 허용한다."""
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
