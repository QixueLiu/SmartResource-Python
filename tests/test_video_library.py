"""Fast, offline API regression tests for private video favorites.

These tests do not fetch or play third-party videos.
"""
import pytest
from fastapi.testclient import TestClient

from smartresource.main import app

AUTH = {"X-API-Key": "private-token"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SMARTRESOURCE_DB", str(tmp_path / "video.sqlite"))
    monkeypatch.setenv("SMARTRESOURCE_API_TOKEN", "private-token")
    with TestClient(app) as http:
        yield http


def site(client, domain="movies.example.org", kind="video"):
    response = client.post("/api/sites?auto_scan=false", headers=AUTH, json={
        "name": "Video site", "base_url": f"https://{domain}", "kind": kind
    })
    assert response.status_code == 201, response.text
    return response.json()["id"]


def favorite(client, site_id, url="https://movies.example.org/show/1", title="作品甲"):
    return client.post("/api/videos/shelf", headers=AUTH, json={
        "site_id": site_id, "video_url": url, "title": title
    })


def test_video_shelf_requires_auth_for_all_operations(client):
    sid = site(client)
    assert client.get("/api/videos/shelf").status_code == 401
    assert client.post("/api/videos/shelf", json={
        "site_id": sid, "video_url": "https://movies.example.org/show/1",
        "title": "A"
    }).status_code == 401
    item = favorite(client, sid).json()
    assert client.put(f"/api/videos/shelf/{item['id']}/last-page", json={
        "url": "https://movies.example.org/watch/1"
    }).status_code == 401
    assert client.delete(f"/api/videos/shelf/{item['id']}").status_code == 401


def test_save_and_continue_last_page_is_preserved_on_repeat_favorite(client):
    sid = site(client)
    first = favorite(client, sid)
    assert first.status_code == 201, first.text
    video_id = first.json()["id"]
    watched = client.put(f"/api/videos/shelf/{video_id}/last-page", headers=AUTH, json={
        "url": "https://movies.example.org/watch/episode-3"
    })
    assert watched.status_code == 200, watched.text
    assert watched.json()["last_url"].endswith("episode-3")
    repeated = favorite(client, sid, title="作品甲（已更名）")
    assert repeated.status_code == 201, repeated.text
    assert repeated.json()["id"] == video_id
    assert repeated.json()["last_url"].endswith("episode-3")
    items = client.get("/api/videos/shelf", headers=AUTH).json()["items"]
    assert len(items) == 1
    assert items[0]["title"] == "作品甲（已更名）"
    assert items[0]["site_name"] == "Video site"


def test_cross_site_and_private_addresses_rejected(client):
    sid = site(client)
    for url in ("https://other.example.org/show/1", "http://127.0.0.1/admin",
                "http://localhost/", "file:///data/private"):
        bad = favorite(client, sid, url=url)
        assert bad.status_code == 422, (url, bad.text)
    item = favorite(client, sid)
    assert item.status_code == 201
    item_id = item.json()["id"]
    for url in ("https://other.example.org/watch/1", "http://192.168.1.1/",
                "https://movies.example.org:8080/watch/1"):
        bad = client.put(f"/api/videos/shelf/{item_id}/last-page",
                         headers=AUTH, json={"url": url})
        assert bad.status_code == 422, (url, bad.text)
    saved = client.get("/api/videos/shelf", headers=AUTH).json()["items"][0]
    assert saved["last_url"] == "https://movies.example.org/show/1"


def test_novel_site_cannot_be_saved_as_video(client):
    sid = site(client, domain="books.example.org", kind="novel")
    response = favorite(client, sid, url="https://books.example.org/book/1")
    assert response.status_code == 422
    assert client.get("/api/videos/shelf", headers=AUTH).json()["items"] == []


def test_disabled_or_missing_site_refused(client):
    assert favorite(client, 999).status_code == 422
    sid = site(client)
    disabled = client.patch(f"/api/sites/{sid}", headers=AUTH, json={"enabled": False})
    assert disabled.status_code == 200
    assert favorite(client, sid).status_code == 422


def test_remove_missing_and_existing_video(client):
    sid = site(client)
    vid = favorite(client, sid).json()["id"]
    gone = client.delete(f"/api/videos/shelf/{vid}", headers=AUTH)
    assert gone.status_code == 200
    assert gone.json()["deleted"] is True
    assert client.get("/api/videos/shelf", headers=AUTH).json()["items"] == []
    assert client.delete(f"/api/videos/shelf/{vid}", headers=AUTH).status_code == 404
    assert client.put(f"/api/videos/shelf/{vid}/last-page", headers=AUTH, json={
        "url": "https://movies.example.org/watch/7"
    }).status_code == 404


def test_video_collections_are_separate_from_novel_shelf(client):
    sid = site(client, kind="other")
    vid = favorite(client, sid)
    assert vid.status_code == 201
    assert len(client.get("/api/videos/shelf", headers=AUTH).json()["items"]) == 1
    assert client.get("/api/shelf", headers=AUTH).json()["items"] == []
