import pytest
from fastapi.testclient import TestClient
from smartresource.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SMARTRESOURCE_DB", str(tmp_path / "data.sqlite3"))
    monkeypatch.setenv("SMARTRESOURCE_API_TOKEN", "secret")
    with TestClient(app) as api:
        yield api


def test_requires_auth(client):
    assert client.post("/api/sites/999/analyze", json={}).status_code == 401


def test_missing_site(client):
    r = client.post("/api/sites/999/analyze", headers={"X-API-Key": "secret"}, json={})
    assert r.status_code == 404


def test_sample_url_restricted_to_same_host(client, monkeypatch):
    # Public syntactic host: no DNS is needed before the same-host check.
    from smartresource import main
    site = client.post("/api/sites", headers={"X-API-Key": "secret"},
                       json={"name":"Demo", "base_url":"https://books.example.org"}).json()
    r = client.post(f"/api/sites/{site['id']}/analyze", headers={"X-API-Key":"secret"},
                    json={"page_url":"https://evil.example.org/novel/1"})
    assert r.status_code == 422


def test_analyze_returns_uncommitted_suggestions(client, monkeypatch):
    from smartresource import main

    sample = """
    <form method="GET" action="/search"><input name="q"></form>
    <ul id="chapter-list">
      <li><a href="/c/1">第1章 开始</a></li>
      <li><a href="/c/2">第2章 中间</a></li>
      <li><a href="/c/3">第3章 结束</a></li>
    </ul>
    """

    async def fake_fetch(url, expected_host):
        assert expected_host == "books.example.org"
        assert url == "https://books.example.org"
        return sample

    monkeypatch.setattr(main, "fetch_html", fake_fetch)
    site = client.post(
        "/api/sites", headers={"X-API-Key": "secret"},
        json={"name": "Demo", "base_url": "https://books.example.org"}
    ).json()
    response = client.post(
        f"/api/sites/{site['id']}/analyze", headers={"X-API-Key": "secret"}, json={}
    )
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["kind_guess"] == "novel"
    assert report["search_candidate"]["search_url_template"] == "https://books.example.org/search?q={q}"
    assert report["toc_candidate"]["toc_item_selector"] == "#chapter-list > li"
    saved = client.get("/api/sites", headers={"X-API-Key": "secret"}).json()["items"][0]
    assert saved["search_url_template"] is None
    assert saved["toc_item_selector"] is None
