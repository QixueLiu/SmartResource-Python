import pytest
from fastapi.testclient import TestClient
from smartresource.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SMARTRESOURCE_DB", str(tmp_path / "shelf.sqlite"))
    monkeypatch.setenv("SMARTRESOURCE_API_TOKEN", "top-secret")
    with TestClient(app) as client:
        yield client


H = {"X-API-Key": "top-secret"}


def create_site(client):
    response = client.post("/api/sites?auto_scan=false", headers=H, json={
        "name": "Novel site", "base_url": "https://books.example.org", "kind": "novel"
    })
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_shelf_token_required_and_upsert_preserves_progress(client):
    sid = create_site(client)
    assert client.get("/api/shelf").status_code == 401
    base = {"site_id": sid, "book_url": "https://books.example.org/book/1", "title": "第一个故事"}
    first = client.post("/api/shelf", headers=H, json=base)
    assert first.status_code == 201, first.text
    bid = first.json()["id"]
    saved = client.put(f"/api/shelf/{bid}/progress", headers=H, json={
        "chapter_url": "https://books.example.org/chapter/14",
        "chapter_title": "第14章", "progress": 0.56
    })
    assert saved.status_code == 200, saved.text
    assert saved.json()["progress"] == 0.56
    twice = client.post("/api/shelf", headers=H, json={**base, "title": "修改后的书名"})
    assert twice.status_code == 201
    assert twice.json()["id"] == bid
    assert twice.json()["last_chapter_url"].endswith("/chapter/14")
    assert twice.json()["progress"] == 0.56
    items = client.get("/api/shelf", headers=H).json()["items"]
    assert len(items) == 1 and items[0]["title"] == "修改后的书名"


def test_invalid_cross_site_url_and_progress_bounds(client):
    sid = create_site(client)
    bad = client.post("/api/shelf", headers=H, json={
        "site_id": sid, "book_url": "http://127.0.0.1/admin", "title": "bad"
    })
    assert bad.status_code == 422
    good = client.post("/api/shelf", headers=H, json={
        "site_id": sid, "book_url": "https://books.example.org/book/1", "title": "作品"
    })
    bid = good.json()["id"]
    invalid = client.put(f"/api/shelf/{bid}/progress", headers=H, json={
        "chapter_url": "https://other.example.org/x",
        "chapter_title": "第2章", "progress": 0.3
    })
    assert invalid.status_code == 422
    bounds = client.put(f"/api/shelf/{bid}/progress", headers=H, json={
        "chapter_url": "https://books.example.org/chapter/2",
        "chapter_title": "第2章", "progress": 2.0
    })
    assert bounds.status_code == 422
    current = client.get("/api/shelf", headers=H).json()["items"][0]
    assert current["last_chapter_url"] == "https://books.example.org/book/1"


def test_remove_and_missing_book(client):
    sid = create_site(client)
    response = client.post("/api/shelf", headers=H, json={
        "site_id": sid, "book_url": "https://books.example.org/book/1", "title": "样书"
    })
    bid = response.json()["id"]
    assert client.delete(f"/api/shelf/{bid}", headers=H).status_code == 200
    assert client.delete(f"/api/shelf/{bid}", headers=H).status_code == 404
    assert client.get("/api/shelf", headers=H).json()["items"] == []


def test_missing_or_disabled_site_rejected(client):
    missing = client.post("/api/shelf", headers=H, json={
        "site_id": 404, "book_url": "https://books.example.org/book/1", "title": "x"
    })
    assert missing.status_code == 422
    sid = create_site(client)
    assert client.patch(f"/api/sites/{sid}", headers=H, json={"enabled": False}).status_code == 200
    disabled = client.post("/api/shelf", headers=H, json={
        "site_id": sid, "book_url": "https://books.example.org/book/1", "title": "x"
    })
    assert disabled.status_code == 422
