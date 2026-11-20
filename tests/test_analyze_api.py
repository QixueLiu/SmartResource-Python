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
