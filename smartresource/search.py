import asyncio
from urllib.parse import urljoin, quote_plus, urlsplit
from bs4 import BeautifulSoup
from .fetcher import fetch_html
from .browser_renderer import fetch_analysis_html
from .site_health import observe, classify_error, health_map, rank_sites


def extract_search_results(site: dict, html: str, search_url: str):
    soup = BeautifulSoup(html, "html.parser")
    results = []
    host = urlsplit(site["base_url"]).hostname
    for row in soup.select(site["item_selector"])[:60]:
        title_el = row.select_one(site["title_selector"])
        link_el = row.select_one(site["link_selector"])
        if title_el is None or link_el is None:
            continue
        target = urljoin(search_url, (link_el.get("href") or "").strip())
        parsed = urlsplit(target)
        if parsed.scheme not in ("http", "https") or parsed.hostname != host:
            continue
        title = title_el.get_text(" ", strip=True)
        if title:
            results.append({
                "title": title[:200],
                "url": target, "site_id": site["id"],
                "site_name": site["name"], "kind": site["kind"]
            })
    return results


def heuristic_search_results(site: dict, html: str, search_url: str, keyword: str):
    """Fallback for GET search forms without maintained selectors.

    Candidates are deliberately limited to links containing the full search
    string. This can miss books; do not call them complete or validated.
    """
    soup = BeautifulSoup(html, "html.parser")
    host = urlsplit(site["base_url"]).hostname
    needle = "".join(keyword.casefold().split())
    seen, result = set(), []
    for a in soup.select("a[href]")[:8000]:
        title = a.get_text(" ", strip=True)[:200]
        if not title or len(title) < 2 or len(title) > 160:
            continue
        if needle not in "".join(title.casefold().split()):
            continue
        target = urljoin(search_url, (a.get("href") or "").strip())
        parsed = urlsplit(target)
        if parsed.scheme not in ("http", "https") or parsed.hostname != host:
            continue
        if target == search_url or target in seen:
            continue
        seen.add(target)
        result.append({
            "title": title, "url": target, "site_id": site["id"],
            "site_name": site["name"], "kind": site["kind"],
            "extraction": "heuristic"
        })
        if len(result) == 40:
            break
    return result


async def fetch_site_results(site: dict, keyword: str):
    if not site.get("search_url_template"):
        return [], "未发现已保存的站内搜索地址（仍可原网页浏览）"
    url = site["search_url_template"].replace("{q}", quote_plus(keyword)).replace("{page}", "1")
    try:
        html, _ = await fetch_analysis_html(url, urlsplit(site["base_url"]).hostname, http_fetch=fetch_html)
        fields = ("item_selector", "title_selector", "link_selector")
        if all(site.get(k) for k in fields):
            rows = extract_search_results(site, html, url)
            warning = None if rows else "搜索返回成功，但 CSS 规则未提取到结果"
        else:
            rows = heuristic_search_results(site, html, url, keyword)
            warning = "使用关键字链接匹配，属于未经验证的候选结果，建议补充 CSS 规则"
        if rows and all(site.get(k) for k in fields):
            observe(site["id"], "search", "success", "ok")
        elif rows:
            observe(site["id"], "search", "neutral", "heuristic_results")
        else:
            # Empty results may be a valid response to the user's query.
            observe(site["id"], "search", "neutral", "empty_results")
        return rows, warning
    except Exception as exc:
        reason = classify_error(exc)
        observe(site["id"], "search", "failure", reason)
        return [], f"检索失败：{reason}（请检查站点或稍后重试）"


async def search_all(sites: list[dict], q: str):
    semaphore = asyncio.Semaphore(5)

    async def one(site):
        async with semaphore:
            return await fetch_site_results(site, q)

    # Prefer sites with actual successes; never suppress a poorly scoring site.
    ordered = rank_sites(sites)
    outcomes = await asyncio.gather(*(one(site) for site in ordered))
    health = health_map([site["id"] for site in ordered])
    results, seen, warnings = [], set(), []
    for site, (items, warning) in zip(ordered, outcomes):
        if warning:
            warnings.append({"site_id": site["id"], "site_name": site["name"], "message": warning})
        for item in items:
            if item["url"] not in seen:
                seen.add(item["url"])
                results.append({**item, "source_health": health[site["id"]]})
    return {"items": results, "warnings": warnings, "searched_sites": len(sites)}
