from fastapi.testclient import TestClient

from smartresource.main import app


def test_import_enrichment_preserves_existing_configuration(monkeypatch, tmp_path):
    monkeypatch.setenv("SMARTRESOURCE_DB", str(tmp_path / "enrich.sqlite"))
    monkeypatch.setenv("SMARTRESOURCE_API_TOKEN", "test-token")
    site = {"name": "Original", "base_url": "https://books.example.org",
            "kind": "novel", "enabled": False}
    rule = {**site, "name": "Updated", "enabled": True,
            "search_url_template": "https://books.example.org/search?q={q}"}
    protected = {**site, "base_url": "https://custom.example.org", "item_selector": ".manual"}
    with TestClient(app, headers={"X-API-Key": "test-token"}) as client:
        route = "/api/sites/import?auto_scan=false"
        assert client.post(route, json=[site, protected]).json()["created"] == 2
        assert client.post(route, json=[rule]).json()["existing"] == 1
        update = route + "&enrich_missing_rules=true"
        custom_rule = {**rule, "base_url": protected["base_url"],
                       "search_url_template": "https://custom.example.org/search?q={q}"}
        assert client.post(update, json=[rule, custom_rule]).json()["enriched"] == 1
        assert client.post(update, json=[rule]).json()["enriched"] == 0
        records = {record["base_url"]: record for record in client.get("/api/sites").json()["items"]}
        assert records[site["base_url"]]["search_url_template"] == rule["search_url_template"]
        assert records[site["base_url"]]["enabled"] is False
        assert records[site["base_url"]]["name"] == "Original"
        assert records[protected["base_url"]]["search_url_template"] is None
