"""Quick book bookmarks: no third-party fetches, no auto-scans, preserve old shelf state."""
import pytest
from fastapi.testclient import TestClient

from smartresource.main import app

AUTH = {"X-API-Key": "private-token"}
ROOT = "https://novels.example.org"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SMARTRESOURCE_DB", str(tmp_path / "novel-quick-save.sqlite"))
    monkeypatch.setenv("SMARTRESOURCE_API_TOKEN", "private-token")
    with TestClient(app) as http:
        yield http


def quick(client, url=ROOT + "/book/abc", title="我的小说", headers=AUTH):
    return client.post("/api/shelf/quick-save", headers=headers, json={
        "title": title, "url": url
    })


def site(client, kind="novel", enabled=True):
    response = client.post("/api/sites?auto_scan=false", headers=AUTH, json={
        "name": "我的书源", "base_url": ROOT, "kind": kind, "enabled": enabled,
        "search_url_template": ROOT + "/search?q={q}"
    })
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_requires_auth_and_does_not_create_site(client):
    assert quick(client, headers={}).status_code == 401
    assert client.get("/api/sites", headers=AUTH).json()["items"] == []


def test_new_host_creates_novel_site_without_scans_or_fetches(client, monkeypatch):
    def forbidden(*a, **kw):
        raise AssertionError("quick save must not request an external page")
    monkeypatch.setattr("smartresource.main.fetch_html", forbidden)
    monkeypatch.setattr("smartresource.main.enqueue_scan", forbidden)
    saved = quick(client, title="测试作品")
    assert saved.status_code == 201, saved.text
    record = saved.json()
    assert record["site_name"] == "novels.example.org"
    assert record["created_site"] is True
    assert record["book_url"] == ROOT + "/book/abc"
    assert record["last_chapter_url"] == record["book_url"]
    assert record["progress"] == 0
    sites = client.get("/api/sites", headers=AUTH).json()["items"]
    assert len(sites) == 1
    assert sites[0]["kind"] == "novel"
    assert sites[0]["search_url_template"] is None
    assert sites[0]["scan_status"] == "skipped"


def test_existing_site_reused_and_search_rules_preserved(client):
    sid = site(client)
    record = quick(client).json()
    assert record["site_id"] == sid
    assert record["site_name"] == "我的书源"
    assert record["created_site"] is False
    sites = client.get("/api/sites", headers=AUTH).json()["items"]
    assert len(sites) == 1
    assert sites[0]["search_url_template"] == ROOT + "/search?q={q}"


def test_repeat_does_not_clear_progress_or_rename(client):
    book = quick(client, title="初次收藏").json()
    bid = book["id"]
    response = client.put(f"/api/shelf/{bid}/progress", headers=AUTH, json={
        "chapter_url": ROOT + "/book/abc/chapter/15",
        "chapter_title": "第十五章",
        "progress": 0.75
    })
    assert response.status_code == 200, response.text
    updated = response.json()
    again = quick(client, title="网页标题改变")
    assert again.status_code == 201
    value = again.json()
    assert value["id"] == bid
    assert value["title"] == "初次收藏"
    assert value["last_chapter_url"] == ROOT + "/book/abc/chapter/15"
    assert value["last_chapter_title"] == "第十五章"
    assert value["progress"] == 0.75
    assert value["updated_at"] == updated["updated_at"]
    assert len(client.get("/api/shelf", headers=AUTH).json()["items"]) == 1


def test_existing_video_and_disabled_sources_are_not_mutated(client):
    site(client, kind="video")
    assert quick(client).status_code == 422
    assert client.get("/api/shelf", headers=AUTH).json()["items"] == []
    edit = client.patch("/api/sites/1", headers=AUTH, json={
        "kind": "novel", "enabled": False
    })
    assert edit.status_code == 200
    assert quick(client).status_code == 422


@pytest.mark.parametrize("url", [
    "http://localhost/book/1",
    "http://127.0.0.1/book/1",
    "http://192.168.0.9/book/1",
    "file:///tmp/book.txt",
    "https://novels.example.org/",
    "https://novels.example.org",
    "https://novels.example.org:4555/book/1",
    "https://user:secret@novels.example.org/book/1",
])
def test_bad_addresses_rejected_without_creating_site(client, url):
    bad = quick(client, url=url)
    assert bad.status_code == 422, (url, bad.text)
    assert client.get("/api/sites", headers=AUTH).json()["items"] == []


def test_empty_title_rejected_and_fragment_removed(client):
    assert quick(client, title="   ").status_code == 422
    saved = quick(client, url=ROOT + "/book/abc#chapter-1")
    assert saved.status_code == 201, saved.text
    assert saved.json()["book_url"] == ROOT + "/book/abc"


def test_reuse_host_across_http_and_https(client):
    first = quick(client).json()
    second = quick(client, url="http://novels.example.org/book/second")
    assert second.status_code == 201, second.text
    assert second.json()["site_id"] == first["site_id"]
    assert len(client.get("/api/sites", headers=AUTH).json()["items"]) == 1
