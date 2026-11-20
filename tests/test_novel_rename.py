"""Rename novel favorites without rewinding reading progress or overwriting user titles."""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from smartresource.main import app

AUTH = {"X-API-Key": "private-token"}
BASE = "https://books.example.org"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SMARTRESOURCE_DB", str(tmp_path / "novel-rename.sqlite"))
    monkeypatch.setenv("SMARTRESOURCE_API_TOKEN", "private-token")
    with TestClient(app) as http:
        yield http


def save(client, title="原书名"):
    response = client.post("/api/sites?auto_scan=false", headers=AUTH, json={
        "name": "小说站", "base_url": BASE, "kind": "novel"
    })
    assert response.status_code == 201
    sid = response.json()["id"]
    response = client.post("/api/shelf", headers=AUTH, json={
        "site_id": sid, "book_url": BASE + "/book/1", "title": title
    })
    assert response.status_code == 201, response.text
    return sid, response.json()["id"]


def test_rename_preserves_progress_chapter_and_update_time(client):
    sid, bid = save(client)
    progress = client.put(f"/api/shelf/{bid}/progress", headers=AUTH, json={
        "chapter_url": BASE + "/chapter/27",
        "chapter_title": "第27章",
        "progress": 0.42
    })
    assert progress.status_code == 200
    previous = progress.json()
    renamed = client.patch(f"/api/shelf/{bid}/title", headers=AUTH, json={
        "title": "  我的书名  "
    })
    assert renamed.status_code == 200, renamed.text
    record = renamed.json()
    assert record["title"] == "我的书名"
    assert record["title_custom"] == 1
    assert record["updated_at"] == previous["updated_at"]
    assert record["last_chapter_url"] == previous["last_chapter_url"]
    assert record["last_chapter_title"] == "第27章"
    assert record["progress"] == 0.42

    repeated = client.post("/api/shelf", headers=AUTH, json={
        "site_id": sid, "book_url": BASE + "/book/1", "title": "网站自动标题"
    })
    assert repeated.status_code == 201
    assert repeated.json()["id"] == bid
    assert repeated.json()["title"] == "我的书名"
    assert repeated.json()["last_chapter_url"] == previous["last_chapter_url"]

    quick = client.post("/api/shelf/quick-save", headers=AUTH, json={
        "url": BASE + "/book/1", "title": "网页重新识别的书名"
    })
    assert quick.status_code == 201
    assert quick.json()["title"] == "我的书名"
    assert quick.json()["progress"] == 0.42


def test_before_explicit_rename_normal_add_can_update_title(client):
    sid, bid = save(client)
    repeated = client.post("/api/shelf", headers=AUTH, json={
        "site_id": sid, "book_url": BASE + "/book/1", "title": "书名更新"
    })
    assert repeated.status_code == 201
    assert repeated.json()["title"] == "书名更新"
    assert repeated.json()["title_custom"] == 0
    assert repeated.json()["id"] == bid


@pytest.mark.parametrize("value", ["", "   ", "x" * 201])
def test_invalid_names_rejected_without_changes(client, value):
    _, bid = save(client)
    changed = client.patch(f"/api/shelf/{bid}/title", headers=AUTH,
                           json={"title": value})
    assert changed.status_code == 422
    assert client.get("/api/shelf", headers=AUTH).json()["items"][0]["title"] == "原书名"


def test_rename_needs_token_and_existing_book(client):
    _, bid = save(client)
    assert client.patch(f"/api/shelf/{bid}/title", json={"title": "新名字"}).status_code == 401
    missing = client.patch("/api/shelf/9999/title", headers=AUTH, json={"title": "新名字"})
    assert missing.status_code == 404


def test_existing_database_migrates_without_losing_shelf(tmp_path, monkeypatch):
    path = tmp_path / "older.sqlite"
    monkeypatch.setenv("SMARTRESOURCE_DB", str(path))
    monkeypatch.setenv("SMARTRESOURCE_API_TOKEN", "private-token")
    with sqlite3.connect(path) as conn:
        conn.execute("""CREATE TABLE novel_shelf (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            site_id INTEGER NOT NULL,
            book_url TEXT NOT NULL,
            title TEXT NOT NULL,
            last_chapter_url TEXT NOT NULL,
            last_chapter_title TEXT NOT NULL DEFAULT '',
            progress REAL NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(site_id, book_url)
        )""")
        conn.execute("""INSERT INTO novel_shelf
            (site_id, book_url, title, last_chapter_url, last_chapter_title,
             progress, created_at, updated_at)
            VALUES (1, ?, '旧数据', ?, '第九章', 0.7, '2026-01-01', '2026-01-02')""",
            (BASE + "/book/1", BASE + "/chapter/9"))
    with TestClient(app) as http:
        shelf = http.get("/api/shelf", headers=AUTH)
        assert shelf.status_code == 200
        record = shelf.json()["items"][0]
        assert record["title"] == "旧数据"
        assert record["title_custom"] == 0
        assert record["last_chapter_url"].endswith("/chapter/9")
        assert record["last_chapter_title"] == "第九章"
        assert record["progress"] == 0.7
