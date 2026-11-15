# imageGallery — 대용량 이미지 갤러리·게시판 백엔드

관리자가 글과 고해상도 사진(장당 수백 MB, RAW 포함)을 올리면, 방문자가 로그인 없이 목록·상세·딥줌(확대) 뷰어로
보는 사이트의 **백엔드 API**입니다. 프론트엔드는 포함하지 않으며, 이 저장소를 받아 그대로 띄운 뒤 원하는 화면을
붙여 쓰면 됩니다.

- 언어·프레임워크: Python 3.12 · FastAPI · PostgreSQL 16 · S3(로컬은 MinIO)
- 상태: 기능 구현 완료(로컬에서 전체 흐름 동작, 테스트 250여 개). **운영 배포 구성은 아직 없습니다** → [운영에 올리기 전에](#운영에-올리기-전에)

## 목차

1. [무엇을 할 수 있나](#무엇을-할-수-있나)
2. [구조](#구조)
3. [빠른 시작 (10분)](#빠른-시작-10분)
4. [데모 데이터로 바로 보기](#데모-데이터로-바로-보기)
5. [API 명세](#api-명세)
6. [프론트엔드 연동 예시](#프론트엔드-연동-예시)
7. [환경변수](#환경변수)
8. [명령 모음](#명령-모음)
9. [디렉터리 구조](#디렉터리-구조)
10. [내 서비스에 맞게 바꾸기](#내-서비스에-맞게-바꾸기)
11. [운영에 올리기 전에](#운영에-올리기-전에)

## 무엇을 할 수 있나

| 대상 | 기능 |
|---|---|
| 관리자 | 로그인(계정은 서버에서만 생성), 카테고리·게시물 작성/수정/삭제, 공개·숨김 전환, 사진 순서·대표 사진 지정 |
| 사진 업로드 | 브라우저 → S3 **직접 업로드**(멀티파트, 16MB 파트). 끊겨도 **이어 올리기**. 최대 1GB. JPEG·TIFF·PNG·RAW(arw, cr2, cr3, nef, raf, orf, rw2, dng) |
| 자동 변환 | 업로드가 끝나면 워커가 썸네일(600px)·미리보기(3840px)·딥줌 타일(DZI)을 만들고 EXIF를 추출(GPS는 제외). 300MB 파일 처리 시 메모리 약 200MB |
| 방문자 | 카테고리·게시물 목록(검색·정렬·페이지네이션), 상세, 조회수(방문자당 하루 1회) |
| 통계 | 일별 조회수(전체·게시물별), 기간별 인기 게시물 |

설계 원칙

- **큰 파일은 API 서버를 지나가지 않습니다.** 업로드는 S3로 직행하고, 변환은 디스크 스트리밍으로 합니다.
- **추가 인프라가 없습니다.** 작업 큐·조회수 중복 제거·로그인 잠금을 모두 PostgreSQL로 처리합니다(Redis·SQS 없음).
- **API와 워커는 같은 코드·같은 Docker 이미지**이고 실행 명령만 다릅니다.

## 구조

```
[관리자 브라우저] ─① 업로드 시작·파트 URL 요청─▶ [API (FastAPI)] ─▶ [PostgreSQL]
        └─② 파트를 S3로 직접 PUT ─▶ [S3 originals · 비공개]
                                       │ ③ 완료 → 변환 작업 등록(photo_job)
                                       ▼
                        [워커  python -m app.jobs.worker]
                        원본 → 디스크 → RAW 현상 → EXIF → 썸네일·미리보기·딥줌 타일
                                       ▼
                        [S3 derived] ◀── [CDN] ◀── 방문자        (로컬: MinIO가 CDN 역할)
```

## 빠른 시작 (10분)

### 준비물

| 도구 | 용도 | 설치(macOS) |
|---|---|---|
| [uv](https://docs.astral.sh/uv/) | Python·의존성 관리 | `brew install uv` |
| Docker Desktop | Postgres·MinIO 실행 | `brew install --cask docker` |
| make | 명령 실행 | 기본 설치됨 |
| exiftool | 사진 EXIF 추출(워커·테스트) | `brew install exiftool` |

Linux에서는 `libvips42`(또는 `libvips`)와 `libimage-exiftool-perl`을 패키지 관리자로 설치하세요.
macOS는 libvips를 따로 설치하지 않아도 됩니다(개발 의존성에 포함).

### 실행

```bash
git clone https://github.com/shw7349/imageGallery.git
cd imageGallery
git switch develop         # 개발 브랜치

uv sync                    # Python 3.12 + 의존성 설치(.venv)
make up                    # Postgres + MinIO 시작, .env 생성, 버킷 생성
make migrate               # DB 테이블 생성
uv run python -m app.cli create-admin --username admin    # 관리자 계정(비밀번호 12자 이상, 프롬프트 입력)

make api                   # API 서버  → http://127.0.0.1:8000
make worker                # (다른 터미널) 사진 변환 워커
```

확인

```bash
curl localhost:8000/healthz          # {"status":"ok","db":"ok"}
open http://127.0.0.1:8000/docs      # Swagger UI — 모든 API를 브라우저에서 실행해 볼 수 있습니다
```

- 8000번 포트가 사용 중이면 `make api API_PORT=8001`
- MinIO 콘솔: http://127.0.0.1:9001 (minioadmin / minioadmin, 로컬 전용)
- 전체 검사: `make check` (lint + 타입 검사 + 테스트)

## 데모 데이터로 바로 보기

실제 사진을 올리지 않아도 화면을 만들 수 있도록, 목업 이미지를 쓰는 공개 게시물 36개를 넣을 수 있습니다.

```bash
make mock-images     # 목업 이미지 12장(썸네일·미리보기·딥줌 타일)을 MinIO에 생성
make demo-data       # 위 이미지를 쓰는 게시물 36개·사진 106장을 DB에 넣기 (제목이 "[데모] "로 시작)

curl "localhost:8000/api/posts?size=3"          # 공개 목록
curl -G "localhost:8000/api/posts" --data-urlencode "q=한강"   # 검색(curl은 한글을 직접 인코딩해 줘야 함)
```

- 목록·상세·검색·조회수가 실제와 똑같이 동작합니다(가짜 응답 모드가 아닙니다).
- 데모 게시물만 지우고 다시 만들기: `make demo-data ARGS=--reset`

딥줌(확대) 확인 — 로컬 MinIO는 `http://localhost:5173` 출처만 허용하므로 그 포트로 뷰어를 띄웁니다.

```bash
cd scripts && python3 -m http.server 5173
# http://localhost:5173/viewer.html?dzi=http://localhost:9000/gallery-derived/mock/3/photo.dzi
```

실제 업로드를 시험하려면(로컬 MinIO, 300MB 파일):

```bash
uv run python scripts/make_big_file.py --size 300M /tmp/big.tif
export GALLERY_ADMIN_PASSWORD=...        # create-admin으로 만든 비밀번호
uv run python scripts/upload_test.py /tmp/big.tif --post-id 1 --fail-after-parts 5   # 중간에 끊기
uv run python scripts/upload_test.py /tmp/big.tif --post-id 1                        # 이어 올리기
```

## API 명세

- **전체 명세(기계용): [`docs/openapi.json`](docs/openapi.json)** — Postman·코드 생성기에 그대로 넣을 수 있습니다.
- **Swagger UI**: 서버 실행 후 `/docs`
- 사람이 읽는 문서: `make openapi`를 실행하면 `docs/api.md`가 생성됩니다(필드·응답 표 포함).

### 공통 규칙

| 항목 | 규칙 |
|---|---|
| 형식 | 요청·응답 모두 JSON, 필드 이름은 **camelCase** |
| 시간 | ISO 8601(UTC). 날짜만 있는 값은 `YYYY-MM-DD` |
| 목록 | `{ "items": [...], "page": 1, "size": 20, "total": 42 }` — `page`는 1부터, `size` 최대 100 |
| 요청 ID | 모든 응답에 `X-Request-ID` 헤더(문제 추적용) |
| 본문 크기 | 최대 1MB (사진은 API로 보내지 않습니다) |

### 인증 (관리자 API만)

1. `POST /api/admin/auth/login` `{ "username", "password" }` → `{ "username", "csrfToken" }` + 세션 쿠키
   (`gallery_admin`, HttpOnly·Secure·SameSite=Lax, 12시간)
2. 이후 요청은 쿠키가 자동으로 붙습니다(`fetch`는 `credentials: "include"`).
3. **변경 요청(POST·PUT·PATCH·DELETE)에는 `X-CSRF-Token: <csrfToken>` 헤더가 필수**입니다.
4. 새로고침으로 토큰을 잃으면 `GET /api/admin/auth/me`로 다시 받습니다(401이면 로그인 화면으로).
5. 같은 IP에서 15분 안에 5번 실패하면 그 IP가 잠깁니다(429, `Retry-After`).

### 오류 형식

모든 오류는 RFC 7807 `application/problem+json`입니다. 분기는 `status`와 `code`로 하고, `title`·`detail`은
사용자에게 보여 줘도 되는 한국어 문장입니다.

```json
{
  "type": "https://gallery.example/problems/conflict",
  "title": "현재 상태에서 처리할 수 없습니다",
  "status": 409,
  "code": "conflict",
  "detail": "처리가 끝난(READY) 사진이 1장 이상 있어야 공개할 수 있습니다",
  "instance": "/api/admin/posts/1/status"
}
```

| status | code | 의미 |
|---|---|---|
| 401 | `authentication_required`, `invalid_credentials` | 로그인 필요 / 아이디·비밀번호 불일치 |
| 403 | `forbidden` | CSRF 토큰 누락·불일치 |
| 404 | `not_found` | 없음(방문자 API는 비공개 게시물도 404) |
| 409 | `conflict` | 현재 상태에서 불가(중복 이름, 공개 조건 미충족 등) |
| 413 | `payload_too_large` | 요청 본문이 1MB 초과 |
| 422 | `validation_failed` | 입력값 오류. `errors: [{ loc, msg, type }]` 포함 |
| 429 | `too_many_requests` | 요청 과다. `Retry-After` 헤더(초) |
| 500 | `internal_error` | 서버 오류 |
| 503 | `storage_unavailable` | 파일 저장소(S3) 일시 장애. 잠시 후 재시도 |

### 방문자 API (로그인 없음)

| 메서드 · 경로 | 설명 |
|---|---|
| `GET /api/categories` | 카테고리 목록 |
| `GET /api/posts?category=&q=&page=&size=&sort=` | 공개 게시물 목록. `sort`: `published`(기본) \| `views` |
| `GET /api/posts/{id}` | 상세: 본문, 사진별 `thumbUrl`·`previewUrl`·`dziUrl`·`exif` |
| `POST /api/posts/{id}/views` | 조회수 기록(방문자당 하루 1회, 항상 204) |

- 공개 조건: 게시물이 `PUBLISHED`이고 처리 완료(`READY`) 사진이 1장 이상.
- 이미지 URL은 CDN 기준 절대 URL로 내려옵니다. 프론트에서 조립하지 말고 그대로 쓰세요.
- 원본 파일은 방문자에게 제공하지 않습니다(다운로드 API 없음). 원본은 비공개 버킷에 보관하고 재처리에만 씁니다.
- 딥줌은 [OpenSeadragon](https://openseadragon.github.io/)에 `dziUrl`을 넘기면 됩니다.

### 관리자 API (`/api/admin/*`, 로그인 필요)

| 메서드 · 경로 | 설명 |
|---|---|
| `POST /auth/login` · `POST /auth/logout` · `GET /auth/me` | 로그인 · 로그아웃(모든 세션 종료) · 현재 관리자와 CSRF 토큰 |
| `GET/POST /categories` · `PUT/DELETE /categories/{id}` | 카테고리(게시물이 있으면 삭제 409) |
| `GET /posts?status=&categoryId=&page=&size=&sort=` | 게시물 목록(모든 상태). `sort`: `created` \| `published` \| `views` |
| `POST /posts` · `GET/PUT/DELETE /posts/{id}` | 작성(초안 `DRAFT`) · 상세 · 수정(전체 교체) · 삭제(사진·파일 포함) |
| `PATCH /posts/{id}/status` | `DRAFT` / `PUBLISHED` / `HIDDEN` 전환(공개는 READY 사진 1장 이상 필요) |
| `PUT /posts/{id}/photos/order` | 사진 순서 저장(첫 사진이 대표) |
| `POST /posts/{id}/photos` | 업로드 시작 → `photoId`, `partSize`, `partCount` |
| `POST /photos/{id}/parts` | 파트 번호들 → Presigned PUT URL(1시간) |
| `GET /photos/{id}/parts` | 이미 올라간 파트(이어 올리기용) |
| `POST /photos/{id}/complete` | 업로드 완료 → 변환 작업 등록 |
| `GET /photos/{id}` · `DELETE /photos/{id}` | 처리 상태 조회 · 업로드 중단/삭제 |
| `POST /photos/{id}/retry` | 변환 실패(`FAILED`) 사진 재처리 |
| `GET /stats/views?from=&to=&postId=` | 일별 조회수(최대 366일). `postId`를 주면 그 게시물만 |
| `GET /stats/top?days=7\|30\|90` | 기간별 조회수 상위 20 게시물 |

상태값

| 대상 | 흐름 |
|---|---|
| 게시물 | `DRAFT`(작성 중) → `PUBLISHED`(공개) ↔ `HIDDEN`(숨김) |
| 사진 | `UPLOADING` → `UPLOADED` → `PROCESSING` → `READY` 또는 `FAILED`(`errorMessage` 확인 후 retry) |

### 사진 업로드 절차

```
① POST /api/admin/posts/{postId}/photos   { filename, sizeBytes, contentType }
      → 201 { photoId, partSize, partCount }
② POST /api/admin/photos/{photoId}/parts  { partNumbers: [1,2,3,4] }
      → { urls: [{ partNumber, url, expiresAt }] }
③ 각 url에 파트 바이트를 PUT (S3로 직행, 동시 3~4개) → 응답 헤더 ETag 저장
      파트 N = 바이트 [(N-1)·partSize, min(N·partSize, sizeBytes))
④ POST /api/admin/photos/{photoId}/complete  { parts: [{ partNumber, etag }, ...] }
      → { status: "UPLOADED" }  (서버가 변환 작업 등록)
⑤ GET /api/admin/photos/{photoId} 를 3~5초 간격으로 조회 → PROCESSING → READY
```

- ③의 PUT은 본문에 파트 바이트만 보내면 됩니다. 별도 헤더(인증·`Content-Type`)는 필요 없습니다.
- ETag는 응답 헤더 값을 **따옴표까지 그대로** ④에 보냅니다.
- 끊겼을 때: `GET /photos/{photoId}/parts`로 올라간 파트를 확인하고 빠진 번호만 ②~③ 후 ④.
- ④는 같은 내용으로 다시 보내도 안전합니다.

## 프론트엔드 연동 예시

```js
const API = "";   // 같은 도메인이면 빈 문자열. 로컬 개발은 "http://127.0.0.1:8000" + CORS_ORIGINS 설정

// 방문자: 목록과 상세
const list = await fetch(`${API}/api/posts?size=20&sort=published`).then(r => r.json());
const post = await fetch(`${API}/api/posts/${list.items[0].id}`).then(r => r.json());
await fetch(`${API}/api/posts/${post.id}/views`, { method: "POST", credentials: "include" });

// 관리자: 로그인 후 글 작성
const login = await fetch(`${API}/api/admin/auth/login`, {
  method: "POST", credentials: "include",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ username, password }),
}).then(r => r.json());
const admin = (path, method, body) => fetch(`${API}/api/admin${path}`, {
  method, credentials: "include",
  headers: { "Content-Type": "application/json", "X-CSRF-Token": login.csrfToken },
  body: body && JSON.stringify(body),
}).then(r => r.status === 204 ? null : r.json());

const created = await admin("/posts", "POST", { categoryId: 1, title: "제목", body: "본문" });

// 관리자: 사진 업로드(파일 하나)
const up = await admin(`/posts/${created.id}/photos`, "POST",
  { filename: file.name, sizeBytes: file.size, contentType: file.type || "application/octet-stream" });
const numbers = Array.from({ length: up.partCount }, (_, i) => i + 1);
const { urls } = await admin(`/photos/${up.photoId}/parts`, "POST", { partNumbers: numbers });
const parts = [];
for (const { partNumber, url } of urls) {            // 실제로는 3~4개씩 동시에
  const blob = file.slice((partNumber - 1) * up.partSize, partNumber * up.partSize);
  const res = await fetch(url, { method: "PUT", body: blob });   // 본문은 파트 바이트만
  parts.push({ partNumber, etag: res.headers.get("ETag") });
}
await admin(`/photos/${up.photoId}/complete`, "POST", { parts });
// 이후 GET /photos/{id}로 READY를 확인하고 PATCH /posts/{id}/status { status: "PUBLISHED" }
```

- 파트 URL은 한 번에 100개까지 요청할 수 있습니다. 파트가 더 많으면 나눠서 요청하세요.
- 로그인 쿠키는 `SameSite=Lax`라서 **관리자 화면과 API가 같은 도메인**이어야 합니다(다른 사이트에서 호출하면 쿠키가 붙지 않습니다).
- 쿠키가 `Secure`이므로 HTTPS가 필요합니다. `http://localhost`는 브라우저가 예외로 허용합니다.

## 환경변수

`make up`이 `.env.example`을 `.env`로 복사합니다. 로컬은 그대로 써도 됩니다.

| 변수 | 기본값(로컬) | 설명 |
|---|---|---|
| `ENV` | `local` | `local` / `test` / `prod`. `prod`는 아래 필수 검증이 켜집니다 |
| `DATABASE_URL` | `postgresql+psycopg://gallery:gallery@localhost:5432/gallery` | PostgreSQL 주소 |
| `S3_ENDPOINT_URL` | `http://localhost:9000` | MinIO 주소. AWS S3는 비웁니다 |
| `S3_ACCESS_KEY_ID` · `S3_SECRET_ACCESS_KEY` | `minioadmin` | AWS에서 IAM 역할을 쓰면 비웁니다 |
| `S3_REGION` | `ap-northeast-2` | |
| `S3_ORIGINALS_BUCKET` · `S3_DERIVED_BUCKET` | `gallery-originals` · `gallery-derived` | 원본(비공개) · 파생 이미지(CDN 공개) |
| `CDN_BASE_URL` | `http://localhost:9000/gallery-derived` | 응답의 이미지 URL 앞부분 |
| `SESSION_SECRET` | 예시값 | 32자 이상. **운영에서는 새로 생성**: `python3 -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `ADMIN_HOSTS` | (비움) | 관리자 API를 허용할 도메인(쉼표 구분). 다른 도메인에서는 `/api/admin/*`이 404. **운영 필수** |
| `CORS_ORIGINS` | `http://localhost:3000,http://localhost:5173` | 다른 출처의 프론트 개발 서버 |
| `UPLOAD_MAX_BYTES` | `1073741824` (1GB) | 사진 한 장의 최대 크기 |
| `VIEWS_RATE_PER_MINUTE` | `60` | IP당 조회수 기록 요청 한도 |
| `STATS_TIMEZONE` | `Asia/Seoul` | 일별 통계의 날짜 기준 |
| `WORKER_CONCURRENCY` | `1` | 워커 동시 처리 수(메모리에 맞춰 조정) |
| `JOB_RETRY_DELAYS_SECONDS` | `60,300,900` | 변환 실패 시 재시도 대기 |
| `WORK_DIR` | `/tmp/gallery-work` | 변환용 임시 디렉터리(원본의 3~8배 여유 필요) |
| `VIPS_CONCURRENCY` · `VIPS_CACHE_MAX_MB` | `2` · `100` | 이미지 처리 스레드·캐시 제한 |

전체 목록과 설명은 [`.env.example`](.env.example)과 [`app/config.py`](app/config.py)에 있습니다.

## 명령 모음

| 명령 | 설명 |
|---|---|
| `make up` / `make down` | Postgres + MinIO 시작 / 중지(데이터 삭제는 `docker compose down -v`) |
| `make migrate` | DB 마이그레이션 적용 |
| `make revision m="설명"` | 모델 변경 후 새 마이그레이션 생성 |
| `make api` / `make worker` | API 서버 / 사진 변환 워커 |
| `make check` | lint + 타입 검사 + 테스트 (`make up` 필요) |
| `make fmt` | 자동 포맷 |
| `make openapi` | `docs/openapi.json`, `docs/api.md` 갱신(API를 바꿨다면 필수) |
| `make mock-images` / `make demo-data` | 목업 이미지 / 데모 게시물 생성 |
| `make docker-build` | linux/arm64 이미지 빌드 |
| `uv run python -m app.cli create-admin --username X` | 관리자 계정 생성 |
| `uv run python -m app.cli reset-password --username X` | 비밀번호 재설정(기존 세션 모두 종료) |

Docker 이미지 하나로 세 가지를 실행합니다.

```bash
docker run ... gallery:dev                                  # API (기본 명령)
docker run ... gallery:dev python -m app.jobs.worker        # 워커
docker run ... gallery:dev alembic upgrade head             # 마이그레이션
```

## 디렉터리 구조

```
app/
├─ main.py            앱 팩토리(미들웨어, 라우터 등록)
├─ config.py          환경변수 설정(pydantic-settings)
├─ api/               라우터: 요청 검증·응답 변환만
│  ├─ admin/          관리자 API (전체에 로그인 + CSRF 자동 적용)
│  └─ public/         방문자 API
├─ services/          비즈니스 로직과 트랜잭션
├─ models/            SQLAlchemy 모델   ·  schemas/  요청·응답 스키마
├─ storage/s3.py      S3 접근(boto3는 여기서만 사용)
├─ jobs/              작업 큐(queue.py) · 워커(worker.py) · 사진 처리(processing.py)
└─ imaging/           RAW 현상 · EXIF · 썸네일/딥줌 생성(DB·S3를 모르는 순수 함수)
migrations/           Alembic 마이그레이션
tests/                실제 Postgres·MinIO로 도는 테스트
scripts/              데모 데이터, 업로드 테스트 클라이언트, 벤치마크, 뷰어
docs/openapi.json     API 명세
```

## 내 서비스에 맞게 바꾸기

| 하고 싶은 것 | 바꿀 곳 |
|---|---|
| **사진 없는 글도 공개**(일반 게시판) | 지금은 READY 사진이 1장 이상이어야 공개됩니다. `app/services/public.py`의 공개 조건과 `app/services/posts.py`의 `change_status`를 고치세요 |
| 썸네일·미리보기 크기, 화질 | `app/imaging/derivatives.py`의 `THUMB_SIZE`, `PREVIEW_SIZE`, 화질(Q) 값 |
| 허용 파일 형식·최대 크기 | `app/services/uploads.py`의 `CONTENT_TYPES`, 환경변수 `UPLOAD_MAX_BYTES` |
| 게시물에 필드 추가(태그 등) | `app/models/content.py` → `make revision m="..."` → `app/schemas/` → `app/services/` → `make openapi` |
| 관리자 여러 명 | `create-admin`을 여러 번 실행하면 됩니다(권한 구분은 없습니다) |
| 댓글·회원 가입 | 없습니다. 방문자는 익명이라는 전제로 만들어져 있어 새로 설계해야 합니다 |

코드를 바꾼 뒤에는 `make check`가 통과하는지 확인하세요. API를 바꿨다면 `make openapi`로 명세를 갱신해야
계약 테스트가 통과합니다.

## 운영에 올리기 전에

로컬에서는 완전히 동작하지만, **운영 배포에 필요한 구성은 이 저장소에 아직 없습니다.**

| 항목 | 상태 |
|---|---|
| AWS 인프라 코드(S3, CloudFront, EC2 — Terraform) | 없음 |
| 운영용 Docker Compose, HTTPS 프록시, CI/CD | 없음 |
| DB 백업·복원 | 없음 |
| 감시·알림 | 없음(헬스 체크는 DB 연결만 확인) |

직접 운영 환경을 만들 때 반드시 지킬 것

1. `ENV=prod`로 실행하고 `SESSION_SECRET`(새로 생성)과 `ADMIN_HOSTS`를 설정합니다. 빠지면 서버가 시작되지 않습니다.
2. **프록시 뒤라면 실제 방문자 IP를 전달**해야 합니다(uvicorn `--proxy-headers --forwarded-allow-ips`).
   빠지면 모든 요청이 같은 IP로 보여 로그인 잠금·요청 제한이 전체에 걸립니다.
3. 관리자 도메인의 `Host` 헤더가 API까지 그대로 전달돼야 `ADMIN_HOSTS` 제한이 동작합니다.
4. 원본 버킷은 비공개, 파생 버킷은 CDN을 통해서만 공개합니다. 원본 버킷 CORS는 관리자 도메인의 `PUT`을 허용하고
   `ETag` 헤더를 노출해야 합니다([`scripts/s3_cors.json`](scripts/s3_cors.json)).
5. 원본 버킷에 "미완료 멀티파트 업로드 7일 후 삭제" 수명 주기를 겁니다.
6. Postgres를 정기적으로 백업합니다.

참고 사양: 4GB 메모리 서버 1대(예: AWS t4g.medium)에서 API·워커·Postgres를 함께 돌리는 것을 기준으로 만들었습니다.
