import asyncio
from urllib.parse import urljoin, quote_plus, urlsplit
import httpx
from bs4 import BeautifulSoup
from .security import assert_public_url

async def fetch_site_results(site: dict, keyword: str):
    if not all(site.get(k) for k in ("search_url_template", "item_selector", "title_selector", "link_selector")):
        return [], "未配置完整搜索规则"
    url = site["search_url_template"].replace("{q}", quote_plus(keyword)).replace("{page}", "1")
    try:
        host = assert_public_url(url)
        if host != urlsplit(site["base_url"]).hostname.lower():
            return [], "请求越过原站域名"
        async with httpx.AsyncClient(follow_redirects=False, timeout=12, headers={"User-Agent": "SmartResource/0.1 (+private site search)"}) as client:
            response = await client.get(url)
        if response.is_redirect:
            return [], f"搜索接口重定向（HTTP {response.status_code}），需核查"
        response.raise_for_status()
        if "text/html" not in response.headers.get("content-type", ""):
            return [], "搜索响应不是 HTML，需专属 API 解析规则"
        soup = BeautifulSoup(response.text[:3_000_000], "html.parser")
        rows = soup.select(site["item_selector"])[:40]
        results = []
        for row in rows:
            title_el = row.select_one(site["title_selector"])
            link_el = row.select_one(site["link_selector"])
            if not title_el or not link_el:
                continue
            href = (link_el.get("href") or "").strip()
            target = urljoin(url, href)
            if urlsplit(target).scheme not in ("https", "http"):
                continue
            # Do not automatically fetch links; they are opened manually in the App.
            title = title_el.get_text(" ", strip=True)
            if title:
                results.append({"title": title[:200], "url": target, "site_id": site["id"], "site_name": site["name"], "kind": site["kind"]})
        return results, None if results else "页面返回成功，但选择器未识别到作品"
    except Exception as exc:
        return [], f"访问或解析失败: {type(exc).__name__}: {str(exc)[:100]}"

async def search_all(sites: list[dict], q: str):
    limiter = asyncio.Semaphore(5)
    async def one(site):
        async with limiter:
            return await fetch_site_results(site, q)
    outcomes = await asyncio.gather(*(one(s) for s in sites))
    result, seen, warnings = [], set(), []
    for site, (items, issue) in zip(sites, outcomes):
        if issue:
            warnings.append({"site_id": site["id"], "site_name": site["name"], "message": issue})
        for item in items:
            key = item["url"]
            if key not in seen:
                seen.add(key)
                result.append(item)
    return {"items": result, "warnings": warnings, "searched_sites": len(sites)}
