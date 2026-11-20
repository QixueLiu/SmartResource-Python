import pytest
from fastapi.testclient import TestClient
from smartresource.reader import extract_reader_page, expand_toc_result
import asyncio
from smartresource.main import app

SITE = {"id": 3, "name": "Booksite", "base_url": "https://books.example.org",
        "toc_item_selector": None, "toc_title_selector": None, "toc_link_selector": None}


def test_clean_chapter_and_same_host_next():
    html = """<html><title>第12章 新的冒险</title><nav>广告</nav>
      <article id="chapter-content">
        <h1>第12章 新的冒险</h1>
        <p>夜色渐渐笼罩着小镇，街头的行人纷纷回家。故事的主人公踏上了漫长的旅行，心中充满期待与疑问。</p>
        <div class="advert-banner">广告推广，请下载应用获取更多内容内容内容内容。</div>
        <p>他沿着蜿蜒的道路继续前进，远方的群山在月光下变得格外清晰。初冬的微风吹过，他没有停下脚步。</p>
        <p>第二天清晨，他抵达了陌生的村庄。人们围坐在广场上，讨论正在发生的奇异事件，以及未来的计划。</p>
        <p>老人指着远处的道路，告诉他森林深处隐藏着许多从未被发现的秘密。他静静聆听，决定继续探索。</p>
      </article><a href="/chapter/13">下一章</a>
      <a href="https://other.example.com/13">上一章</a></html>"""
    result = extract_reader_page(html, "https://books.example.org/chapter/12", SITE)
    assert result["kind"] == "chapter", result
    assert "初冬的微风" in result["content"]
    assert "下载应用" not in result["content"]
    assert result["next_url"] == "https://books.example.org/chapter/13"
    assert result["previous_url"] is None
    assert result["character_count"] >= 160


def test_book_detail_returns_toc():
    html = """<html><h1>示例小说</h1>
      <ul id="chapters">
      <li><a href="/1">第1章 起始</a></li>
      <li><a href="/2">第2章 路途</a></li>
      <li><a href="/3">第3章 完结</a></li>
      </ul></html>"""
    result = extract_reader_page(html, "https://books.example.org/book/3", SITE)
    assert result["kind"] == "toc", result
    assert len(result["chapters"]) == 3
    assert result["chapters"][0]["url"] == "https://books.example.org/1"


def test_short_or_navigation_only_returns_fallback():
    html = '<h1>登录</h1><nav>首页 | 搜索 | 广告</nav><div class="info">请先登录</div>'
    result = extract_reader_page(html, "https://books.example.org/ch/3", SITE)
    assert result["kind"] == "fallback"
    assert result["source_url"].startswith("https://")


def test_does_not_export_external_links_as_chapters():
    html = """<h1>示例小说</h1><ul id="chapters">
        <li><a href="/1">第1章 起始</a></li><li><a href="/2">第2章 路途</a></li>
        <li><a href="https://offsite.example.com/3">第3章 外站</a></li>
        <li><a href="/4">第4章 回归</a></li></ul>"""
    result = extract_reader_page(html, "https://books.example.org/book/9", SITE)
    assert result["kind"] == "toc"
    assert len(result["chapters"]) == 3


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("SMARTRESOURCE_DB", str(tmp_path / "reader.db"))
    monkeypatch.setenv("SMARTRESOURCE_API_TOKEN", "test-token")
    with TestClient(app) as client:
        yield client


def test_reader_rejects_cross_host_and_requires_auth(api):
    added = api.post("/api/sites?auto_scan=false", headers={"X-API-Key":"test-token"},
        json={"name": "Booksite", "base_url": "https://books.example.org", "kind":"novel"}).json()
    sid = added["id"]
    request = {"site_id": sid, "url": "https://other.example.com/chapter/1"}
    assert api.post("/api/reader/open", json=request).status_code == 401
    response = api.post("/api/reader/open", headers={"X-API-Key":"test-token"}, json=request)
    assert response.status_code == 422, response.text


def test_reader_api_renders_chapter(api, monkeypatch):
    from smartresource import main
    async def fake_fetch(url, host, http_fetch=None):
        return """<h1>第8章 旅程</h1><article class="chapter-content">
          <p>夜色降临时他们来到静谧的湖边，远处的星空照亮了崎岖的小路。这一晚所有人都沉浸在安静的思绪里。</p>
          <p>清晨时分大家收拾好行囊，再次沿着山坡向前走去。大地逐渐苏醒，远方的小镇传来热闹的人声。</p>
          <p>在经过漫长的旅程以后，他们终于看见山谷尽头的古老城堡。旅途的艰辛并未减弱他们继续探索的热情。</p>
        </article>""", "http"
    monkeypatch.setattr(main, "fetch_analysis_html", fake_fetch)
    added = api.post("/api/sites?auto_scan=false", headers={"X-API-Key":"test-token"},
        json={"name": "Booksite", "base_url": "https://books.example.org", "kind":"novel"}).json()
    response = api.post("/api/reader/open", headers={"X-API-Key":"test-token"},
        json={"site_id": added["id"], "url": "https://books.example.org/chapter/8"})
    assert response.status_code == 200, response.text
    assert response.json()["kind"] == "chapter"
    assert "清晨" in response.json()["content"]



def _directory(page: int, next_url: str | None = None):
    offset = (page - 1) * 3
    li = "".join(
        f'<li><a href="/chapters/{number}">第{number}章 正文</a></li>'
        for number in range(offset + 1, offset + 4)
    )
    pager = f'<div class="pagination"><a href="{next_url}">目录下一页</a></div>' if next_url else ""
    return f'<h1>示例长篇小说</h1><ul id="chapters">{li}</ul>{pager}'


def test_directory_page_navigation_and_deduplication():
    first = extract_reader_page(
        _directory(1, "/book/1?page=2"), "https://books.example.org/book/1", SITE
    )
    assert first["kind"] == "toc"
    assert first["toc_next_url"] == "https://books.example.org/book/1?page=2"

    calls = []
    async def fake_fetch(url, host):
        calls.append(url)
        assert host == "books.example.org"
        if "?page=2" in url:
            return _directory(2, "/book/1?page=3")
        if "?page=3" in url:
            return _directory(3)
        raise AssertionError("unwanted follow-up fetch")
    result = asyncio.run(expand_toc_result(first, SITE, fake_fetch, max_pages=3))
    assert len(result["chapters"]) == 9
    assert result["pages_fetched"] == 3
    assert result["has_more_directory_pages"] is False
    assert len(calls) == 2


def test_directory_crawl_is_bounded_and_no_external_page():
    page = _directory(1, "https://bad.example.net/book/1?page=2")
    first = extract_reader_page(page, "https://books.example.org/book/1", SITE)
    assert first["toc_next_url"] is None
    async def no_fetch(url, host):
        raise AssertionError("must not fetch")
    out = asyncio.run(expand_toc_result(first, SITE, no_fetch))
    assert len(out["chapters"]) == 3
    assert out["pages_fetched"] == 1

    first = extract_reader_page(
        _directory(1, "/book/1?page=2"), "https://books.example.org/book/1", SITE
    )
    out = asyncio.run(expand_toc_result(first, SITE, no_fetch, max_pages=1))
    assert out["has_more_directory_pages"] is True
    assert len(out["chapters"]) == 3


def test_chapter_next_link_not_mistaken_for_directory_page():
    html = _directory(1) + '<a href="/chapter/4">下一章</a>'
    first = extract_reader_page(html, "https://books.example.org/book/1", SITE)
    assert first["kind"] == "toc"
    assert first["toc_next_url"] is None
