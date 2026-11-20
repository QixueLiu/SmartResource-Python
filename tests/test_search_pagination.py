"""Search pagination is explicit, bounded, deduplicated and opt-in to full mode."""
import asyncio

from smartresource import search


def make_site():
    return {
        "id": 570, "name": "示例作品站", "kind": "novel",
        "base_url": "https://novel.example.org",
        "search_url_template": "https://novel.example.org/search?kw={q}&page={page}",
        "item_selector": ".item", "title_selector": "a.title",
        "link_selector": "a.title", "scan_status": "probe_passed",
    }


def first_result(site, keyword):
    return [{
        "title": keyword, "url": site["base_url"] + "/book/1",
        "site_id": site["id"], "site_name": site["name"], "kind": site["kind"],
        "match_grade": "exact", "extraction": "css"
    }]


def test_full_fetches_up_to_three_pages_reusing_first_and_deduplicating(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("SMARTRESOURCE_DB", str(tmp_path / "pages.sqlite"))
    site = make_site()
    calls = []

    async def fetch_first(record, keyword):
        calls.append(("first", keyword))
        return first_result(record, keyword), None

    async def fake_html(url, host, http_fetch=None):
        assert host == "novel.example.org"
        assert "kw=%E4%B8%89%E4%BD%93" in url
        page = int(url.rsplit("page=", 1)[-1])
        calls.append(("page", page))
        if page == 2:
            return ("""
                <div class="item"><a class="title" href="/book/1/#chapter">三体</a></div>
                <div class="item"><a class="title" href="/book/2">三体</a></div>
                <div class="item"><a class="title" href="https://another.example.org/book/9">三体</a></div>
            """, "http")
        if page == 3:
            return ('<div class="item"><a class="title" href="/book/3">三体</a></div>', "http")
        raise AssertionError("should not fetch page one via extra-page function")

    monkeypatch.setattr(search, "fetch_site_results", fetch_first)
    monkeypatch.setattr(search, "fetch_analysis_html", fake_html)

    fast = asyncio.run(search.search_all([site], "三体", mode="fast"))
    assert len(fast["items"]) == 1
    assert fast["extra_pages_fetched"] == 0
    assert fast["paginated_sites"] == 1
    assert calls == [("first", "三体")]

    full = asyncio.run(search.search_all([site], "三体", mode="full"))
    assert [item["url"] for item in full["items"]] == [
        site["base_url"] + "/book/1", site["base_url"] + "/book/2",
        site["base_url"] + "/book/3"
    ]
    assert full["reused_sites"] == 1
    assert full["extra_pages_fetched"] == 2
    assert full["paginated_sites"] == 1
    assert calls == [("first", "三体"), ("page", 2), ("page", 3)]

    repeated = asyncio.run(search.search_all([site], "三体", mode="full"))
    assert repeated["cached"] is True
    assert repeated["extra_pages_fetched"] == 2
    assert len(calls) == 3

    # A forced refresh bypasses both first-page and full-result caches.
    refreshed = asyncio.run(search.search_all([site], "三体", mode="full", refresh=True))
    assert refreshed["reused_sites"] == 0
    assert refreshed["extra_pages_fetched"] == 2
    assert calls[-3:] == [("first", "三体"), ("page", 2), ("page", 3)]


def test_sites_without_page_parameter_do_not_get_guessed_page_urls(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("SMARTRESOURCE_DB", str(tmp_path / "plain.sqlite"))
    site = make_site()
    site["search_url_template"] = "https://novel.example.org/search?kw={q}"
    async def fetch_first(record, keyword):
        return first_result(record, keyword), None
    async def no_more(*args, **kwargs):
        raise AssertionError("must not guess page syntax")
    monkeypatch.setattr(search, "fetch_site_results", fetch_first)
    monkeypatch.setattr(search, "fetch_additional_search_page", no_more)
    full = asyncio.run(search.search_all([site], "三体", mode="full"))
    assert len(full["items"]) == 1
    assert full["extra_pages_fetched"] == 0
    assert full["paginated_sites"] == 0


def test_empty_second_page_stops_without_page_three(tmp_path, monkeypatch):
    monkeypatch.setenv("SMARTRESOURCE_DB", str(tmp_path / "empty.sqlite"))
    site = make_site()
    pages = []
    async def fetch_first(record, keyword):
        return first_result(record, keyword), None
    async def fetch_more(record, keyword, page):
        pages.append(page)
        return [], None
    monkeypatch.setattr(search, "fetch_site_results", fetch_first)
    monkeypatch.setattr(search, "fetch_additional_search_page", fetch_more)
    full = asyncio.run(search.search_all([site], "三体", mode="full"))
    assert len(full["items"]) == 1
    assert pages == [2]
    assert full["extra_pages_fetched"] == 1


def test_repeated_page_is_not_requested_endlessly(tmp_path, monkeypatch):
    monkeypatch.setenv("SMARTRESOURCE_DB", str(tmp_path / "repeat.sqlite"))
    site = make_site()
    pages = []
    async def fetch_first(record, keyword):
        return first_result(record, keyword), None
    async def fetch_more(record, keyword, page):
        pages.append(page)
        return [{
            **first_result(record, keyword)[0],
            "url": record["base_url"] + "/book/1/#toc"
        }], None
    monkeypatch.setattr(search, "fetch_site_results", fetch_first)
    monkeypatch.setattr(search, "fetch_additional_search_page", fetch_more)
    full = asyncio.run(search.search_all([site], "三体", mode="full"))
    assert pages == [2]
    assert len(full["items"]) == 1


def test_extra_page_failure_keeps_first_page(tmp_path, monkeypatch):
    monkeypatch.setenv("SMARTRESOURCE_DB", str(tmp_path / "failed.sqlite"))
    site = make_site()
    calls = []
    async def fetch_first(record, keyword):
        return first_result(record, keyword), None
    async def fetch_more(record, keyword, page):
        calls.append(page)
        raise OSError("failure to fetch")
    monkeypatch.setattr(search, "fetch_site_results", fetch_first)
    monkeypatch.setattr(search, "fetch_additional_search_page", fetch_more)
    full = asyncio.run(search.search_all([site], "三体", mode="full"))
    assert len(full["items"]) == 1
    assert "后续分页读取失败" in full["warnings"][0]["message"]
    assert full["completed_sites"] == 1
    assert full["extra_pages_fetched"] == 0
    assert calls == [2]


def test_pagination_has_a_single_site_time_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("SMARTRESOURCE_DB", str(tmp_path / "timed.sqlite"))
    monkeypatch.setattr(search, "PER_SITE_SECONDS", 0.04)
    site = make_site()
    async def fetch_first(record, keyword):
        return first_result(record, keyword), None
    async def fetch_more(record, keyword, page):
        await asyncio.sleep(0.3)
        return [], None
    monkeypatch.setattr(search, "fetch_site_results", fetch_first)
    monkeypatch.setattr(search, "fetch_additional_search_page", fetch_more)
    full = asyncio.run(search.search_all([site], "三体", mode="full"))
    assert len(full["items"]) == 1
    assert "后续分页读取超时" in full["warnings"][0]["message"]
    assert full["extra_pages_fetched"] == 0
    assert full["completed_sites"] == 1


def test_extra_pages_never_exceed_site_result_cap():
    site = make_site()
    sample = first_result(site, "三体")[0]
    first = [{**sample, "url": site["base_url"] + "/book/" + str(i)} for i in range(60)]
    second = [{**sample, "url": site["base_url"] + "/book/" + str(i)} for i in range(60, 145)]
    combined = search._merge_site_pages(first, second)
    assert len(combined) == search.FULL_SITE_MAX_RESULTS == 100
    assert combined[0]["url"].endswith("/0")
    assert combined[-1]["url"].endswith("/99")
