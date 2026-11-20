"""Search-result next-link inference is conservative and network-free."""
import asyncio
from urllib.parse import quote_plus

from smartresource import search
from smartresource.search_pagination import discover_next_search_page


BASE = "https://read.example.org/search?wd=" + quote_plus("三体")


def test_accepts_labeled_adjacent_page_and_normalizes_fragment():
    html = '<nav class="pagination"><a href="?wd=%E4%B8%89%E4%BD%93&page=2#top">下一页 ›</a></nav>'
    assert discover_next_search_page(html, BASE, "read.example.org") == (
        "https://read.example.org/search?wd=%E4%B8%89%E4%BD%93&page=2"
    )
    page2 = "https://read.example.org/search?wd=%E4%B8%89%E4%BD%93&page=2"
    html2 = '<a rel="next" href="?page=3&wd=%E4%B8%89%E4%BD%93">→</a>'
    assert discover_next_search_page(html2, page2, "read.example.org") == (
        "https://read.example.org/search?page=3&wd=%E4%B8%89%E4%BD%93"
    )


def test_rejects_wrong_host_path_query_scheme_credentials_and_page_jump():
    candidates = [
        "https://evil.example.org/search?wd=%E4%B8%89%E4%BD%93&page=2",
        "https://read.example.org/video?wd=%E4%B8%89%E4%BD%93&page=2",
        "https://read.example.org/search?wd=%E5%85%B6%E4%BB%96&page=2",
        "http://read.example.org/search?wd=%E4%B8%89%E4%BD%93&page=2",
        "https://user:password@read.example.org/search?wd=%E4%B8%89%E4%BD%93&page=2",
        "https://read.example.org/search?wd=%E4%B8%89%E4%BD%93&page=9",
        "https://read.example.org/search?wd=%E4%B8%89%E4%BD%93&page=2&debug=1",
        "javascript:alert(1)",
    ]
    for target in candidates:
        html = f'<a rel="next" href="{target}">下一页</a>'
        assert discover_next_search_page(html, BASE, "read.example.org") is None


def test_rejects_ambiguous_or_unlabeled_pagination():
    two = """
        <a rel="next" href="?wd=%E4%B8%89%E4%BD%93&page=2">下一页</a>
        <a rel="next" href="?wd=%E4%B8%89%E4%BD%93&p=2">下一页</a>
    """
    assert discover_next_search_page(two, BASE, "read.example.org") is None
    unlabeled = '<a href="?wd=%E4%B8%89%E4%BD%93&page=2">页码 2</a>'
    assert discover_next_search_page(unlabeled, BASE, "read.example.org") is None
    previous = '<a href="?wd=%E4%B8%89%E4%BD%93&page=2">上一页</a>'
    assert discover_next_search_page(previous, BASE, "read.example.org") is None


def test_allows_p_parameter_and_next_page_title():
    html = '<a aria-label="Next Page" href="?wd=%E4%B8%89%E4%BD%93&p=2">2</a>'
    assert discover_next_search_page(html, BASE, "read.example.org") == (
        "https://read.example.org/search?wd=%E4%B8%89%E4%BD%93&p=2"
    )


def test_fast_discovers_page_then_full_reuses_first_and_follows_chain(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("SMARTRESOURCE_DB", str(tmp_path / "auto-pagination.sqlite"))
    monkeypatch.setattr(search, "observe", lambda *args: None)
    site = {
        "id": 919, "name": "阅读站", "kind": "novel",
        "base_url": "https://read.example.org",
        "search_url_template": "https://read.example.org/search?wd={q}",
        "item_selector": ".work",
        "title_selector": ".title",
        "link_selector": ".title",
        "scan_status": "probe_passed",
    }
    fetched = []

    async def fake_html(url, host, http_fetch=None):
        assert host == "read.example.org"
        fetched.append(url)
        page = 1
        if "page=2" in url:
            page = 2
        elif "page=3" in url:
            page = 3
        html = (
            f'<div class="work"><a class="title" href="/book/{page}">三体</a></div>'
        )
        if page < 3:
            html += (
                f'<a class="next" href="?wd=%E4%B8%89%E4%BD%93&page={page + 1}">'
                '下一页</a>'
            )
        return html, "http"

    monkeypatch.setattr(search, "fetch_analysis_html", fake_html)
    quick = asyncio.run(search.search_all([site], "三体", mode="fast"))
    assert len(quick["items"]) == 1
    assert quick["paginated_sites"] == 1
    assert quick["discovered_pagination_sites"] == 1
    assert quick["extra_pages_fetched"] == 0
    assert len(fetched) == 1

    wide = asyncio.run(search.search_all([site], "三体", mode="full"))
    assert wide["reused_sites"] == 1
    assert wide["paginated_sites"] == 1
    assert wide["discovered_pagination_sites"] == 1
    assert wide["extra_pages_fetched"] == 2
    assert [hit["url"] for hit in wide["items"]] == [
        "https://read.example.org/book/1",
        "https://read.example.org/book/2",
        "https://read.example.org/book/3",
    ]
    assert len(fetched) == 3
    again = asyncio.run(search.search_all([site], "三体", mode="full"))
    assert again["cached"]
    assert len(fetched) == 3


def test_direct_full_discovers_without_preceding_quick(tmp_path, monkeypatch):
    monkeypatch.setenv("SMARTRESOURCE_DB", str(tmp_path / "direct-full.sqlite"))
    monkeypatch.setattr(search, "observe", lambda *args: None)
    site = {
        "id": 918, "name": "小说站", "kind": "novel",
        "base_url": "https://read.example.org",
        "search_url_template": "https://read.example.org/search?wd={q}",
        "item_selector": ".work", "title_selector": ".title",
        "link_selector": ".title",
    }
    visits = []

    async def fake_html(url, host, http_fetch=None):
        visits.append(url)
        if "page=2" in url:
            return '<div class="work"><a class="title" href="/book/2">三体</a></div>', "http"
        return (
            '<div class="work"><a class="title" href="/book/1">三体</a></div>'
            '<a rel="next" href="?wd=%E4%B8%89%E4%BD%93&page=2">下一页</a>'
        ), "http"

    monkeypatch.setattr(search, "fetch_analysis_html", fake_html)
    report = asyncio.run(search.search_all([site], "三体", mode="full"))
    assert report["extra_pages_fetched"] == 1
    assert len(report["items"]) == 2
    assert len(visits) == 2
