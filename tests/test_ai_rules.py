import asyncio
import json
import pytest
from fastapi.testclient import TestClient
from smartresource import ai_rules
from smartresource.main import app

HTML = """<html><h1>书库目录</h1>
<div id="books">
<div class="result"><a class="title" href="/book/1">春风十里</a></div>
<div class="result"><a class="title" href="/book/2">海边故事</a></div>
</div>
<ul id="chapters">
<li><a href="/chapter/1">第1章 第一夜</a></li>
<li><a href="/chapter/2">第2章 雨夜</a></li>
<li><a href="/chapter/3">第3章 清晨</a></li>
</ul>
<script>var secret='DO_NOT_SEND';</script>
<form><input name="password" value="SECRET_PASSWORD"/></form>
</html>"""

VALID = {
    "search_results": {
        "item_selector": "#books .result",
        "title_selector": "a.title",
        "link_selector": "a.title",
    },
    "toc": {
        "item_selector": "#chapters li",
        "title_selector": "a",
        "link_selector": "a",
    },
}


def test_snapshot_excludes_scripts_and_passwords():
    snap = ai_rules.dom_snapshot(HTML, "https://book.example.org")
    assert "SECRET_PASSWORD" not in snap
    assert "DO_NOT_SEND" not in snap
    assert "book" in snap


def test_valid_candidate_has_same_host_evidence():
    result = ai_rules.validate_model_output(VALID, HTML, "https://book.example.org")
    assert result["search_candidate"]["matched_links"] == 2
    assert result["toc_candidate"]["matched_links"] == 3
    assert result["search_candidate"]["status"] == "candidate_only"


def test_invalid_selectors_and_offsite_urls_dropped():
    proposed = {"search_results": {
        "item_selector": "script:has(a)",
        "title_selector": "a", "link_selector": "a",
    }, "toc": {"item_selector": "html", "title_selector": "a", "link_selector": "a"}}
    html = """<a href="https://outside.example.net/c1">第1章 外部</a>
    <a href="https://outside.example.net/c2">第2章 外部</a>
    <a href="https://outside.example.net/c3">第3章 外部</a>"""
    result = ai_rules.validate_model_output(proposed, html, "https://book.example.org")
    assert result["search_candidate"] is None
    assert result["toc_candidate"] is None


def test_disabled_never_calls_model(monkeypatch):
    monkeypatch.delenv("SMARTRESOURCE_AI_ENABLED", raising=False)
    async def forbidden(prompt):
        raise AssertionError("AI should not be called")
    result = asyncio.run(ai_rules.suggest(HTML, "https://book.example.org", generate=forbidden))
    assert result["status"] == "disabled"


def test_enabled_with_stub_ollama_model(monkeypatch):
    monkeypatch.setenv("SMARTRESOURCE_AI_ENABLED", "1")
    async def fake_model(prompt):
        assert "SECRET_PASSWORD" not in prompt
        return VALID
    result = asyncio.run(ai_rules.suggest(HTML, "https://book.example.org", generate=fake_model))
    assert result["status"] == "candidates"
    assert result["toc_candidate"]["matched_links"] == 3


def test_ollama_url_must_be_local(monkeypatch):
    monkeypatch.setenv("SMARTRESOURCE_OLLAMA_URL", "http://192.168.1.2:11434")
    with pytest.raises(ValueError):
        ai_rules._local_endpoint()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SMARTRESOURCE_DB", str(tmp_path / "ai.sqlite"))
    monkeypatch.setenv("SMARTRESOURCE_API_TOKEN", "secret")
    with TestClient(app) as c:
        yield c


def test_ai_api_requires_auth(client):
    r = client.post("/api/sites/1/ai-analyze", json={})
    assert r.status_code == 401


def test_ai_report_not_saved_without_user_approval(client, monkeypatch):
    from smartresource import main
    monkeypatch.setenv("SMARTRESOURCE_AI_ENABLED", "1")
    async def fake_fetch(url, host, http_fetch=None):
        return HTML, "http"
    async def fake_analyze(html, url):
        return {"enabled": True, "status": "candidates",
                "model": "fake", **ai_rules.validate_model_output(VALID, html, url)}
    monkeypatch.setattr(main, "fetch_analysis_html", fake_fetch)
    monkeypatch.setattr(main, "ai_suggest", fake_analyze)
    site = client.post("/api/sites?auto_scan=false", headers={"X-API-Key":"secret"},
                       json={"name":"Books","base_url":"https://book.example.org"}).json()
    report = client.post(f"/api/sites/{site['id']}/ai-analyze",
                         headers={"X-API-Key":"secret"}, json={})
    assert report.status_code == 200, report.text
    assert report.json()["ai_analysis"]["toc_candidate"]["matched_links"] == 3
    saved = client.get("/api/sites", headers={"X-API-Key":"secret"}).json()["items"][0]
    assert saved["toc_item_selector"] is None
