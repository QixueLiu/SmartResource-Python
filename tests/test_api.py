import pytest
from fastapi.testclient import TestClient
from smartresource.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SMARTRESOURCE_DB", str(tmp_path / "test.db"))
    monkeypatch.setenv("SMARTRESOURCE_API_TOKEN", "test-token")
    with TestClient(app) as api:
        yield api


def auth():
    return {"X-API-Key": "test-token"}


def test_health_and_auth(client):
    assert client.get("/api/health").json()["status"] == "ok"
    assert client.get("/api/sites").status_code == 401
    assert client.get("/api/sites", headers=auth()).json() == {"items": []}


def test_empty_search(client):
    r = client.get("/api/search?q=hello", headers=auth())
    assert r.status_code == 200
    assert r.json() == {"items": [], "warnings": [], "searched_sites": 0}


def test_reject_private_network(client):
    r = client.post("/api/sites", headers=auth(), json={"name": "bad", "base_url": "http://127.0.0.1:8000"})
    assert r.status_code == 422


def test_import_text_and_toggle_delete(client):
    r = client.post("/api/sites/import-text", headers=auth(), json={
        "text": "Novels | https://books.example.com/page\nhttps://books.example.com/other\nhttps://videos.example.org",
        "kind": "novel"
    })
    assert r.status_code == 200, r.text
    assert r.json()["created"] == 2
    assert r.json()["existing"] == 1
    sites = client.get("/api/sites", headers=auth()).json()["items"]
    assert len(sites) == 2
    site_id = sites[0]["id"]
    r = client.patch(f"/api/sites/{site_id}", headers=auth(), json={"enabled": False, "kind": "video"})
    assert r.status_code == 200 and not r.json()["enabled"]
    assert r.json()["kind"] == "video"
    assert client.delete(f"/api/sites/{site_id}", headers=auth()).status_code == 200
    assert len(client.get("/api/sites", headers=auth()).json()["items"]) == 1


def test_compare_unknown_site(client):
    r = client.post("/api/novels/compare", headers=auth(), json={
        "candidates": [{"site_id": 999, "url": "https://example.com/book/1"}]
    })
    assert r.status_code == 200
    assert "error" in r.json()["items"][0]
