"""Fast/full single-user search: coverage, ranking, bounded cache, safe status."""
import asyncio

import pytest
from fastapi.testclient import TestClient
from smartresource import search
from smartresource.main import app


def site(number, kind="novel", configured=True):
    host = f"source{number}.example.org"
    return {
        "id": number, "name": f"站点{number}", "kind": kind,
        "base_url": f"https://{host}",
        "search_url_template": f"https://{host}/search?q={{q}}" if configured else None,
        "item_selector": None, "scan_status": "probe_passed"
    }


def query(sites, text="故事", **options):
    return asyncio.run(search.search_all(sites, text, **options))


def test_fast_skips_unconfigured_without_consuming_the_24_slots(monkeypatch, tmp_path):
    monkeypatch.setenv("SMARTRESOURCE_DB", str(tmp_path / "f.sqlite"))
    counts = []
    async def fake(site_row, q):
        counts.append(site_row["id"])
        return ([{"title": q, "url": site_row["base_url"] + "/book/1",
                  "site_id": site_row["id"], "site_name": site_row["name"],
                  "kind": site_row["kind"]}], None)
    monkeypatch.setattr(search, "fetch_site_results", fake)
    sites = [site(i, configured=False) for i in range(1, 51)] + [
        site(i) for i in range(51, 81)
    ]
    result = query(sites)
    assert result["searched_sites"] == 24
    assert result["completed_sites"] == 24
    assert result["searchable_sites"] == 30
    assert result["unconfigured_sites"] == 50
    assert result["skipped_sites"] == 6
    assert result["timed_out_sites"] == 0
    assert len(result["items"]) == 24
    assert all(i > 50 for i in counts)


def test_full_mode_expands_coverage_and_caches_repeat_queries(monkeypatch, tmp_path):
    monkeypatch.setenv("SMARTRESOURCE_DB", str(tmp_path / "wide.sqlite"))
    calls = []
    async def fake(record, q):
        calls.append(record["id"])
        return ([{"title": q, "url": record["base_url"] + "/book",
                  "site_id": record["id"], "site_name": record["name"], "kind": "novel"}], None)
    monkeypatch.setattr(search, "fetch_site_results", fake)
    sources = [site(i) for i in range(1, 31)]
    fast = query(sources, "大作", mode="fast")
    full = query(sources, "大作", mode="full")
    assert len(fast["items"]) == 24
    assert len(full["items"]) == 30
    assert full["searched_sites"] == 30
    assert len(calls) == 30
    assert full["reused_sites"] == 24
    again = query(sources, "大作", mode="full")
    assert again["cached"] is True
    assert again["elapsed_ms"] == 0
    assert len(calls) == 30
    forced = query(sources, "大作", mode="full", refresh=True)
    assert forced["cached"] is False
    assert len(calls) == 60
    assert forced["reused_sites"] == 0


def test_exact_titles_rank_above_fuzzy_and_duplicates_are_removed(monkeypatch, tmp_path):
    monkeypatch.setenv("SMARTRESOURCE_DB", str(tmp_path / "rank.sqlite"))
    async def fake(record, q):
        url = record["base_url"]
        return ([
            {"title": "无关前缀" + q, "url": url + "/book/1#toc",
             "site_id": record["id"], "site_name": record["name"], "kind": "novel"},
            {"title": q + "（全文）", "url": url + "/book/1/",
             "site_id": record["id"], "site_name": record["name"], "kind": "novel"},
            {"title": " 故 事 ", "url": url + "/book/2",
             "site_id": record["id"], "site_name": record["name"], "kind": "novel"},
        ], None)
    monkeypatch.setattr(search, "fetch_site_results", fake)
    result = query([site(1)], "故事")
    assert len(result["items"]) == 2
    assert result["items"][0]["title"] == " 故 事 "
    assert result["items"][1]["title"].startswith("无关前缀")


def test_type_filter_includes_mixed_sites_without_mislabeling(monkeypatch, tmp_path):
    monkeypatch.setenv("SMARTRESOURCE_DB", str(tmp_path / "type.sqlite"))
    used = []
    async def fake(record, q):
        used.append(record["id"])
        return [], None
    monkeypatch.setattr(search, "fetch_site_results", fake)
    source = [site(1, "novel"), site(2, "video"), site(3, "mixed"), site(4, "other")]
    report = query(source, kind="video")
    assert report["searched_sites"] == 3
    assert set(used) == {2, 3, 4}
    assert report["kind"] == "video"


def test_search_timeout_not_reported_as_completed(monkeypatch, tmp_path):
    monkeypatch.setenv("SMARTRESOURCE_DB", str(tmp_path / "timeout.sqlite"))
    monkeypatch.setattr(search, "MAX_SEARCH_SECONDS", 0.02)
    async def slow(record, q):
        await asyncio.sleep(1)
        return [], None
    monkeypatch.setattr(search, "fetch_site_results", slow)
    result = query([site(1), site(2)])
    assert result["searched_sites"] == 2
    assert result["completed_sites"] == 0
    assert result["timed_out_sites"] == 2
    assert len(result["warnings"]) == 2


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SMARTRESOURCE_DB", str(tmp_path / "api.sqlite"))
    monkeypatch.setenv("SMARTRESOURCE_API_TOKEN", "personal-token")
    with TestClient(app) as http:
        yield http


def test_search_api_rejects_bad_types_and_blank_query(client):
    headers = {"X-API-Key": "personal-token"}
    assert client.get("/api/search?q=a").status_code == 401
    assert client.get("/api/search?q=%20%20", headers=headers).status_code == 422
    assert client.get("/api/search?q=a&mode=unknown", headers=headers).status_code == 422
    assert client.get("/api/search?q=a&kind=movie", headers=headers).status_code == 422
    assert client.get("/api/search?q=a&mode=full&kind=video", headers=headers).status_code == 200



def test_expired_css_rules_use_low_confidence_keyword_fallback(monkeypatch, tmp_path):
    monkeypatch.setenv("SMARTRESOURCE_DB", str(tmp_path / "fallback.sqlite"))
    from smartresource import search
    template = site(9)
    template.update({
        "item_selector": ".old-result",
        "title_selector": "a.title",
        "link_selector": "a.title",
    })
    async def sample(url, host, http_fetch=None):
        return (
            '<html><a href="/book/9">春风故事</a>'
            '<a href="/book/10">其他故事</a></html>', "http"
        )
    monkeypatch.setattr(search, "fetch_analysis_html", sample)
    rows, warning = asyncio.run(search.fetch_site_results(template, "春风故事"))
    assert len(rows) == 1
    assert rows[0]["extraction"] == "heuristic"
    assert rows[0]["url"] == template["base_url"] + "/book/9"
    assert "未经验证" in warning


def test_no_css_or_heuristic_match_is_neutral(monkeypatch, tmp_path):
    monkeypatch.setenv("SMARTRESOURCE_DB", str(tmp_path / "fallback-empty.sqlite"))
    template = site(9)
    template.update({
        "item_selector": ".old-result",
        "title_selector": "a.title",
        "link_selector": "a.title",
    })
    async def sample(url, host, http_fetch=None):
        return '<html><a href="/book/9">毫不相关</a></html>', "http"
    monkeypatch.setattr(search, "fetch_analysis_html", sample)
    rows, warning = asyncio.run(search.fetch_site_results(template, "春风故事"))
    assert not rows
    assert "CSS" in warning
