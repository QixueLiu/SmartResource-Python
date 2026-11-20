"""Same-title grouping is only a display hint; it never merges identities."""
import asyncio

from smartresource import search
from smartresource.search_grouping import (
    annotate_same_title_candidates, normalized_title, title_group_key
)


def row(title, kind="novel", site=1, url=None):
    return {
        "title": title, "kind": kind, "site_id": site,
        "url": url or f"https://books{site}.example.org/book/1",
        "site_name": f"站点{site}",
        "match_grade": "exact", "extraction": "css"
    }


def test_same_title_across_sites_keeps_every_candidate_and_flags_uncertainty():
    candidates = [
        row("三体", site=1),
        row("《三体》免费阅读", site=2),
        row("三 体", site=3)
    ]
    original_urls = [entry["url"] for entry in candidates]
    annotate_same_title_candidates(candidates)
    assert [entry["url"] for entry in candidates] == original_urls
    assert len({entry["title_group_key"] for entry in candidates}) == 1
    assert [entry["same_title_source_count"] for entry in candidates] == [3, 3, 3]
    assert [entry["preferred_same_title_candidate"] for entry in candidates] == [
        True, False, False
    ]
    assert all(entry["same_title_verified"] is False for entry in candidates)


def test_series_sequels_and_different_media_do_not_get_grouped():
    candidates = [
        row("《三体》第二部", site=1),
        row("三体", site=2),
        row("三体", site=3, kind="video"),
        row("三体", site=4, kind="other"),
    ]
    annotate_same_title_candidates(candidates)
    assert len({item["title_group_key"] for item in candidates}) == 4
    assert all(item["same_title_source_count"] == 1 for item in candidates)


def test_same_site_multiple_hits_are_not_called_multiple_sources():
    values = [
        row("三体", site=1, url="https://books1.example.org/book/a"),
        row("三 体", site=1, url="https://books1.example.org/book/b"),
    ]
    annotate_same_title_candidates(values)
    assert [x["same_title_source_count"] for x in values] == [1, 1]
    assert all(not x["preferred_same_title_candidate"] for x in values)


def test_short_titles_and_ambiguous_wrapping_are_kept_distinct():
    assert normalized_title(" Ａ-Ｂ ") == "ab"
    assert title_group_key(row("仙")) == ""
    assert title_group_key(row("《三体》第二部")) != title_group_key(row("三体"))
    assert title_group_key(row("《三体》免费阅读")) == title_group_key(row("三体"))


def test_ranking_uses_real_health_data_only_with_same_relevance(tmp_path, monkeypatch):
    monkeypatch.setenv("SMARTRESOURCE_DB", str(tmp_path / "grouping.sqlite"))
    sources = [
        {
            "id": i, "name": f"站点{i}", "kind": "novel",
            "base_url": f"https://books{i}.example.org",
            "search_url_template": f"https://books{i}.example.org/search?q={{q}}"
        } for i in range(1, 4)
    ]
    async def search_one(source, query):
        return ([row(query, site=source["id"])], None)
    monkeypatch.setattr(search, "fetch_site_results", search_one)
    monkeypatch.setattr(search, "rank_sites", lambda sites, metrics: sites)
    monkeypatch.setattr(search, "health_map", lambda ids: {
        1: {"score": 95, "samples": 1, "status": "insufficient_data"},
        2: {"score": 80, "samples": 8, "status": "stable"},
        3: {"score": 20, "samples": 8, "status": "unstable"},
    })
    result = asyncio.run(search.search_all(sources, "三体", refresh=True))
    assert [hit["site_id"] for hit in result["items"]] == [2, 1, 3]
    assert [hit["same_title_source_count"] for hit in result["items"]] == [3, 3, 3]
    assert result["items"][0]["preferred_same_title_candidate"]
    assert not any(hit["same_title_verified"] for hit in result["items"])


def test_title_relevance_overrides_site_health(tmp_path, monkeypatch):
    monkeypatch.setenv("SMARTRESOURCE_DB", str(tmp_path / "priority.sqlite"))
    sources = [
        {"id": i, "name": f"源{i}", "kind": "novel",
         "base_url": f"https://books{i}.example.org",
         "search_url_template": f"https://books{i}.example.org/search?q={{q}}"}
        for i in (1, 2)
    ]
    async def search_one(source, q):
        grade = "exact" if source["id"] == 1 else "context"
        return ([{**row("三体" if grade == "exact" else "宇宙故事",
                        site=source["id"]), "match_grade": grade}], None)
    monkeypatch.setattr(search, "fetch_site_results", search_one)
    monkeypatch.setattr(search, "rank_sites", lambda sites, metrics: sites)
    monkeypatch.setattr(search, "health_map", lambda ids: {
        1: {"score": 20, "samples": 9}, 2: {"score": 99, "samples": 9}
    })
    found = asyncio.run(search.search_all(sources, "三体", refresh=True))
    assert [hit["site_id"] for hit in found["items"]] == [1, 2]
