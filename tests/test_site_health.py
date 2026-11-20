from datetime import datetime, timedelta, timezone
import asyncio
import pytest
import httpx
from fastapi.testclient import TestClient
from smartresource.main import app
from smartresource.db import connect
from smartresource.site_health import observe, health_map, rank_sites, classify_error

AUTH = {"X-API-Key": "test-token"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SMARTRESOURCE_DB", str(tmp_path / "metrics.sqlite"))
    monkeypatch.setenv("SMARTRESOURCE_API_TOKEN", "test-token")
    with TestClient(app) as api:
        yield api


def add_site(client, domain="novel.example.org"):
    response = client.post("/api/sites?auto_scan=false", headers=AUTH, json={
        "name": domain, "base_url": "https://" + domain, "kind": "novel",
        "search_url_template": "https://" + domain + "/search?q={q}"
    })
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_unknown_has_neutral_score_and_requires_samples(client):
    first = add_site(client)
    snapshot = client.get("/api/sites", headers=AUTH).json()["items"][0]["health"]
    assert snapshot["score"] == 50
    assert snapshot["status"] == "insufficient_data"
    assert snapshot["samples"] == 0
    observe(first, "search", "success", "ok")
    score = health_map([first])[first]
    assert score["status"] == "insufficient_data"
    assert score["score"] < 100


def test_failure_codes_safe_and_bounded(client):
    site = add_site(client)
    observe(site, "reader", "failure", "secret=https://example.org/path?api_key=ABC")
    details = client.get(f"/api/sites/{site}/health", headers=AUTH)
    assert details.status_code == 200
    assert details.json()["recent_failures"][0]["reason"] == "unexpected_error"
    for _ in range(160):
        observe(site, "search", "failure", "timeout")
    with connect() as conn:
        rows = conn.execute("SELECT COUNT(*) FROM site_health_events WHERE site_id=?", (site,)).fetchone()[0]
    assert rows == 150
    assert "https://" not in str(details.json())


def test_stale_samples_ignored_and_disabled_site_rejected(client):
    site = add_site(client)
    observe(site, "reader", "failure", "timeout")
    with connect() as conn:
        conn.execute("UPDATE site_health_events SET observed_at=? WHERE site_id=?",
            ((datetime.now(timezone.utc)-timedelta(days=40)).isoformat(), site))
    assert health_map([site])[site]["samples"] == 0
    assert client.get(f"/api/sites/{site}/health").status_code == 401
    assert client.get("/api/sites/health", headers=AUTH).status_code == 200


def test_search_prioritization_after_repeated_real_success(monkeypatch, client):
    from smartresource import search
    high = add_site(client, "high.example.org")
    low = add_site(client, "low.example.org")
    for _ in range(6):
        observe(high, "reader", "success", "ok")
        observe(low, "reader", "failure", "parse_failure")
    candidates = [
        {"id":low, "name":"low", "kind":"novel","enabled":1,
         "base_url":"https://low.example.org","search_url_template":"x"},
        {"id":high, "name":"high", "kind":"novel","enabled":1,
         "base_url":"https://high.example.org","search_url_template":"x"},
    ]
    assert rank_sites(candidates)[0]["id"] == high
    async def mocked(site, keyword):
        return ([{"title":keyword, "url":site["base_url"]+"/novel",
                  "site_id":site["id"],"site_name":site["name"],"kind":"novel"}], None)
    monkeypatch.setattr(search, "fetch_site_results", mocked)
    found = asyncio.run(search.search_all(candidates, "测试"))
    assert found["items"][0]["site_id"] == high
    assert found["items"][0]["source_health"]["status"] == "stable"
    assert found["items"][1]["source_health"]["status"] == "unstable"


def test_search_zero_results_are_neutral_not_failure(client, monkeypatch):
    from smartresource import search
    site_id = add_site(client)
    site = {"id":site_id, "name":"demo", "kind":"novel",
            "base_url":"https://novel.example.org",
            "search_url_template":"https://novel.example.org/search?q={q}",
            "item_selector":"li.book", "title_selector":"a",
            "link_selector":"a"}
    async def fake_fetch(url, host, http_fetch=None):
        return "<html><h1>No matching books</h1></html>", "http"
    monkeypatch.setattr(search, "fetch_analysis_html", fake_fetch)
    hits, warning = asyncio.run(search.fetch_site_results(site, "NotFound"))
    assert hits == []
    score = health_map([site_id])[site_id]
    assert score["failures"] == 0
    assert score["neutral"] == 1


def test_reader_status_records_success_and_parse_failure(client, monkeypatch):
    from smartresource import main
    site = add_site(client)
    page = '<h1>第3章 旅途</h1><article class="chapter-text">' + ''.join(
        f"<p>这是一段足够长的正文示例段落，内容围绕主人公旅程展开，记录途中的见闻和故事发展。{i}</p>"
        for i in range(6)) + '</article>'
    async def fake_fetch(url, host, http_fetch=None):
        return (page if url.endswith("/c3") else "<h1>登录</h1>"), "http"
    monkeypatch.setattr(main, "fetch_analysis_html", fake_fetch)
    for endpoint in ("/c3", "/login"):
        r = client.post("/api/reader/open", headers=AUTH, json={
            "site_id":site, "url":"https://novel.example.org"+endpoint})
        assert r.status_code == 200, r.text
    score = health_map([site])[site]
    assert score["successes"] == 1
    assert score["failures"] == 1
    # Request validation errors should not harm source health.
    r = client.post("/api/reader/open", headers=AUTH, json={
        "site_id":site, "url":"https://other.example.org/c3"})
    assert r.status_code == 422
    assert health_map([site])[site]["failures"] == 1


def test_classify_timeout():
    assert classify_error(httpx.ConnectTimeout("time out")) == "timeout"
