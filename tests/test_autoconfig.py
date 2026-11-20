import sqlite3
import pytest
from fastapi.testclient import TestClient
from smartresource.main import app
from smartresource import autoconfig
from smartresource.db import connect


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SMARTRESOURCE_DB", str(tmp_path / "autoconfig.db"))
    monkeypatch.setenv("SMARTRESOURCE_API_TOKEN", "test-token")
    with TestClient(app) as instance:
        yield instance


def test_import_automatically_fills_get_search_form_without_css(client, monkeypatch):
    sample = """
    <h1>小说目录</h1>
    <form action="/search" method="GET">
      <input name="q" type="search"><button>搜索</button>
    </form>"""

    async def fake_fetch(url, host):
        assert url == "https://books.example.org"
        assert host == "books.example.org"
        return sample

    monkeypatch.setattr(autoconfig, "fetch_html", fake_fetch)
    r = client.post("/api/sites/import-text", headers={"X-API-Key": "test-token"},
                    json={"text":"小说库 | https://books.example.org/novel/1"})
    assert r.status_code == 200, r.text
    assert r.json()["created"] == 1
    assert r.json()["auto_scan_queued"] == 1
    sites = client.get("/api/sites", headers={"X-API-Key": "test-token"}).json()["items"]
    assert sites[0]["search_url_template"] == "https://books.example.org/search?q={q}"
    assert sites[0]["item_selector"] is None
    assert sites[0]["scan_status"] == "candidate"
    assert "无法自动验证" in sites[0]["scan_message"]


def test_no_overwrite_and_recheck(client, monkeypatch):
    async def fake_fetch(url, host):
        return '<form method="GET" action="/search"><input name="q"></form><h1>小说</h1>'

    monkeypatch.setattr(autoconfig, "fetch_html", fake_fetch)
    r = client.post("/api/sites?auto_scan=false", headers={"X-API-Key":"test-token"}, json={
        "name":"Manual", "base_url":"https://books.example.org",
        "search_url_template":"https://books.example.org/custom?keyword={q}"
    })
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    r = client.post("/api/sites/scan", headers={"X-API-Key":"test-token"},
                    json={"site_ids":[sid]})
    assert r.status_code == 200, r.text
    site = client.get("/api/sites", headers={"X-API-Key":"test-token"}).json()["items"][0]
    assert site["search_url_template"] == "https://books.example.org/custom?keyword={q}"


def test_unrecognized_form_needs_rule(client, monkeypatch):
    async def fake_fetch(url, host):
        return '<form method="POST" action="/search"><input name="q"></form>'

    monkeypatch.setattr(autoconfig, "fetch_html", fake_fetch)
    r = client.post("/api/sites", headers={"X-API-Key": "test-token"},
                    json={"name":"POST site","base_url":"https://books.example.org"})
    assert r.status_code == 201
    site = client.get("/api/sites", headers={"X-API-Key":"test-token"}).json()["items"][0]
    assert site["scan_status"] == "needs_rule"
    assert not site["search_url_template"]


def test_existing_site_migration(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    monkeypatch.setenv("SMARTRESOURCE_DB", str(path))
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE sites (id INTEGER PRIMARY KEY, name TEXT, base_url TEXT, kind TEXT, enabled INTEGER)")
        conn.execute("INSERT INTO sites VALUES (1, 'Old', 'https://example.org', 'novel', 1)")
    from smartresource.db import initialize
    initialize()
    with connect() as conn:
        col = {row[1] for row in conn.execute("PRAGMA table_info(sites)")}
        assert "scan_status" in col and "scan_checked_at" in col
