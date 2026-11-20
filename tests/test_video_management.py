"""Private video favorites: explicit names and last-page management (no network)."""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from smartresource.main import app

AUTH = {"X-API-Key": "private-token"}
BASE = "https://shows.example.org"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SMARTRESOURCE_DB", str(tmp_path / "manage.sqlite"))
    monkeypatch.setenv("SMARTRESOURCE_API_TOKEN", "private-token")
    with TestClient(app) as http:
        yield http


def save(client, title="第一季"):
    site = client.post("/api/sites?auto_scan=false", headers=AUTH, json={
        "name": "演示站", "base_url": BASE, "kind": "video"
    })
    assert site.status_code == 201
    fav = client.post("/api/videos/shelf", headers=AUTH, json={
        "site_id": site.json()["id"], "title": title,
        "video_url": f"{BASE}/show/1"
    })
    assert fav.status_code == 201
    return site.json()["id"], fav.json()["id"]


def test_video_rename_keeps_page_and_sorting_time(client):
    sid, vid = save(client)
    response = client.put(f"/api/videos/shelf/{vid}/last-page", headers=AUTH,
                          json={"url": f"{BASE}/watch/4"})
    assert response.status_code == 200
    timestamp = response.json()["updated_at"]
    renamed = client.patch(f"/api/videos/shelf/{vid}/title", headers=AUTH,
                           json={"title": "  我的自定义名称  "})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["title"] == "我的自定义名称"
    assert renamed.json()["title_custom"] == 1
    assert renamed.json()["last_url"].endswith("/watch/4")
    assert renamed.json()["updated_at"] == timestamp

    repeated = client.post("/api/videos/shelf", headers=AUTH, json={
        "site_id": sid, "title": "网站原标题",
        "video_url": f"{BASE}/show/1"
    })
    assert repeated.status_code == 201
    assert repeated.json()["title"] == "我的自定义名称"
    assert repeated.json()["last_url"].endswith("/watch/4")


def test_clear_history_keeps_favorite_and_custom_title(client):
    _, vid = save(client)
    assert client.patch(f"/api/videos/shelf/{vid}/title", headers=AUTH,
                        json={"title": "重命名"}).status_code == 200
    client.put(f"/api/videos/shelf/{vid}/last-page", headers=AUTH,
               json={"url": f"{BASE}/watch/5"})
    reset = client.delete(f"/api/videos/shelf/{vid}/last-page", headers=AUTH)
    assert reset.status_code == 200, reset.text
    assert reset.json()["last_url"] == reset.json()["video_url"]
    assert reset.json()["title"] == "重命名"
    shelf = client.get("/api/videos/shelf", headers=AUTH).json()["items"]
    assert len(shelf) == 1
    assert shelf[0]["id"] == vid
    assert shelf[0]["last_url"] == f"{BASE}/show/1"


def test_same_last_page_does_not_change_timestamp(client):
    _, vid = save(client)
    first = client.put(f"/api/videos/shelf/{vid}/last-page", headers=AUTH,
                       json={"url": f"{BASE}/watch/5"}).json()
    next_time = client.put(f"/api/videos/shelf/{vid}/last-page", headers=AUTH,
                           json={"url": f"{BASE}/watch/5"}).json()
    assert next_time["updated_at"] == first["updated_at"]


@pytest.mark.parametrize("title", ["", "   ", "x" * 201])
def test_rename_rejects_invalid_title(client, title):
    _, vid = save(client)
    response = client.patch(f"/api/videos/shelf/{vid}/title", headers=AUTH,
                            json={"title": title})
    assert response.status_code == 422
    assert client.get("/api/videos/shelf", headers=AUTH).json()["items"][0]["title"] == "第一季"


def test_auth_and_missing_video(client):
    _, vid = save(client)
    assert client.patch(f"/api/videos/shelf/{vid}/title", json={"title": "别名"}).status_code == 401
    assert client.delete(f"/api/videos/shelf/{vid}/last-page").status_code == 401
    assert client.patch("/api/videos/shelf/999/title", headers=AUTH,
                        json={"title": "别名"}).status_code == 404
    assert client.delete("/api/videos/shelf/999/last-page", headers=AUTH).status_code == 404


def test_existing_database_adds_title_flag_non_destructively(tmp_path, monkeypatch):
    path = tmp_path / "previous-version.sqlite"
    monkeypatch.setenv("SMARTRESOURCE_DB", str(path))
    monkeypatch.setenv("SMARTRESOURCE_API_TOKEN", "private-token")
    with sqlite3.connect(path) as conn:
        conn.execute("""CREATE TABLE video_library (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            site_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            video_url TEXT NOT NULL,
            last_url TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(site_id, video_url)
        )""")
        conn.execute("""INSERT INTO video_library
            (site_id, title, video_url, last_url, created_at, updated_at)
            VALUES (11, '原有收藏', ?, ?, '2026-01-01', '2026-01-02')""",
            (f"{BASE}/show/1", f"{BASE}/watch/2"))
    with TestClient(app) as http:
        rows = http.get("/api/videos/shelf", headers=AUTH).json()["items"]
        assert len(rows) == 1
        assert rows[0]["title"] == "原有收藏"
        assert rows[0]["title_custom"] == 0
        assert rows[0]["last_url"] == f"{BASE}/watch/2"
