"""Small, conservative episode-link hints from one public video detail page.

Never extracts media streams or executes scripts. Links are only suggestions;
actual playback stays in the user's Android WebView on the original website.
"""
import re
from urllib.parse import urljoin, urlsplit, urldefrag

from bs4 import BeautifulSoup, Tag

EPISODE_LABEL = re.compile(
    r"^(?:(?:第\s*[0-9一二三四五六七八九十两]+\s*季\s*)?第\s*[0-9一二三四五六七八九十百千零〇两]+\s*[集期话]"
    r"|(?:ep(?:isode)?|s\d{1,2}\s*e)\s*[-.:# ]*\s*\d{1,3}\b"
    r"|\d{1,3}\s*[集期话]\b|正片(?:\s*\d+)?$)",
    re.I,
)
NUMBER_LABEL = re.compile(r"^\d{1,3}$")
PLAYLIST_MARKER = re.compile(
    r"play[-_ ]?list|playlist|episode[-_ ]?list|anthology|module[-_ ]?play"
    r"|选集|剧集|分集|播放列表|播放线路|播放地址",
    re.I,
)
PAGER_MARKER = re.compile(
    r"(?:^|[\s_-])(?:pagination|page-nav|pager|page-list)(?:$|[\s_-])", re.I
)

MAX_EPISODES = 80
_SERIES_EPISODE = re.compile(
    r"^(?:第\\s*(\\d{1,2})\\s*季\\s*)?第\\s*(\\d{1,3})\\s*[集期话]"
    r"|^S(\\d{1,2})E(\\d{1,3})\\b"
    r"|^(?:EP|Episode)\\s*[-.:# ]*\\s*(\\d{1,3})\\b"
    r"|^(\\d{1,3})\\s*[集期话]?\\s*$",
    re.I,
)


def _episode_numbers(title: str, numbered_playlist: bool = False):
    match = _SERIES_EPISODE.search(title)
    if match is None:
        return None, None
    if match.group(1) or match.group(2):
        season = int(match.group(1)) if match.group(1) else None
        episode = int(match.group(2))
    elif match.group(3):
        season, episode = int(match.group(3)), int(match.group(4))
    elif match.group(5):
        season, episode = None, int(match.group(5))
    else:
        if not numbered_playlist:
            return None, None
        season, episode = None, int(match.group(6))
    if not 1 <= episode <= 999:
        return None, None
    return season, episode


def _playlist_context(link: Tag) -> bool:
    node = link
    found_playlist = False
    for _ in range(5):
        node = node.parent
        if not isinstance(node, Tag):
            break
        marker = " ".join(str(c) for c in node.get("class", []))
        marker += " " + str(node.get("id", ""))
        if PAGER_MARKER.search(marker):
            return False
        if PLAYLIST_MARKER.search(marker):
            found_playlist = True
    return found_playlist


def parse_episodes(html: str, page_url: str, expected_host: str) -> dict:
    """Best-effort episode navigation; do not assert these links are playable."""
    soup = BeautifulSoup(html, "html.parser")
    seen = set()
    items = []
    page = urldefrag(page_url)[0]
    for link in soup.select("a[href]")[:3000]:
        title = " ".join(link.get_text(" ", strip=True).split())
        if not title or len(title) > 48:
            continue
        if not EPISODE_LABEL.search(title):
            if not (NUMBER_LABEL.fullmatch(title) and _playlist_context(link)):
                continue
        elif not _playlist_context(link):
            # An explicit "第X集" label can stand alone, but skip pagination.
            if any(PAGER_MARKER.search(
                " ".join(str(c) for c in ancestor.get("class", []))
                + " " + str(ancestor.get("id", ""))
            ) for ancestor in list(link.parents)[:4] if isinstance(ancestor, Tag)):
                continue
        raw_href = (link.get("href") or "").strip()
        if not raw_href or raw_href.startswith(("#", "javascript:", "data:")):
            continue
        url = urldefrag(urljoin(page_url, raw_href))[0]
        parsed = urlsplit(url)
        try:
            port = parsed.port
        except ValueError:
            continue
        if (parsed.scheme not in ("http", "https")
                or parsed.hostname != expected_host
                or parsed.username is not None or parsed.password is not None
                or port not in (None, 80 if parsed.scheme == "http" else 443)
                or url == page or url in seen):
            continue
        seen.add(url)
        season, episode = _episode_numbers(title, _playlist_context(link))
        item = {"title": title, "url": url}
        if season is not None:
            item["season_number"] = season
        if episode is not None:
            item["episode_number"] = episode
        items.append(item)
        if len(items) >= MAX_EPISODES:
            break
    return {
        "status": "candidates" if items else "none",
        "items": items,
        "note": (
            "仅从作品详情页识别公开网页选集链接，未验证是否可播放或是否完整。"
            if items else "此网页未识别出选集链接，可在原网站手动选集。"
        ),
    }
