import asyncio
import pytest
from fastapi.testclient import TestClient
from smartresource.fallback_sources import (
    normalize_work, chapter_key, match_chapter, find_sources
)
from smartresource.main import app

H = {"X-API-Key": "secret"}


def test_titles_match_strictly_and_chapters_have_numbers():
    assert normalize_work("《春风故事》") == normalize_work("春风 故事")
    assert normalize_work("春风故事外传") != normalize_work("春风故事")
    assert chapter_key("第十二章 风起") == (12, "风起")
    assert match_chapter("第12章 风起", "第十二章 风起") == "exact_chapter"
    assert match_chapter("第12章 风起", "第12章 风停") is None
    assert match_chapter("第12章", "第12章 风起") == "number_only"
    assert match_chapter("第12章", "第13章 风起") is None


def test_unknown_chapter_short_circuits_without_network():
    book = {"site_id": 1, "title": "春风故事"}
    result = asyncio.run(find_sources(book, [], "下一章"))
    assert result["status"] == "needs_chapter_title"
    assert result["alternatives"] == []


def test_valid_alternative_is_verified_without_switching(monkeypatch):
    import smartresource.fallback_sources as alt
    a = {"id": 1, "name": "Old", "base_url": "https://old.example.org",
         "kind": "novel", "enabled": 1, "search_url_template": "https://old.example.org/s?q={q}"}
    b = {"id": 2, "name": "New", "base_url": "https://books.example.org",
         "kind": "novel", "enabled": 1, "search_url_template": "https://books.example.org/search?q={q}"}
    async def fake_search(site, keyword):
        assert keyword == "春风故事"
        return [{"title": "《春风故事》", "url": "https://books.example.org/book/1"}], None
    async def fake_fetch(url, host, http_fetch=None):
        assert host == "books.example.org"
        if url.endswith("/book/1"):
            return """<h1>春风故事</h1><ul id="toc">
              <li><a href="/c/12">第12章 风起</a></li>
              <li><a href="/c/13">第13章 夜行</a></li>
              <li><a href="/c/14">第14章 远方</a></li>
              </ul>""", "http"
        return """<article id="chapter-content"><h1>第12章 风起</h1>
              <p>漫天繁星映照着山谷，少年收好行装准备出发，沿途的微风带来了远方树木的清香。</p>
              <p>他想起了昨日的决定，在黎明之前穿过树林，前往远处那座古老的城市继续冒险。</p>
              <p>穿过寂静的小路之后，太阳逐渐升起，远处的村庄终于出现在宽阔的草地上。</p>
              </article>""", "http"
    monkeypatch.setattr(alt, "fetch_site_results", fake_search)
    monkeypatch.setattr(alt, "fetch_analysis_html", fake_fetch)
    result = asyncio.run(find_sources({"site_id": 1, "title": "春风故事"},
                                      [a, b], "第十二章 风起"))
    assert result["status"] == "matches"
    assert result["auto_switched"] is False
    assert len(result["alternatives"]) == 1
    assert result["alternatives"][0]["confidence"] == "exact_chapter"
    assert result["alternatives"][0]["verified_extract"]


def test_mismatched_work_name_is_skipped(monkeypatch):
    import smartresource.fallback_sources as alt
    async def fake_search(site, keyword):
        return [{"title": "春风故事外传", "url": "https://books.example.org/book/1"}], None
    async def fail_fetch(*args, **kwargs):
        raise AssertionError("should not fetch if book name differs")
    monkeypatch.setattr(alt, "fetch_site_results", fake_search)
    monkeypatch.setattr(alt, "fetch_analysis_html", fail_fetch)
    b = {"id": 2, "name": "New", "base_url": "https://books.example.org",
         "kind": "novel", "enabled": 1, "search_url_template": "https://books.example.org/s?q={q}"}
    result = asyncio.run(find_sources({"site_id": 1, "title": "春风故事"}, [b], "第12章 风起"))
    assert result["status"] == "no_match"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SMARTRESOURCE_DB", str(tmp_path / "alt.sqlite"))
    monkeypatch.setenv("SMARTRESOURCE_API_TOKEN", "secret")
    with TestClient(app) as tc:
        yield tc


def test_api_requires_token_and_existing_book(client):
    assert client.post("/api/reader/alternatives", json={"book_id": 1, "chapter_title": "第12章"}).status_code == 401
    r = client.post("/api/reader/alternatives", headers=H,
                    json={"book_id": 1234, "chapter_title": "第12章 风起"})
    assert r.status_code == 404


def test_no_target_chapter_returns_needs_title(client):
    new = client.post("/api/sites?auto_scan=false", headers=H,
                      json={"name": "Old", "base_url": "https://books.example.org"}).json()
    item = client.post("/api/shelf", headers=H,
                       json={"site_id": new["id"], "book_url": "https://books.example.org/book/1",
                             "title": "春风故事"}).json()
    result = client.post("/api/reader/alternatives", headers=H,
                         json={"book_id": item["id"], "chapter_title": "下一章"})
    assert result.status_code == 200
    assert result.json()["status"] == "needs_chapter_title"
