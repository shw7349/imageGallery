from fastapi.testclient import TestClient

from tests.conftest import AdminClient

URL = "/api/admin/categories"


def _create(admin: AdminClient, name: str, sort_order: int = 0) -> dict[str, object]:
    res = admin.http.post(URL, json={"name": name, "sortOrder": sort_order}, headers=admin.headers())
    assert res.status_code == 201, res.text
    body: dict[str, object] = res.json()
    return body


def test_create_and_list_in_sort_order(admin: AdminClient) -> None:
    _create(admin, "인물", 2)
    _create(admin, "풍경", 1)
    _create(admin, "  거리  ", 1)  # 공백은 잘린다
    res = admin.http.get(URL)
    assert res.status_code == 200
    assert [c["name"] for c in res.json()] == ["풍경", "거리", "인물"]
    assert set(res.json()[0]) == {"id", "name", "sortOrder"}


def test_duplicate_name_is_409(admin: AdminClient) -> None:
    _create(admin, "풍경")
    res = admin.http.post(URL, json={"name": "풍경"}, headers=admin.headers())
    assert res.status_code == 409
    assert res.headers["content-type"] == "application/problem+json"


def test_update(admin: AdminClient) -> None:
    cat = _create(admin, "풍경")
    res = admin.http.put(f"{URL}/{cat['id']}", json={"name": "자연", "sortOrder": 5}, headers=admin.headers())
    assert res.status_code == 200
    assert res.json() == {"id": cat["id"], "name": "자연", "sortOrder": 5}


def test_update_keeping_same_name_is_ok_but_taking_other_name_is_409(admin: AdminClient) -> None:
    a = _create(admin, "A")
    _create(admin, "B")
    assert admin.http.put(f"{URL}/{a['id']}", json={"name": "A"}, headers=admin.headers()).status_code == 200
    assert admin.http.put(f"{URL}/{a['id']}", json={"name": "B"}, headers=admin.headers()).status_code == 409


def test_delete(admin: AdminClient) -> None:
    cat = _create(admin, "풍경")
    assert admin.http.delete(f"{URL}/{cat['id']}", headers=admin.headers()).status_code == 204
    assert admin.http.get(URL).json() == []


def test_delete_with_posts_is_409(admin: AdminClient) -> None:
    cat = _create(admin, "풍경")
    admin.http.post("/api/admin/posts", json={"categoryId": cat["id"], "title": "t"}, headers=admin.headers())
    res = admin.http.delete(f"{URL}/{cat['id']}", headers=admin.headers())
    assert res.status_code == 409
    assert "게시물" in res.json()["detail"]


def test_not_found_is_404(admin: AdminClient) -> None:
    assert admin.http.put(f"{URL}/999", json={"name": "x"}, headers=admin.headers()).status_code == 404
    assert admin.http.delete(f"{URL}/999", headers=admin.headers()).status_code == 404


def test_validation(admin: AdminClient) -> None:
    for body in ({"name": ""}, {"name": "   "}, {"name": "x" * 51}, {"name": "ok", "sortOrder": -1}, {}):
        res = admin.http.post(URL, json=body, headers=admin.headers())
        assert res.status_code == 422, body
        assert res.json()["code"] == "validation_failed"


def test_requires_login(client: TestClient) -> None:
    assert client.get(URL).status_code == 401
    assert client.post(URL, json={"name": "x"}).status_code == 401


def test_requires_csrf(admin: AdminClient) -> None:
    assert admin.http.post(URL, json={"name": "x"}).status_code == 403
