# imageGallery
대용량이미지업로드갤러리

고해상도 사진(장당 약 300MB) 갤러리의 백엔드 API (Python · FastAPI · PostgreSQL · S3).

- 문서 안내: [docs/README.md](docs/README.md)
- 프로젝트 규칙: [.claude/CLAUDE.md](.claude/CLAUDE.md)
- 실행 순서: [docs/guide/roadmap.md](docs/guide/roadmap.md) · 대화용 명령: [docs/guide/commands.md](docs/guide/commands.md)

## 5분 로컬 실행

필요한 것: [uv](https://docs.astral.sh/uv/), Docker Desktop(실행 중), make. 설치는 [docs/guide/setup.md](docs/guide/setup.md).

```bash
uv sync                 # Python 3.12 + 의존성 설치 (.venv)
make up                 # Postgres + MinIO 시작, .env 생성, 버킷 생성
make api                # http://127.0.0.1:8000  (포트 변경: make api API_PORT=8001)
curl localhost:8000/healthz   # {"status":"ok","db":"ok"}
```

- Swagger UI: http://127.0.0.1:8000/docs
- MinIO 콘솔: http://127.0.0.1:9001 (minioadmin / minioadmin, 로컬 전용)

## 자주 쓰는 명령

| 명령 | 설명 |
|---|---|
| `make check` | lint + typecheck + test (커밋 전 필수, `make up` 필요) |
| `make fmt` | 자동 포맷 |
| `make openapi` | `docs/openapi.json` 갱신 |
| `make docker-build` | linux/arm64 이미지 빌드 + pyvips 확인 |
| `make down` | 개발 환경 중지 (데이터 삭제는 `docker compose down -v`) |
| `make help` | 전체 목록 |

`uv run pre-commit install`로 커밋 전 ruff·mypy 자동 실행을 켤 수 있다.

## 프론트 개발용 목업 모드

```bash
make up && make mock-images && make api-mock
```

아직 구현 전인 엔드포인트(공개 목록·상세, 조회수, 원본 다운로드, 통계)가 계약대로 가짜 데이터를 돌려준다
(`X-Mock: true` 헤더). 자세한 내용: `docs/api.md` 8-1.

## 업로드 수동 테스트 (로컬 MinIO)

```bash
uv run python scripts/make_big_file.py --size 300M /tmp/big.tif     # 300MB TIFF 생성
export GALLERY_ADMIN_PASSWORD=...                                   # create-admin으로 만든 계정
uv run python scripts/upload_test.py /tmp/big.tif --post-id 1 --fail-after-parts 5   # 중간에 끊기
uv run python scripts/upload_test.py /tmp/big.tif --post-id 1                        # 이어 올리기
```

## 참고

- macOS 로컬은 `pyvips-binary`(libvips 내장, 개발 의존성)를 쓰고, Docker 이미지는 Debian `libvips42`를 쓴다.
- 로컬 MinIO는 공식 `minio/minio` 이미지의 Docker Hub 배포 중단으로 `cgr.dev/chainguard/minio`를 쓴다.
