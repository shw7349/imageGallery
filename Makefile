# Gallery Backend — 개발 명령. `make help`로 목록 확인.
SHELL := /bin/bash
.DEFAULT_GOAL := help

API_PORT ?= 8000
UV ?= uv

.PHONY: help env up down migrate revision api mock-images demo-data worker lint fmt typecheck test check openapi docker-build

help: ## 명령 목록
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

env: ## .env가 없으면 .env.example을 복사
	@test -f .env || { cp .env.example .env; echo "created .env from .env.example"; }

up: env ## 개발 환경 시작 (Postgres + MinIO, 개발·테스트 버킷 생성)
	docker compose up -d --wait postgres minio
	$(UV) run python scripts/dev_create_buckets.py

down: ## 개발 환경 중지 (데이터 유지. 삭제는 docker compose down -v)
	docker compose down

migrate: ## DB 마이그레이션 적용 (02단계부터)
	$(UV) run alembic upgrade head

revision: ## 새 마이그레이션 리비전: make revision m="설명"
	@test -n "$(m)" || { echo 'usage: make revision m="설명"'; exit 1; }
	$(UV) run alembic revision --autogenerate -m "$(m)"

api: ## API 서버 실행 (리로드)
	$(UV) run uvicorn --factory app.main:create_app --reload --host 127.0.0.1 --port $(API_PORT) --no-access-log

mock-images: ## 목업 이미지(썸네일·미리보기·딥줌 타일)를 로컬 MinIO에 생성
	$(UV) run python scripts/seed_mock_images.py

demo-data: ## 데모 게시물 36개(목업 이미지 사용)를 로컬 DB에 넣기 (다시 만들기: make demo-data ARGS=--reset)
	$(UV) run python scripts/seed_demo_data.py $(ARGS)

worker: ## 사진 변환 워커 실행
	$(UV) run python -m app.jobs.worker

lint: ## ruff 검사 + 포맷 확인
	$(UV) run ruff check .
	$(UV) run ruff format --check .

fmt: ## 자동 포맷 + 고칠 수 있는 lint 수정
	$(UV) run ruff check --fix .
	$(UV) run ruff format .

typecheck: ## mypy strict
	$(UV) run mypy --strict app

test: ## pytest (make up 필요)
	$(UV) run pytest

check: lint typecheck test ## lint + typecheck + test

openapi: ## docs/openapi.json + docs/api.md 생성 (API 변경 시 필수)
	$(UV) run python scripts/export_openapi.py docs/openapi.json
	$(UV) run python scripts/gen_api_md.py docs/openapi.json docs/api.md

docker-build: ## arm64 이미지 빌드 + pyvips 확인
	docker build --platform linux/arm64 -t gallery:dev .
	docker run --rm --platform linux/arm64 gallery:dev python -c "import pyvips; print(pyvips.version(0), pyvips.version(1))"
