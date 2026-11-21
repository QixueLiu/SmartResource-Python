"""Offline regression tests for search-type priority and video title precision."""
import asyncio

from smartresource import search
from smartresource.search_quality import video_core_matches_query


def site(number, kind):
    return {
        "id": number,
        "name": f"测试站点{number}",
        "kind": kind,
        "base_url": f"https://source{number}.example.org",
        "search_url_template": f"https://source{number}.example.org/?q={{q}}",
        "scan_status": "probe_passed",
    }


def test_video_core_match_is_evidence_not_same_film_verification():
    row = {"kind": "video", "title": "《三体》 (2023) 高清"}
    assert video_core_matches_query(row, "三体")
    assert video_core_matches_query(row, "三体 2023")
    assert not video_core_matches_query(row, "三体 2024")
    assert not video_core_matches_query(row, "三体 第二季")
    assert not video_core_matches_query(
        {"kind": "video", "title": "三体第二部"}, "三体"
    )
    assert not video_core_matches_query(
        {"kind": "novel", "title": "三体 (2023)"}, "三体"
    )
    assert not video_core_matches_query(
        {"kind": "mixed", "title": "三体 (2023)"}, "三体"
    )
    assert not video_core_matches_query(
        {"kind": "video", "title": "三体 电影版"}, "三体 电视剧版"
    )
    assert not video_core_matches_query(
        {"kind": "video", "title": "三体 第二季"}, "三体 第一季"
    )


def test_fast_video_search_queries_configured_video_sites_before_mixed(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("SMARTRESOURCE_DB", str(tmp_path / "type-priority.sqlite"))
    chosen = []

    async def fake_fetch(record, query):
        chosen.append(record["id"])
        return ([{
            "title": query, "url": record["base_url"] + "/watch",
            "site_id": record["id"], "site_name": record["name"],
            "kind": record["kind"], "match_grade": "exact"
        }], None)

    monkeypatch.setattr(search, "fetch_site_results", fake_fetch)
    # These 30 high-health mixed sites used to consume all 24 fast slots
    # ahead of specifically configured video sites.
    monkeypatch.setattr(search, "health_map", lambda ids: {
        i: {"score": 95 if i < 100 else 15, "samples": 10} for i in ids
    })
    monkeypatch.setattr(search, "rank_sites", lambda sites, metrics: sorted(
        sites, key=lambda s: -metrics[s["id"]]["score"]
    ))
    sites = [site(i, "mixed") for i in range(1, 31)]
    sites += [site(i, "video") for i in range(101, 111)]
    result = asyncio.run(search.search_all(
        sites, "三体", kind="video", mode="fast", refresh=True
    ))
    assert result["searched_sites"] == 24
    assert result["skipped_sites"] == 16
    assert {i for i in chosen if i >= 100} == set(range(101, 111))
    assert len({i for i in chosen if i < 100}) == 14
    assert result["searchable_sites"] == 40


def test_video_core_title_ranks_before_loose_substring_if_evidence_equal(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("SMARTRESOURCE_DB", str(tmp_path / "video-core.sqlite"))

    async def fake_fetch(record, query):
        title = "三体第二部" if record["id"] == 1 else "《三体》(2023) 高清"
        return ([{
            "title": title, "url": record["base_url"] + "/play",
            "site_id": record["id"], "site_name": record["name"],
            "kind": "video", "extraction": "css",
            "match_grade": "title"
        }], None)

    monkeypatch.setattr(search, "fetch_site_results", fake_fetch)
    monkeypatch.setattr(search, "rank_sites", lambda sites, metrics: sites)
    monkeypatch.setattr(search, "health_map", lambda ids: {
        1: {"score": 95, "samples": 9},
        2: {"score": 10, "samples": 9},
    })
    found = asyncio.run(search.search_all(
        [site(1, "video"), site(2, "video")], "三体",
        kind="video", refresh=True
    ))
    assert [item["site_id"] for item in found["items"]] == [2, 1]
    assert [item["video_title_core_match"] for item in found["items"]] == [
        True, False
    ]
    assert all(item["same_title_verified"] is False for item in found["items"])


def test_novel_search_order_and_uncertain_types_are_unchanged(tmp_path, monkeypatch):
    monkeypatch.setenv("SMARTRESOURCE_DB", str(tmp_path / "novel-priority.sqlite"))
    chosen = []

    async def fake_fetch(record, query):
        chosen.append(record["kind"])
        return [], None

    monkeypatch.setattr(search, "fetch_site_results", fake_fetch)
    sources = [site(i, "mixed") for i in range(1, 30)]
    sources += [site(i, "novel") for i in range(100, 105)]
    result = asyncio.run(search.search_all(
        sources, "三体", kind="novel", mode="fast", refresh=True
    ))
    assert result["searched_sites"] == 24
    assert chosen.count("novel") == 5
    assert chosen.count("mixed") == 19
    assert all("video_title_core_match" not in item for item in result["items"])
