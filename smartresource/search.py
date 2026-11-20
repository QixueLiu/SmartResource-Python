import asyncio
from urllib.parse import urljoin, quote_plus, urlsplit
from bs4 import BeautifulSoup
from .fetcher import fetch_html
from .browser_renderer import fetch_analysis_html
from .site_health import observe, classify_error, health_map, rank_sites

# Keep one slow/broken site from delaying every search result.
MAX_SEARCH_SITES = 24
MAX_SEARCH_SECONDS = 8.0
PER_SITE_SECONDS = 6.0


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
    # Search top-ranked sources first. Skipped sites are explicitly reported;
    # all remain configured and may be searched in a later request.
    ordered = rank_sites(sites)
    chosen = ordered[:MAX_SEARCH_SITES]
    skipped = len(ordered) - len(chosen)
    if not chosen:
        return {"items": [], "warnings": [], "searched_sites": 0}

    semaphore = asyncio.Semaphore(8)

    async def one(site):
        async with semaphore:
            try:
                return await asyncio.wait_for(
                    fetch_site_results(site, q), timeout=PER_SITE_SECONDS
                )
            except asyncio.TimeoutError:
                return [], "网站响应超过单站6秒限制"

    tasks = {asyncio.create_task(one(site)): site for site in chosen}
    done, pending = await asyncio.wait(tasks, timeout=MAX_SEARCH_SECONDS)
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    by_id = {}
    for task in done:
        site = tasks[task]
        try:
            by_id[site["id"]] = task.result()
        except Exception:
            by_id[site["id"]] = ([], "站点请求异常")
    health = health_map([site["id"] for site in chosen])
    results, seen, warnings = [], set(), []
    for site in chosen:
        items, warning = by_id.get(site["id"], ([], "整次搜索达到8秒时间上限"))
        if warning:
            warnings.append({"site_id": site["id"], "site_name": site["name"], "message": warning})
        for item in items:
            if item["url"] not in seen:
                seen.add(item["url"])
                results.append({**item, "source_health": health[site["id"]]})
    result = {"items": results, "warnings": warnings, "searched_sites": len(chosen)}
    if skipped:
        result["skipped_sites"] = skipped
        result["note"] = "为保证响应速度，仅检索了优先级较高的24个站点；其余站点未检索"
    return result
