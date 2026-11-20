"""Conservative same-title source presentation, not work-identity inference.

Titles alone cannot confirm author, edition or media identity. Never discard a
candidate or deduplicate unrelated URLs as part of this grouping.
"""
import re
import unicodedata
from collections import defaultdict
from .video_titles import video_title_details

_DECORATIVE_SUFFIX = (
    "最新", "全文", "全本", "免费", "在线", "目录", "章节",
    "阅读", "小说", "全集", "完整", "高清", "播放", "txt下载", "完结",
)
_WRAPPED_TITLE = re.compile(r"^\s*[《「『](.{1,120})[》」』](.*)$")


def normalized_title(title: str) -> str:
    return "".join(
        char for char in unicodedata.normalize("NFKC", title).casefold()
        if char.isalnum()
    )


def title_group_key(item: dict) -> str:
    """Strict same-title hint; sequels and unknown media types remain separate."""
    kind = item.get("kind")
    if kind not in ("novel", "video"):
        return ""
    title = str(item.get("title") or "").strip()
    if kind == "video":
        hints = video_title_details(title)
        item.update(hints)
        core = hints["video_title_key"]
        if not core:
            return ""
        # A missing year/season/edition is UNKNOWN, not equivalent to a
        # specific observed one. This intentionally favors precision.
        year = hints["video_release_year"] or "?"
        season = hints["video_season_number"] or "?"
        edition = hints["video_edition"] or "?"
        return f"video:{core}:year={year}:season={season}:edition={edition}"
    wrapped = _WRAPPED_TITLE.match(title)
    if wrapped:
        suffix = normalized_title(wrapped.group(2))
        if not suffix or suffix.startswith(_DECORATIVE_SUFFIX):
            title = wrapped.group(1)
    key = normalized_title(title)
    # Single-character or very long titles are particularly ambiguous.
    if len(key) < 2 or len(key) > 100:
        return ""
    return kind + ":" + key


def annotate_same_title_candidates(items: list[dict]) -> None:
    """Add presentation-only hints, leaving all URLs and order untouched."""
    grouped: dict[str, list[dict]] = defaultdict(list)
    for item in items:
        key = title_group_key(item)
        item["title_group_key"] = key
        if key:
            grouped[key].append(item)

    for key, group in grouped.items():
        count = len({
            str(row.get("site_id", "")) for row in group
            if row.get("site_id") is not None
        })
        for index, row in enumerate(group):
            row["same_title_source_count"] = count
            row["preferred_same_title_candidate"] = count >= 2 and index == 0
            # The UI must not claim a verified author/edition match.
            row["same_title_verified"] = False

    for item in items:
        if not item["title_group_key"]:
            item["same_title_source_count"] = 1
            item["preferred_same_title_candidate"] = False
            item["same_title_verified"] = False
