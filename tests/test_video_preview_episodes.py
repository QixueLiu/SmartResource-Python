"""Single-page episode hints for videos opened directly from multi-source search."""
import pytest
from fastapi.testclient import TestClient

from smartresource.main import app

AUTH = {"X-API-Key": "single-user-secret"}
BASE = "https://movies.example.org"
DETAIL = BASE + "/series/20"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SMARTRESOURCE_DB", str(tmp_path / "video-preview.sqlite"))
    monkeypatch.setenv("SMARTRESOURCE_API_TOKEN", "single-user-secret")
    with TestClient(app) as api:
        yield api


def add_site(client, kind="video"):
    response = client.post(
        "/api/sites?auto_scan=false", headers=AUTH,
        json={"name": "视频原网站", "base_url": BASE, "kind": kind}
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def preview(client, site_id, url=DETAIL, headers=AUTH):
    return client.get(
        "/api/videos/episodes",
        headers=headers,
        params={"site_id": site_id, "url": url},
    )


def test_direct_episode_list_does_not_create_video_favorite(client, monkeypatch):
    site_id = add_site(client)
    requests = []
    async def fake_fetch(url, host):
        requests.append((url, host))
        return '''
        <div class="play-list">
          <a href="/watch/1">第1集</a>
          <a href="/watch/2">第2集</a>
        </div>
        '''
    monkeypatch.setattr("smartresource.main.fetch_html", fake_fetch)
    result = preview(client, site_id)
    assert result.status_code == 200
    assert result.json()["status"] == "candidates"
    assert [entry["episode_number"] for entry in result.json()["items"]] == [1, 2]
    assert requests == [(DETAIL, "movies.example.org")]
    assert client.get("/api/videos/shelf", headers=AUTH).json()["items"] == []


def test_mixed_site_can_preview_episodes_without_favorite(client, monkeypatch):
    site_id = add_site(client, kind="mixed")
    async def fake_fetch(url, host):
        return '<div class="playlist"><a href="/watch/12">Episode 12</a></div>'
    monkeypatch.setattr("smartresource.main.fetch_html", fake_fetch)
    result = preview(client, site_id)
    assert result.status_code == 200
    assert result.json()["items"][0]["episode_number"] == 12


@pytest.mark.parametrize("url", [
    "https://outside.example.org/watch/7",
    "http://localhost/private",
    "http://127.0.0.1/data",
    "file:///etc/passwd",
    "javascript:alert(1)",
    "https://movies.example.org:8080/admin",
    BASE + "/"
])
def test_bad_preview_urls_are_rejected_without_fetch(client, monkeypatch, url):
    site_id = add_site(client)
    async def reject_fetch(*args):
        raise AssertionError("MUST NOT FETCH REJECTED URL")
    monkeypatch.setattr("smartresource.main.fetch_html", reject_fetch)
    result = preview(client, site_id, url)
    assert result.status_code == 422, result.text


def test_preview_auth_disabled_and_novel_sites(client, monkeypatch):
    site_id = add_site(client)
    assert preview(client, site_id, headers={}).status_code == 401
    assert preview(client, 9999).status_code == 404

    client.patch(f"/api/sites/{site_id}", headers=AUTH, json={"enabled": False})
    disabled = preview(client, site_id)
    assert disabled.status_code == 200
    assert disabled.json()["status"] == "unavailable"

    client.patch(
        f"/api/sites/{site_id}", headers=AUTH,
        json={"enabled": True, "kind": "novel"}
    )
    assert preview(client, site_id).status_code == 422


def test_preview_network_failure_keeps_site_browser_available(client, monkeypatch):
    site_id = add_site(client)
    async def blocked(*args):
        raise RuntimeError("site requires login")
    monkeypatch.setattr("smartresource.main.fetch_html", blocked)
    response = preview(client, site_id)
    assert response.status_code == 200
    assert response.json()["items"] == []
    assert response.json()["status"] == "unavailable"
