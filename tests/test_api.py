import pytest
from fastapi.testclient import TestClient
from smartresource.main import app

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SMARTRESOURCE_DB", str(tmp_path / "test.db"))
    monkeypatch.setenv("SMARTRESOURCE_API_TOKEN", "test-token")
    with TestClient(app) as api:
        yield api

def auth():
    return {"X-API-Key": "test-token"}

def test_health_and_auth(client):
    assert client.get("/api/health").json()["status"] == "ok"
    assert client.get("/api/sites").status_code == 401
    assert client.get("/api/sites", headers=auth()).json() == {"items": []}

def test_empty_search(client):
    response = client.get("/api/search?q=hello", headers=auth())
    assert response.status_code == 200
    assert response.json() == {"items": [], "warnings": [], "searched_sites": 0}

def test_reject_private_network(client):
    response = client.post("/api/sites", headers=auth(), json={"name": "bad", "base_url": "http://127.0.0.1:8000", "kind": "novel"})
    assert response.status_code == 422
