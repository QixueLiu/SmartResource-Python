import asyncio
import pytest
from fastapi.testclient import TestClient
from smartresource.main import app

AUTH = {"X-API-Key": "test-token"}
HOME = """<h1>图书站</h1>
<form action="/search" method="GET"><input name="q"></form>
<a href="/book/1">春风故事</a>"""


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SMARTRESOURCE_DB", str(tmp_path / "quick.sqlite"))
    monkeypatch.setenv("SMARTRESOURCE_API_TOKEN", "test-token")
    with TestClient(app) as api:
        yield api


def create_site(client, template=None):
    data = {"name": "Books", "base_url": "https://books.example.org", "kind": "novel"}
    if template is not None:
        data["search_url_template"] = template
    response = client.post("/api/sites?auto_scan=false", headers=AUTH, json=data)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_fast_check_adds_missing_url_only_on_positive_sample(client, monkeypatch):
    from smartresource import main
    async def fetch(url, host):
        assert host == "books.example.org"
        return HOME if url == "https://books.example.org" else '<a href="/book/1">春风故事</a>'
    monkeypatch.setattr(main, "fetch_html", fetch)
    sid = create_site(client)
    r = client.post(f"/api/sites/{sid}/quick-check", headers=AUTH)
    assert r.status_code == 200, r.text
    assert r.json()["verified"] is True and r.json()["auto_applied"] is True
    saved = client.get("/api/sites", headers=AUTH).json()["items"][0]
    assert saved["search_url_template"] == "https://books.example.org/search?q={q}"


def test_fast_check_does_not_apply_unverified_candidate(client, monkeypatch):
    from smartresource import main
    async def fetch(url, host):
        return HOME if url == "https://books.example.org" else "<div>No results</div>"
    monkeypatch.setattr(main, "fetch_html", fetch)
    sid = create_site(client)
    r = client.post(f"/api/sites/{sid}/quick-check", headers=AUTH)
    assert r.status_code == 200 and not r.json()["verified"]
    assert client.get("/api/sites", headers=AUTH).json()["items"][0]["search_url_template"] is None


def test_fast_check_does_not_overwrite_manual_template(client, monkeypatch):
    from smartresource import main
    old = "https://books.example.org/custom?wd={q}"
    async def fetch(url, host):
        return HOME if url == "https://books.example.org" else '<a href="/book/1">春风故事</a>'
    monkeypatch.setattr(main, "fetch_html", fetch)
    sid = create_site(client, old)
    r = client.post(f"/api/sites/{sid}/quick-check", headers=AUTH)
    assert r.status_code == 200 and r.json()["verified"]
    assert r.json()["auto_applied"] is False
    assert r.json()["candidate_template"] == "https://books.example.org/search?q={q}"
    assert client.get("/api/sites", headers=AUTH).json()["items"][0]["search_url_template"] == old


def test_fast_check_auth_and_disabled_site(client):
    sid = create_site(client)
    assert client.post(f"/api/sites/{sid}/quick-check").status_code == 401
    assert client.patch(f"/api/sites/{sid}", headers=AUTH, json={"enabled":False}).status_code == 200
    assert client.post(f"/api/sites/{sid}/quick-check", headers=AUTH).status_code == 404


def test_search_budget_reports_skipped_sites_and_keeps_results(monkeypatch):
    from smartresource import search
    monkeypatch.setattr(search, "MAX_SEARCH_SITES", 2)
    monkeypatch.setattr(search, "MAX_SEARCH_SECONDS", 0.1)
    monkeypatch.setattr(search, "PER_SITE_SECONDS", 0.01)
    async def slow(site, keyword):
        await asyncio.sleep(2)
        return [], None
    monkeypatch.setattr(search, "fetch_site_results", slow)
    from smartresource.site_health import rank_sites
    # Supply unknown ids without an initialized DB; ranking has neutral fallback.
    sites = [
        {"id":i,"name":str(i), "kind":"novel", "base_url":f"https://{i}.example.org"}
        for i in range(1, 4)
    ]
    result = asyncio.run(search.search_all(sites, "x"))
    assert result["searched_sites"] == 2
    assert result["skipped_sites"] == 1
    assert len(result["warnings"]) == 2
