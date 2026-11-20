"""Video search-title hints; never claim film/series identity from titles alone.

Year, season and explicit adaptation labels are *display metadata*. These
conservative hints keep unlike editions apart without fetching other sites.
"""
import re
import unicodedata

_YEAR = re.compile(
    r"(?<![0-9A-Za-z\u4e00-\u9fff])((?:19|20)\d{2})"
    r"(?![0-9A-Za-z\u4e00-\u9fff])"
)
_SEASON = re.compile(
    r"第\s*([0-9一二三四五六七八九十两]+)\s*季"
    r"|\bseason\s*0?([0-9]{1,2})\b",
    re.I
)
_EDITION = re.compile(r"电影版|电视剧版|动画版|剧场版|真人版|纪录片版")
_PROMO = re.compile(
    r"(?:\s*[-_|·：:（）()【】\[\] ]*\s*)"
    r"(?:高清|超清|蓝光|1080[pP]|720[pP]|4[kK]|HD|BD|"
    r"免费在线观看|在线观看|在线播放|中字|国语|粤语|"
    r"完整版|全集|正片|无删减|完结|免费阅读)\s*$",
    re.I,
)
_WRAPPED = re.compile(r"^\s*[《「『](.{1,120})[》」』](.*)$")
_NUMERALS = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}


def _normalize(value: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKC", value).casefold()
        if c.isalnum()
    )


def _season_number(text: str) -> int | None:
    found = _SEASON.search(text)
    if not found:
        return None
    raw = found.group(1) or found.group(2)
    if raw.isdigit():
        value = int(raw)
        return value if 1 <= value <= 99 else None
    return _NUMERALS.get(raw)


def video_title_details(title: str) -> dict:
    """Return cautious classification that is safe to put in an API response."""
    value = unicodedata.normalize("NFKC", str(title or "")).strip()
    year_match = _YEAR.search(value)
    year = int(year_match.group(1)) if year_match else None
    season = _season_number(value)
    edition_match = _EDITION.search(value)
    edition = edition_match.group(0) if edition_match else None

    cleaned = value
    if year_match:
        cleaned = _YEAR.sub(" ", cleaned, count=1)
    if season is not None:
        cleaned = _SEASON.sub(" ", cleaned, count=1)
    if edition:
        cleaned = cleaned.replace(edition, " ", 1)
    for _ in range(5):
        shortened = _PROMO.sub("", cleaned).strip()
        if shortened == cleaned:
            break
        cleaned = shortened
    # Strip only title wrappers and whitespace; preserve film sequels such as 2.
    wrapped = _WRAPPED.fullmatch(cleaned)
    if wrapped and not wrapped.group(2).strip():
        cleaned = wrapped.group(1).strip()
    normalized = _normalize(cleaned)
    if not normalized or len(normalized) < 2 or len(normalized) > 100:
        normalized = ""

    return {
        "video_release_year": year,
        "video_season_number": season,
        "video_edition": edition,
        "video_title_core": cleaned[:200],
        "video_title_key": normalized,
    }
