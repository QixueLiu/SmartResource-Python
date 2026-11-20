import asyncio
from urllib.parse import urljoin, quote_plus, urlsplit
from bs4 import BeautifulSoup
from .fetcher import fetch_html


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


async def fetch_site_results(site: dict, keyword: str):
    fields = ("search_url_template", "item_selector", "title_selector", "link_selector")
    if not all(site.get(k) for k in fields):
        return [], "未配置站内搜索规则（仍可原网页浏览）"
    url = site["search_url_template"].replace("{q}", quote_plus(keyword)).replace("{page}", "1")
    try:
        html = await fetch_html(url, urlsplit(site["base_url"]).hostname)
        rows = extract_search_results(site, html, url)
        return rows, None if rows else "网页返回成功，但没有匹配搜索结果"
    except Exception as exc:
        return [], f"检索失败：{type(exc).__name__} - {str(exc)[:120]}"


async def search_all(sites: list[dict], q: str):
    semaphore = asyncio.Semaphore(5)

    async def one(site):
        async with semaphore:
            return await fetch_site_results(site, q)

    outcomes = await asyncio.gather(*(one(site) for site in sites))
    results, seen, warnings = [], set(), []
    for site, (items, warning) in zip(sites, outcomes):
        if warning:
            warnings.append({"site_id": site["id"], "site_name": site["name"], "message": warning})
        for item in items:
            if item["url"] not in seen:
                seen.add(item["url"])
                results.append(item)
    return {"items": results, "warnings": warnings, "searched_sites": len(sites)}
