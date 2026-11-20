"""Best-effort *single-page* TOC analysis; not a proof that a book is complete."""
import asyncio
import re
from urllib.parse import urljoin, urlsplit
from bs4 import BeautifulSoup
from .fetcher import fetch_html
from .browser_renderer import fetch_analysis_html
from .analyzer import detect_toc

CHAPTER_NUMBER = re.compile(r"第\s*(\d{1,6})\s*[章节回]")


def analyze_toc(site: dict, html: str, url: str):
    if not site.get("toc_item_selector") or not site.get("toc_link_selector"):
        raise ValueError("此站点未配置目录条目/章节链接 CSS 规则")
    soup = BeautifulSoup(html, "html.parser")
    entries = []
    seen_urls, seen_names = set(), set()
    duplicates = 0
    host = urlsplit(site["base_url"]).hostname
    for el in soup.select(site["toc_item_selector"])[:6000]:
        a = el.select_one(site["toc_link_selector"])
        t = el.select_one(site["toc_title_selector"]) if site.get("toc_title_selector") else a
        if a is None or t is None:
            continue
        href = (a.get("href") or "").strip()
        target = urljoin(url, href)
        parsed = urlsplit(target)
        if parsed.scheme not in ("http", "https") or parsed.hostname != host:
            continue
        title = t.get_text(" ", strip=True)[:200]
        if not title:
            continue
        norm = re.sub(r"\s+", "", title).casefold()
        if target in seen_urls or norm in seen_names:
            duplicates += 1
            continue
        seen_urls.add(target)
        seen_names.add(norm)
        match = CHAPTER_NUMBER.search(title)
        entries.append({"title": title, "url": target, "number": int(match.group(1)) if match else None})
    numbers = {e["number"] for e in entries if e["number"] is not None}
    missing = []
    if numbers:
        low, high = min(numbers), max(numbers)
        if high - low <= 10000:
            missing = sorted(set(range(low, high + 1)) - numbers)
    return {
        "site_id": site["id"], "site_name": site["name"], "url": url,
        "chapter_count": len(entries), "duplicate_entries": duplicates,
        "numbered_chapter_count": len(numbers),
        "inferred_missing_count": len(missing),
        "missing_number_examples": missing[:30],
        "latest_chapter": entries[-1]["title"] if entries else None,
        "confidence": "medium" if len(numbers) >= max(5, len(entries) * 0.7) else "low",
        "scope": "仅本页目录；不包含分页或正文校验，不保证全书完整",
    }


async def compare(sites_by_id: dict, candidates):
    limiter = asyncio.Semaphore(3)

    async def one(candidate):
        site = sites_by_id.get(candidate.site_id)
        if site is None:
            return {"site_id": candidate.site_id, "url": candidate.url, "error": "未找到已启用的站点"}
        async with limiter:
            try:
                host = urlsplit(site["base_url"]).hostname
                if urlsplit(candidate.url).hostname != host:
                    raise ValueError("目录网址和站点域名不匹配")
                html, _ = await fetch_analysis_html(candidate.url, host, http_fetch=fetch_html)
                auto_discovered = False
                working_site = site
                if not site.get("toc_item_selector") or not site.get("toc_link_selector"):
                    suggestion = detect_toc(BeautifulSoup(html, "html.parser"), candidate.url, host)
                    if not suggestion:
                        raise ValueError("该页面暂未识别出有效章节目录，仍可使用原网页")
                    working_site = {**site,
                                    "toc_item_selector": suggestion["toc_item_selector"],
                                    "toc_title_selector": suggestion["toc_title_selector"],
                                    "toc_link_selector": suggestion["toc_link_selector"]}
                    auto_discovered = True
                result = analyze_toc(working_site, html, candidate.url)
                result["auto_discovered"] = auto_discovered
                if auto_discovered:
                    result["confidence"] = "low" if result["confidence"] == "low" else "medium"
                    result["scope"] += "；目录 CSS 来自本次自动发现，没有写入配置"
                return result
            except Exception as exc:
                return {"site_id": candidate.site_id, "site_name": site["name"], "url": candidate.url, "error": str(exc)[:180]}

    rows = await asyncio.gather(*(one(c) for c in candidates))
    successful = [row for row in rows if "error" not in row]
    max_count = max((row["chapter_count"] for row in successful), default=0)
    for row in successful:
        row["relative_chapter_count"] = row["chapter_count"] / max_count if max_count else 0.0
    return {"items": rows, "note": "章节数量为相对参考；还需检查目录分页、缺章及正文有效性"}
