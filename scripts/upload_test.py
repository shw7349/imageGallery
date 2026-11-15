"""실제 업로드 클라이언트(수동 테스트·E2E용). 브라우저가 할 일을 그대로 한다.

    export GALLERY_ADMIN_PASSWORD=...
    uv run python scripts/upload_test.py big.tif --post-id 1
    uv run python scripts/upload_test.py big.tif --post-id 1 --fail-after-parts 5   # 5파트 후 강제 중단
    uv run python scripts/upload_test.py big.tif --post-id 1                        # 다시 실행 → 이어 올리기

- 파트 단위(partSize)로만 파일을 읽는다. 파일 전체를 메모리에 올리지 않는다.
- 진행 상태(photoId)를 --state-dir에 저장해 다시 실행하면 GET .../parts로 이어 올린다.
"""

import argparse
import hashlib
import json
import os
import sys
import threading
import time
from collections.abc import Iterator
from concurrent.futures import FIRST_EXCEPTION, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

URL_BATCH = 100


class Aborted(Exception):
    """--fail-after-parts로 의도적으로 중단."""


@dataclass
class Api:
    http: httpx.Client
    csrf: str

    def call(self, method: str, path: str, **kw: Any) -> httpx.Response:
        headers = kw.pop("headers", {})
        if method != "GET":
            headers["X-CSRF-Token"] = self.csrf
        return self.http.request(method, path, headers=headers, **kw)


def login(base_url: str, username: str, password: str) -> Api:
    http = httpx.Client(base_url=base_url, timeout=60)
    res = http.post("/api/admin/auth/login", json={"username": username, "password": password})
    if res.status_code != 200:
        sys.exit(f"로그인 실패: {res.status_code} {res.text}")
    # 세션 쿠키는 Secure라서 http(로컬)에서는 쿠키 저장소가 보내지 않는다 → 헤더로 직접 붙인다.
    cookie = res.headers["set-cookie"].split(";", 1)[0]
    http.headers["Cookie"] = cookie
    return Api(http=http, csrf=res.json()["csrfToken"])


def state_path(state_dir: Path, base_url: str, post_id: int, file: Path) -> Path:
    st = file.stat()
    ident = f"{base_url}|{post_id}|{file.resolve()}|{st.st_size}|{int(st.st_mtime)}"
    return state_dir / f"{hashlib.sha256(ident.encode()).hexdigest()[:16]}.json"


def check(res: httpx.Response, *expected: int) -> dict[str, Any]:
    if res.status_code not in expected:
        sys.exit(f"API 오류 {res.request.method} {res.request.url.path}: {res.status_code} {res.text}")
    body: dict[str, Any] = res.json() if res.content else {}
    return body


def start_or_resume(
    api: Api, state_file: Path, post_id: int, file: Path
) -> tuple[int, int, int, dict[int, str]]:
    """(photoId, partSize, partCount, 이미 올라간 {파트: ETag})"""
    if state_file.exists():
        photo_id = json.loads(state_file.read_text())["photoId"]
        res = api.call("GET", f"/api/admin/photos/{photo_id}")
        if res.status_code == 200 and res.json()["status"] == "UPLOADING":
            parts = check(api.call("GET", f"/api/admin/photos/{photo_id}/parts"), 200)
            have = {p["partNumber"]: p["etag"] for p in parts["parts"]}
            print(f"이어 올리기: photoId={photo_id}, {len(have)}/{parts['partCount']} 파트 완료됨")
            return photo_id, parts["partSize"], parts["partCount"], have
        if res.status_code == 200:
            sys.exit(f"이미 업로드가 끝난 사진입니다: photoId={photo_id} status={res.json()['status']}")
        print(f"저장된 업로드(photoId={photo_id})가 없어 새로 시작합니다")

    body = check(
        api.call(
            "POST",
            f"/api/admin/posts/{post_id}/photos",
            json={
                "filename": file.name,
                "sizeBytes": file.stat().st_size,
                "contentType": "application/octet-stream",
            },
        ),
        201,
    )
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps({"photoId": body["photoId"]}))
    print(f"업로드 시작: photoId={body['photoId']} partSize={body['partSize']} partCount={body['partCount']}")
    return body["photoId"], body["partSize"], body["partCount"], {}


CHUNK = 1024 * 1024


def part_length(file_size: int, part_size: int, number: int) -> int:
    return min(part_size, file_size - (number - 1) * part_size)


