"""Private browser-initiated quick save: no HTML fetches, scans or AI calls."""
import pytest
from fastapi.testclient import TestClient
from smartresource.main import app

AUTH = {"X-API-Key": "private-token"}
BASE = "https://media.example.org"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SMARTRESOURCE_DB", str(tmp_path / "quick-save.sqlite"))
    monkeypatch.setenv("SMARTRESOURCE_API_TOKEN", "private-token")
    with TestClient(app) as http:
        yield http


def quick_save(client, url=f"{BASE}/show/1", title="作品一", headers=AUTH):
    return client.post("/api/videos/quick-save", headers=headers, json={
        "url": url, "title": title
    })


def add_site(client, kind="video", enabled=True):
    response = client.post("/api/sites?auto_scan=false", headers=AUTH, json={
        "name": "我的视频来源", "base_url": BASE, "kind": kind,
        "enabled": enabled,
        "search_url_template": f"{BASE}/search?q={{q}}"
    })
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_quick_save_requires_auth(client):
    assert quick_save(client, headers={}).status_code == 401
    assert client.get("/api/sites", headers=AUTH).json()["items"] == []


def test_new_video_host_is_onboarded_without_auto_scan(client, monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("Quick save must not fetch or scan the website")

    monkeypatch.setattr("smartresource.main.enqueue_scan", no_network)
    monkeypatch.setattr("smartresource.main.fetch_html", no_network)
    saved = quick_save(client, title="剧集 A")
    assert saved.status_code == 201, saved.text
    data = saved.json()
    assert data["created_site"] is True
    assert data["site_name"] == "media.example.org"
    assert data["video_url"] == f"{BASE}/show/1"
    assert data["last_url"] == data["video_url"]
    sites = client.get("/api/sites", headers=AUTH).json()["items"]
    assert len(sites) == 1
    assert sites[0]["kind"] == "video"
    assert sites[0]["search_url_template"] is None
    assert sites[0]["scan_status"] == "skipped"


def test_reuse_existing_site_without_overwriting_rules(client):
    sid = add_site(client)
    saved = quick_save(client)
    assert saved.status_code == 201, saved.text
    assert saved.json()["site_id"] == sid
    assert saved.json()["created_site"] is False
    sites = client.get("/api/sites", headers=AUTH).json()["items"]
    assert len(sites) == 1
    assert sites[0]["search_url_template"] == f"{BASE}/search?q={{q}}"


def test_repeat_quick_save_keeps_continue_watching_url(client):
    first = quick_save(client).json()
    vid = first["id"]
    progress = client.put(f"/api/videos/shelf/{vid}/last-page", headers=AUTH, json={
        "url": f"{BASE}/watch/episode-4"
    })
    assert progress.status_code == 200
    again = quick_save(client, title="作品一（重命名）")
    assert again.status_code == 201, again.text
    assert again.json()["id"] == vid
    assert again.json()["last_url"].endswith("/watch/episode-4")
    assert len(client.get("/api/sites", headers=AUTH).json()["items"]) == 1
    assert len(client.get("/api/videos/shelf", headers=AUTH).json()["items"]) == 1


def test_disabled_and_novel_source_are_not_changed(client):
    add_site(client, kind="novel")
    invalid = quick_save(client)
    assert invalid.status_code == 422
    assert len(client.get("/api/sites", headers=AUTH).json()["items"]) == 1
    assert client.get("/api/videos/shelf", headers=AUTH).json()["items"] == []

    edited = client.patch("/api/sites/1", headers=AUTH, json={
        "kind": "video", "enabled": False
    })
    assert edited.status_code == 200
    assert quick_save(client).status_code == 422


@pytest.mark.parametrize("url", [
    "http://localhost/watch/1",
    "http://192.168.0.2/watch/1",
    "https://media.example.org/",
    "https://media.example.org",
    "file:///tmp/video",
    "https://user:pass@media.example.org/watch/1",
    "https://media.example.org:8888/watch/1",
])
def test_quick_save_rejects_unsafe_or_non_video_pages(client, url):
    bad = quick_save(client, url=url)
    assert bad.status_code == 422, (url, bad.text)
    assert client.get("/api/sites", headers=AUTH).json()["items"] == []


def test_whitespace_title_rejected(client):
    bad = quick_save(client, title="   ")
    assert bad.status_code == 422
    assert client.get("/api/sites", headers=AUTH).json()["items"] == []


def test_fragment_is_removed_and_same_host_http_https_reuses_source(client):
    first = quick_save(client, url=f"{BASE}/show/1#episode-3")
    assert first.status_code == 201
    assert first.json()["video_url"] == f"{BASE}/show/1"
    next_one = quick_save(client, url="http://media.example.org/show/2")
    assert next_one.status_code == 201
    assert next_one.json()["site_id"] == first.json()["site_id"]
    assert len(client.get("/api/sites", headers=AUTH).json()["items"]) == 1
