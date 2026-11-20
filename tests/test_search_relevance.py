"""Offline HTML fixtures: validate title relevance and junk-link suppression."""
import asyncio

from smartresource.search import (
    _match_grade, _quality, extract_search_results, heuristic_search_results,
    fetch_site_results,
)

SITE = {
    "id": 7,
    "name": "示例小说站",
    "base_url": "https://books.example.org",
    "kind": "novel",
    "search_url_template": "https://books.example.org/search?q={q}",
    "item_selector": ".result",
    "title_selector": ".title",
    "link_selector": ".title",
}


def test_full_width_symbols_spaces_and_book_title_wrappers():
    assert _match_grade("三 体", "三体") == "exact"
    assert _match_grade("《三体》免费阅读", "三体") == "exact"
    assert _match_grade("《三体》第二部", "三体") == "title"
    assert _match_grade("三体：黑暗森林", "三体") == "title"
    assert _match_grade("宇宙故事", "三体") == "unrelated"
    assert _match_grade("作品甲", "三体", "作者：三体") == "context"
    assert _quality({"title": "三体"}, "三体") == 0


def test_css_parser_discards_unrelated_recommendations_and_navigation():
    html = """
    <div class="result"><a class="title" href="/book/1">《三体》免费阅读</a>
        <span>完结</span></div>
    <div class="result"><a class="title" href="/book/2">另一本小说</a>
        <span>网站推荐</span></div>
    <div class="result"><a class="title" href="/book/3">宇宙航行</a>
        <span>作者：三体</span></div>
    <div class="result"><a class="title" href="/book/1#toc">《三体》免费阅读</a></div>
    <div class="result"><a class="title" href="/search?word=1">搜索</a></div>
    <div class="result"><a class="title"
         href="https://tracker.other.org/collect">三体</a></div>
    """
    results = extract_search_results(SITE, html, SITE["base_url"] + "/search?q=x", "三体")
    assert len(results) == 2
    assert results[0]["title"] == "《三体》免费阅读"
    assert results[0]["match_grade"] == "exact"
    assert results[1]["title"] == "宇宙航行"
    assert results[1]["match_grade"] == "context"
    assert all(r["url"].startswith(SITE["base_url"]) for r in results)


def test_heuristic_search_normalizes_punctuation_without_false_results():
    html = """<a href="/book/a">三·体</a>
    <a href="/book/b">天龙八部</a><a href="/book/c">三 体（全本）</a>"""
    rows = heuristic_search_results(SITE, html, SITE["base_url"] + "/search", "三体")
    assert [r["match_grade"] for r in rows] == ["exact", "title"]
    assert len(rows) == 2


def test_css_context_only_match_is_neutral_not_verified_success(monkeypatch):
    from smartresource import search
    observed = []
    async def fake_fetch(*args, **kwargs):
        return ("""
          <div class="result"><a href="/book/3" class="title">海天之书</a>
          <span>作者：三体</span></div>
        """, "http")
    monkeypatch.setattr(search, "fetch_analysis_html", fake_fetch)
    monkeypatch.setattr(search, "observe", lambda *args: observed.append(args))
    rows, warning = asyncio.run(fetch_site_results(SITE, "三体"))
    assert len(rows) == 1 and rows[0]["match_grade"] == "context"
    assert warning is None
    assert observed == [(7, "search", "neutral", "heuristic_results")]


def test_no_matches_keeps_normal_empty_results_not_network_failure(monkeypatch):
    from smartresource import search
    observed = []
    async def fake_fetch(*args, **kwargs):
        return ("""<div class="result">
          <a class="title" href="/book/other">其他作品</a>
          </div>""", "http")
    monkeypatch.setattr(search, "fetch_analysis_html", fake_fetch)
    monkeypatch.setattr(search, "observe", lambda *args: observed.append(args))
    rows, warning = asyncio.run(fetch_site_results(SITE, "三体"))
    assert rows == []
    assert warning
    assert observed == [(7, "search", "neutral", "empty_results")]
