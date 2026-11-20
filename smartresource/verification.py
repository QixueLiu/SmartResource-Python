"""Best-effort positive-control probing of the site's own GET search form."""
from urllib.parse import urljoin, urlsplit, quote_plus
from bs4 import BeautifulSoup
from .search import heuristic_search_results
from .security import validate_public_url

_SKIP = {"首页", "目录", "分类", "更多", "登录", "注册", "搜索",
         "排行榜", "热门", "返回", "上一页", "下一页", "home", "more", "login"}


def choose_probe(html: str, site_url: str):
    soup = BeautifulSoup(html, "html.parser")
    host = urlsplit(site_url).hostname
    candidates = []
    for link in soup.select("a[href]")[:3500]:
        title = link.get_text(" ", strip=True)
        target = urljoin(site_url, str(link.get("href") or "").strip())
        parsed = urlsplit(target)
        if (parsed.hostname != host or parsed.scheme not in ("http", "https")
                or not parsed.path.strip("/") or len(title) < 3 or len(title) > 36
                or title.casefold() in _SKIP):
            continue
        if any(x in parsed.path.lower() for x in ("/search", "/category", "/login")):
            continue
        score = (3 if any(x in parsed.path.lower() for x in
                 ("/book/", "/novel/", "/detail/", "/vod/")) else 0)
        score += min(len(title), 18) / 18
        candidates.append((score, title, target))
    return sorted(candidates, reverse=True)[0][1:] if candidates else None


async def verify_search(site: dict, homepage_html: str, fetch=None):
    template = site.get("search_url_template")
    if not template:
        return {"status": "needs_rule", "message": "尚未发现 GET 搜索地址"}
    sample = choose_probe(homepage_html, site["base_url"])
    if sample is None:
        return {"status": "candidate", "message": "首页无合适作品链接，无法自动验证"}
    title, target = sample
    url = template.replace("{q}", quote_plus(title)).replace("{page}", "1")
    host = urlsplit(site["base_url"]).hostname
    try:
        if validate_public_url(url) != host:
            return {"status": "candidate", "message": "搜索地址与原站域名不一致"}
        if fetch is None:
            from .fetcher import fetch_html
            fetch = fetch_html
        html = await fetch(url, host)
        hits = heuristic_search_results(site, html, url, title)
        expected = urlsplit(target).path.rstrip("/")
        if any(urlsplit(hit["url"]).path.rstrip("/") == expected for hit in hits):
            return {"status": "probe_passed",
                    "message": "首页样本书名试搜后找到了相同作品链接；只代表此次样本通过"}
        return {"status": "candidate", "message": "站内搜索有响应，但未找到首页样本对应的作品链接"}
    except Exception as exc:
        return {"status": "candidate",
                "message": "自动试搜失败：" + type(exc).__name__ + " " + str(exc)[:100]}
