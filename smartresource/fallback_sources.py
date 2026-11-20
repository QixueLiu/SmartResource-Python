"""Conservative alternate novel-source discovery.

Only enabled sites already registered by the private user are queried. A novel
title alone is never considered enough: the candidate book must have an exact
normalized title, an identifiable matching chapter, and accessible extracted
chapter text. The API returns candidates for explicit approval; it does not
silently replace an existing shelf entry.
"""
import asyncio
import re
from urllib.parse import urlsplit

from .browser_renderer import fetch_analysis_html
from .fetcher import fetch_html
from .reader import extract_reader_page, expand_toc_result
from .search import fetch_site_results

_ARABIC = re.compile(r"第\s*(\d{1,6})\s*[章节回]|chapter\s*(\d{1,6})", re.I)
_CHINESE = re.compile(r"第\s*([零〇一二两三四五六七八九十百千]{1,12})\s*[章节回]")
_CHAPTER_PREFIX = re.compile(
    r"^(?:第\s*(?:\d+|[零〇一二两三四五六七八九十百千]+)\s*[章节回]|chapter\s*\d+)"
    r"[\s:：.、\-—]*", re.I
)
_UNSAFE_WORK = re.compile(r"[〈〉《》「」『』【】\[\]\s\u3000]+")
_SUFFIX = re.compile(r"(?:最新章节|全文阅读|免费阅读|小说在线阅读)$")


def normalize_work(value: str) -> str:
    title = _UNSAFE_WORK.sub("", value.strip().casefold())
    return _SUFFIX.sub("", title)


def _han_number(text: str) -> int | None:
    digits = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3,
              "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    units = {"十": 10, "百": 100, "千": 1000}
    result = current = 0
    if not text:
        return None
    for char in text:
        if char in digits:
            current = digits[char]
        elif char in units:
            unit = units[char]
            result += (current or 1) * unit
            current = 0
        else:
            return None
    return result + current


def chapter_key(value: str):
    match = _ARABIC.search(value)
    if match:
        number = int(match.group(1) or match.group(2))
    else:
        match = _CHINESE.search(value)
        number = _han_number(match.group(1)) if match else None
    if number is None:
        return None
    tail = re.sub(r"[\s\u3000:：.、!！?？\-—_\u00b7]+", "",
                  _CHAPTER_PREFIX.sub("", value).strip().casefold())
    # Restrict suffix comparison to meaningful titles. E.g., "第12章" is number-only.
    return number, (tail if len(tail) >= 2 else "")


def match_chapter(wanted: str, candidate: str) -> str | None:
    left, right = chapter_key(wanted), chapter_key(candidate)
    if not left or not right or left[0] != right[0]:
        return None
    if left[1] and right[1]:
        return "exact_chapter" if left[1] == right[1] else None
    return "number_only"


async def find_sources(
    book: dict, sites: list[dict], chapter_title: str,
    *, max_sites: int = 6
) -> dict:
    """Bounded public-site discovery, no permanent mutations and no auto switch."""
    title = book["title"].strip()
    chapter = chapter_key(chapter_title)
    if not chapter:
        return {"status": "needs_chapter_title", "alternatives": [], "warnings": [],
                "note": "无法识别目标章节编号，请确认章节名后重试；不会猜测下一章"}
    search_sites = [s for s in sites if s["id"] != book["site_id"]
                    and s.get("enabled") and s.get("search_url_template")
                    and s.get("kind") in ("novel", "mixed", "other")]
    search_sites = search_sites[:max(1, min(max_sites, 8))]
    sem = asyncio.Semaphore(2)

    async def inspect(site):
        async with sem:
            try:
                hits, diagnostic = await fetch_site_results(site, title)
                exact_books = [hit for hit in hits[:30]
                               if normalize_work(hit["title"]) == normalize_work(title)]
                if len(exact_books) != 1:
                    return None, ("该站没有唯一的同名作品" if not exact_books
                                  else "同名作品有多个，暂不自动选择")
                work = exact_books[0]
                host = urlsplit(site["base_url"]).hostname
                if urlsplit(work["url"]).hostname != host:
                    return None, "作品地址跨站，已跳过"
                html, _ = await fetch_analysis_html(
                    work["url"], host, http_fetch=fetch_html
                )
                toc = extract_reader_page(html, work["url"], site)
                if toc.get("kind") != "toc":
                    return None, "作品页未识别出可靠目录"
                async def get_followup(url, expected_host):
                    data, _ = await fetch_analysis_html(
                        url, expected_host, http_fetch=fetch_html
                    )
                    return data
                toc = await expand_toc_result(toc, site, get_followup, max_pages=2)
                matching = [(entry, match_chapter(chapter_title, entry["title"]))
                            for entry in toc["chapters"]]
                matching = [(entry, match) for entry, match in matching if match]
                if len(matching) != 1:
                    return None, "目录中没有唯一匹配的目标章节"
                entry, level = matching[0]
                chapter_html, _ = await fetch_analysis_html(
                    entry["url"], host, http_fetch=fetch_html
                )
                extracted = extract_reader_page(chapter_html, entry["url"], site)
                if extracted.get("kind") != "chapter":
                    return None, "目标章节无法提取正文"
                extracted_level = match_chapter(chapter_title, extracted["title"])
                if not extracted_level:
                    return None, "章节正文标题与目标章节不一致"
                level = "exact_chapter" if (
                    level == "exact_chapter" and extracted_level == "exact_chapter"
                ) else "number_only"
                return {
                    "site_id": site["id"], "site_name": site["name"],
                    "book_url": work["url"], "book_title": work["title"],
                    "chapter_url": entry["url"], "chapter_title": extracted["title"],
                    "confidence": level,
                    "verified_extract": True,
                    "reason": ("作品名一致，章节编号及标题一致，且已验证可提取正文"
                               if level == "exact_chapter"
                               else "作品名一致，章节编号一致且正文可提取；章节标题证据不足"),
                }, diagnostic
            except Exception as exc:
                return None, f"候选读取失败：{type(exc).__name__} - {str(exc)[:90]}"

    checked = await asyncio.gather(*(inspect(s) for s in search_sites))
    alternatives = []
    warnings = []
    for site, (candidate, detail) in zip(search_sites, checked):
        if candidate:
            alternatives.append(candidate)
        elif detail:
            warnings.append({"site_name": site["name"], "message": str(detail)[:130]})
    alternatives.sort(key=lambda a: a["confidence"] != "exact_chapter")
    return {
        "status": "matches" if alternatives else "no_match",
        "alternatives": alternatives[:8],
        "searched_sites": len(search_sites),
        "warnings": warnings[:8],
        "auto_switched": False,
        "note": "仅来自已配置站点；自动搜索并验证章节，但切换书源需用户确认；不保证正文与原站完全相同",
    }