def stream_part(file: Path, part_size: int, number: int, length: int) -> Iterator[bytes]:
    """파트를 1MB씩 읽어 흘려보낸다. 파트 전체도 메모리에 올리지 않는다.

    (bytes로 통째로 넘기면 httpx 내부 복사로 메모리가 파일 크기만큼 늘어나는 것을 측정으로 확인함)
    """
    with file.open("rb") as f:
        f.seek((number - 1) * part_size)
        left = length
        while left:
            chunk = f.read(min(left, CHUNK))
            if not chunk:
                raise RuntimeError(f"파일이 예상보다 짧습니다(파트 {number})")
            left -= len(chunk)
            yield chunk


def presign(api: Api, photo_id: int, numbers: list[int]) -> dict[int, str]:
    urls: dict[int, str] = {}
    for i in range(0, len(numbers), URL_BATCH):
        body = check(
            api.call(
                "POST",
                f"/api/admin/photos/{photo_id}/parts",
                json={"partNumbers": numbers[i : i + URL_BATCH]},
            ),
            200,
        )
        urls |= {u["partNumber"]: u["url"] for u in body["urls"]}
    return urls


def upload_parts(
    api: Api,
    photo_id: int,
    file: Path,
    part_size: int,
    missing: list[int],
    *,
    concurrency: int,
    fail_after: int | None,
) -> dict[int, str]:
    urls = presign(api, photo_id, missing)
    etags: dict[int, str] = {}
    lock = threading.Lock()
    stop = threading.Event()
    started = time.monotonic()
    sent_bytes = 0
    s3 = httpx.Client(timeout=300)
    file_size = file.stat().st_size

    def put(number: int) -> None:
        nonlocal sent_bytes
        if stop.is_set():
            return
        length = part_length(file_size, part_size, number)
        for attempt in range(1, 4):
            # Content-Length만 지정하고 바이트를 흘려보낸다(S3 Presigned PUT은 chunked 전송 불가).
            res = s3.put(
                urls[number],
                content=stream_part(file, part_size, number, length),
                headers={"Content-Length": str(length)},
            )
            if res.status_code == 200:
                break
            if res.status_code == 403:  # URL 만료 → 재발급
                urls[number] = presign(api, photo_id, [number])[number]
            if attempt == 3:
                raise RuntimeError(f"파트 {number} 업로드 실패: {res.status_code} {res.text[:200]}")
            time.sleep(attempt)
        with lock:
            etags[number] = res.headers["ETag"]
            sent_bytes += length
            done = len(etags)
            rate = sent_bytes / max(time.monotonic() - started, 1e-6) / 1024**2
            print(f"  파트 {number:>5} 완료  ({done}/{len(missing)}, {rate:.1f} MB/s)")
            if fail_after is not None and done >= fail_after:
                # 새 파트는 시작하지 않는다. 이미 전송 중인 파트(최대 concurrency-1개)는 끝까지 간다
                stop.set()

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(put, n) for n in missing]
        finished, _ = wait(futures, return_when=FIRST_EXCEPTION)
        for f in finished:
            f.result()
    s3.close()
    if stop.is_set() and len(etags) < len(missing):
        raise Aborted(f"--fail-after-parts {fail_after}: {len(etags)}개 파트 후 중단(전송 중이던 파트 포함)")
    return etags


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--post-id", type=int, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--username", default="admin")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--fail-after-parts", type=int, default=None, metavar="K")
    parser.add_argument("--state-dir", type=Path, default=Path.home() / ".cache" / "gallery-upload")
    args = parser.parse_args()

    password = os.environ.get("GALLERY_ADMIN_PASSWORD")
    if not password:
        sys.exit("환경변수 GALLERY_ADMIN_PASSWORD를 설정하세요")

    api = login(args.base_url, args.username, password)
    state_file = state_path(args.state_dir, args.base_url, args.post_id, args.path)
    photo_id, part_size, part_count, have = start_or_resume(api, state_file, args.post_id, args.path)

    missing = [n for n in range(1, part_count + 1) if n not in have]
    t0 = time.monotonic()
    try:
        have |= upload_parts(
            api,
            photo_id,
            args.path,
            part_size,
            missing,
            concurrency=args.concurrency,
            fail_after=args.fail_after_parts,
        )
    except Aborted as exc:
        print(f"중단됨: {exc}. 같은 명령을 다시 실행하면 이어 올립니다.")
        return 3

    body = check(
        api.call(
            "POST",
            f"/api/admin/photos/{photo_id}/complete",
            json={"parts": [{"partNumber": n, "etag": e} for n, e in sorted(have.items())]},
        ),
        200,
    )
    state_file.unlink(missing_ok=True)
    print(f"완료: photoId={photo_id} status={body['status']} ({time.monotonic() - t0:.1f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
