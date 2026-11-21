"""Conservative URL dedup and best-evidence preservation, offline only."""
import asyncio

from smartresource import search


def test_tracking_parameters_are_not_individual_search_results():
    identity = search._dedup_key
    clean = "https://VIDEO.Example.org/watch/42"
    assert identity(clean + "/#player") == identity(clean)
    assert identity(clean + "?utm_source=newsletter&fbclid=123") == identity(clean)
    assert identity(clean + "?%75tm_campaign=a&gclid=b") == identity(clean)
    assert identity(clean + "?episode=2&utm_medium=social") == identity(
        clean + "?episode=2"
    )


def test_playback_essential_queries_are_never_discarded():
    identity = search._dedup_key
    base = "https://video.example.org/play/42?"
    assert identity(base + "episode=1") != identity(base + "episode=2")
    assert identity(base + "token=first") != identity(base + "token=second")
    assert identity(base + "X-Amz-Signature=ABC") != identity(
        base + "X-Amz-Signature=XYZ"
    )
    assert identity(base + "id=1&part=2") != identity(base + "part=2&id=1")
    assert identity(base + "id=1&id=2") != identity(base + "id=2&id=1")
    assert identity(base + "quality=hd") != identity(base + "quality=sd")
    assert identity("http://video.example.org/a") != identity(
        "https://video.example.org/a"
    )


def test_duplicate_keeps_better_evidence_without_dropping_episode_urls(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("SMARTRESOURCE_DB", str(tmp_path / "dedup.sqlite"))
    site = {
        "id": 23, "name": "演示", "kind": "video",
        "base_url": "https://video.example.org",
        "search_url_template": "https://video.example.org/search?q={q}",
        "scan_status": "probe_passed",
    }

    async def fake_fetch(record, query):
        root = record["base_url"] + "/watch/123"
        return ([
            {
                "title": "三体第二部", "url": root + "?utm_source=home",
                "site_id": 23, "site_name": "演示", "kind": "video",
                "match_grade": "title", "extraction": "heuristic",
            },
            {
                "title": "三体", "url": root + "#watch",
                "site_id": 23, "site_name": "演示", "kind": "video",
                "match_grade": "exact", "extraction": "css",
            },
            {
                "title": "三体 第1集", "url": root + "?episode=1",
                "site_id": 23, "site_name": "演示", "kind": "video",
                "match_grade": "title", "extraction": "css",
            },
            {
                "title": "三体 第2集", "url": root + "?episode=2",
                "site_id": 23, "site_name": "演示", "kind": "video",
                "match_grade": "title", "extraction": "css",
            },
        ], None)

    monkeypatch.setattr(search, "fetch_site_results", fake_fetch)
    result = asyncio.run(search.search_all(
        [site], "三体", mode="fast", kind="video", refresh=True
    ))
    assert result["collapsed_duplicate_urls"] == 1
    assert len(result["items"]) == 3
    assert result["items"][0]["title"] == "三体"
    assert result["items"][0]["url"].endswith("#watch")
    assert {row["url"].split("episode=")[-1] for row in result["items"][1:]} == {
        "1", "2"
    }
    assert all(row["same_title_verified"] is False for row in result["items"])


def test_same_title_different_sites_are_distinct():
    assert search._dedup_key("https://a.example.org/show/1") != (
        search._dedup_key("https://b.example.org/show/1")
    )
