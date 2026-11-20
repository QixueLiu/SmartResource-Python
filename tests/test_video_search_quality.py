"""Video-specific title and episode heuristics; no live site, stream or API."""
from smartresource.search_grouping import (
    annotate_same_title_candidates, title_group_key
)
from smartresource.video_titles import video_title_details
from smartresource.video_episodes import parse_episodes


def video(title, site=1):
    return {"title": title, "site_id": site, "kind": "video",
            "url": f"https://video{site}.example.org/show/{site}"}


def test_edition_year_and_season_keep_distinct_titles_separate():
    candidates = [
        video("《三体》 (2023) 高清", 1),
        video("三体 (2023) 在线播放", 2),
        video("三体 (2024)", 3),
        video("三体", 4),
        video("三体 第一季", 5),
        video("三体 第二季", 6),
        video("三体电影版", 7),
        video("三体电视剧版", 8),
    ]
    annotate_same_title_candidates(candidates)
    assert candidates[0]["title_group_key"] == candidates[1]["title_group_key"]
    assert candidates[0]["same_title_source_count"] == 2
    assert candidates[0]["video_release_year"] == 2023
    assert candidates[1]["video_release_year"] == 2023
    assert candidates[2]["video_release_year"] == 2024
    assert candidates[3]["video_release_year"] is None
    assert candidates[4]["video_season_number"] == 1
    assert candidates[5]["video_season_number"] == 2
    assert candidates[6]["video_edition"] == "电影版"
    assert candidates[7]["video_edition"] == "电视剧版"
    assert len({v["title_group_key"] for v in candidates}) == 7
    assert all(not v["same_title_verified"] for v in candidates)


def test_video_title_numeral_and_year_disambiguation():
    assert video_title_details("三体（2023年）")["video_release_year"] == 2023
    assert video_title_details("剧名 S02E03")["video_season_number"] == 2
    assert video_title_details("流浪地球2")["video_title_key"] != (
        video_title_details("流浪地球")["video_title_key"])
    assert video_title_details("《三体》第二部")["video_title_key"] != (
        video_title_details("《三体》")["video_title_key"])
    assert title_group_key({"kind": "video", "title": "A"}) == ""
    assert title_group_key({"kind": "other", "title": "三体(2023)"}) == ""


def test_episode_links_provide_numeric_season_and_episode_metadata():
    html = """
    <div class="play-list">
      <a href="/watch/12">第1季第12集</a>
      <a href="/watch/13">S02E03</a>
      <a href="/watch/14">EP 07</a>
      <a href="/watch/15">Episode 6</a>
      <a href="/watch/16">05</a>
      <a href="/watch/full">正片</a>
    </div>
    <div class="pagination"><a href="/page/3">第3集</a></div>
    """
    report = parse_episodes(
        html, "https://movies.example.org/show/1", "movies.example.org"
    )
    assert [item["title"] for item in report["items"]] == [
        "第1季第12集", "S02E03", "EP 07", "Episode 6", "05", "正片"
    ]
    assert report["items"][0]["season_number"] == 1
    assert report["items"][0]["episode_number"] == 12
    assert report["items"][1]["season_number"] == 2
    assert report["items"][1]["episode_number"] == 3
    assert [entry.get("episode_number") for entry in report["items"][2:]] == [
        7, 6, 5, None
    ]


def test_explicit_number_does_not_confuse_pagination_or_cross_site():
    html = """
    <div class="pagination"><a href="/page/4">04</a></div>
    <a href="https://outside.example.org/watch/2">第2集</a>
    <div class="play-list">
      <a href="javascript:void(0)">第1季第1集</a>
      <a href="/watch/7#time=5">第2季第7集</a>
    </div>
    """
    result = parse_episodes(
        html, "https://movies.example.org/show/1", "movies.example.org"
    )
    assert result["items"] == [{
        "title": "第2季第7集", "url": "https://movies.example.org/watch/7",
        "season_number": 2, "episode_number": 7,
    }]
