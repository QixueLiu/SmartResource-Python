"""Episode-link hints from ONE detail page; no live site or stream requests."""
import pytest
from fastapi.testclient import TestClient

from smartresource.main import app
from smartresource.video_episodes import MAX_EPISODES, parse_episodes

AUTH = {"X-API-Key": "private-token"}
BASE = "https://movies.example.org/show/1"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SMARTRESOURCE_DB", str(tmp_path / "episodes.sqlite"))
    monkeypatch.setenv("SMARTRESOURCE_API_TOKEN", "private-token")
    with TestClient(app) as http:
        yield http


def add_video(client):
    site = client.post("/api/sites?auto_scan=false", headers=AUTH, json={
        "name": "Movies", "base_url": "https://movies.example.org", "kind": "video"
    })
    assert site.status_code == 201
    saved = client.post("/api/videos/shelf", headers=AUTH, json={
        "site_id": site.json()["id"], "title": "电影甲", "video_url": BASE
    })
    assert saved.status_code == 201
    return saved.json()["id"], site.json()["id"]


def test_parse_episode_links_only_on_same_site_and_without_pagination():
    html = """
    <div class="play-list">
      <a href="/watch/1">第1集</a>
      <a href="/watch/2">02</a>
      <a href="/watch/2">02</a>
      <a href="/show/1">第0集</a>
      <a href="https://other.example.org/watch/3">第3集</a>
      <a href="javascript:void(0)">第4集</a>
      <a href="https://movies.example.org:8080/watch/5">第5集</a>
      <a href="/watch/6#position">Episode 6</a>
    </div>
    <div class="pagination"><a href="/page/2">第2集</a></div>
    """
    output = parse_episodes(html, BASE, "movies.example.org")
    assert output["status"] == "candidates"
    assert [e["title"] for e in output["items"]] == ["第1集", "02", "Episode 6"]
    assert output["items"][-1]["url"] == "https://movies.example.org/watch/6"


def test_unlabeled_links_do_not_claim_to_be_episodes():
    output = parse_episodes(
        '<nav><a href="/search">搜索</a></nav><a href="/show/2">热门推荐</a>',
        BASE, "movies.example.org"
    )
    assert output["status"] == "none"
    assert output["items"] == []


def test_episode_scan_is_bounded():
    html = '<div class="playlist">' + "".join(
        f'<a href="/watch/{n}">{n}</a>' for n in range(200)
    ) + "</div>"
    output = parse_episodes(html, BASE, "movies.example.org")
    assert len(output["items"]) == MAX_EPISODES


def test_episode_api_requires_token_and_only_fetches_saved_detail(client, monkeypatch):
    video_id, _ = add_video(client)
    assert client.get(f"/api/videos/shelf/{video_id}/episodes").status_code == 401
    assert client.get("/api/videos/shelf/9999/episodes", headers=AUTH).status_code == 404
    called = []

    async def fake_fetch(url, host):
        called.append((url, host))
        return '<div class="play-list"><a href="/watch/1">第1集</a></div>'

    monkeypatch.setattr("smartresource.main.fetch_html", fake_fetch)
    response = client.get(f"/api/videos/shelf/{video_id}/episodes", headers=AUTH)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "candidates"
    assert len(response.json()["items"]) == 1
    assert called == [(BASE, "movies.example.org")]


def test_episode_api_unavailable_keeps_original_video_usable(client, monkeypatch):
    video_id, site_id = add_video(client)

    async def blocked(*args):
        raise RuntimeError("Access restricted")

    monkeypatch.setattr("smartresource.main.fetch_html", blocked)
    response = client.get(f"/api/videos/shelf/{video_id}/episodes", headers=AUTH)
    assert response.status_code == 200
    assert response.json()["status"] == "unavailable"
    assert response.json()["items"] == []

    client.patch(f"/api/sites/{site_id}", headers=AUTH, json={"enabled": False})
    response = client.get(f"/api/videos/shelf/{video_id}/episodes", headers=AUTH)
    assert response.json()["status"] == "unavailable"
