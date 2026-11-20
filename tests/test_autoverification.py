import pytest
from fastapi.testclient import TestClient
from smartresource import autoconfig
from smartresource.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SMARTRESOURCE_DB", str(tmp_path / "verified.db"))
    monkeypatch.setenv("SMARTRESOURCE_API_TOKEN", "secret")
    with TestClient(app) as api:
        yield api


def test_sample_verified_on_import(client, monkeypatch):
    async def fake_fetch(url, host):
        if "search" in url:
            return '<a href="/book/abc">长篇小说甲</a>'
        return """<h1>小说库</h1><form action="/search" method="GET"><input name="q"></form>
        <a href="/book/abc">长篇小说甲</a>"""
    monkeypatch.setattr(autoconfig, "fetch_html", fake_fetch)
    r = client.post("/api/sites", headers={"X-API-Key": "secret"}, json={
        "name": "Books", "base_url": "https://books.example.org", "kind": "novel"})
    assert r.status_code == 201, r.text
    items = client.get("/api/sites", headers={"X-API-Key": "secret"}).json()["items"]
    assert items[0]["scan_status"] == "probe_passed"
    assert "样本" in items[0]["scan_message"]
    assert items[0]["search_url_template"].endswith("/search?q={q}")
