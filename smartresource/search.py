import asyncio
import copy
import os
import time
import unicodedata
from urllib.parse import urljoin, quote_plus, urlsplit, urlunsplit
from bs4 import BeautifulSoup
from .fetcher import fetch_html
from .browser_renderer import fetch_analysis_html
from .site_health import observe, classify_error, health_map, rank_sites

# Keep one slow/broken site from delaying every search result.
MAX_SEARCH_SITES = 24
MAX_SEARCH_SECONDS = 8.0
PER_SITE_SECONDS = 6.0
FULL_SEARCH_SITES = 96
FULL_SEARCH_SECONDS = 26.0
FULL_SEARCH_CONCURRENCY = 16
CACHE_TTL_SECONDS = 90.0
CACHE_MAX_ENTRIES = 32

# One user, one Python service: tiny process-local cache, no Redis or new DB.
_search_cache = {}


def _normal_title(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).casefold().split())


def _dedup_key(value: str) -> str:
    parsed = urlsplit(value)
    # Fragments and insignificant trailing slashes do not distinguish works.
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, parsed.query, ""))


def _quality(item: dict, keyword: str) -> int:
    title = _normal_title(item.get("title", ""))
    needle = _normal_title(keyword)
    if title == needle:
        return 0
    if title.startswith(needle):
        return 1
    if needle in title:
        return 2
    return 3


def _cache_key(sites: list[dict], keyword: str, mode: str, kind: str):
    fields = (
        "id", "name", "base_url", "kind", "search_url_template",
        "item_selector", "title_selector", "link_selector", "scan_status"
    )
    # Also isolate test/deployment DBs and mocked fetch implementations.
    fingerprint = tuple(tuple(site.get(key) for key in fields) for site in sites)
    return (os.getenv("SMARTRESOURCE_DB", ""), id(fetch_site_results),
            keyword, mode, kind, fingerprint)


def _get_cached(key):
    entry = _search_cache.get(key)
    if entry is None:
        return None
    expires, value = entry
    if expires <= time.monotonic():
        _search_cache.pop(key, None)
        return None
    cached = copy.deepcopy(value)
    cached["cached"] = True
    cached["elapsed_ms"] = 0
    return cached


def _put_cached(key, value):
    now = time.monotonic()
    for old_key, (expiry, _) in list(_search_cache.items()):
        if expiry <= now:
            _search_cache.pop(old_key, None)
    while len(_search_cache) >= CACHE_MAX_ENTRIES:
        _search_cache.pop(next(iter(_search_cache)))
    _search_cache[key] = (now + CACHE_TTL_SECONDS, copy.deepcopy(value))


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
        verified_css = all(site.get(k) for k in fields)
        if verified_css:
            rows = extract_search_results(site, html, url)
            warning = None
            if not rows:
                # A site may change its CSS without changing its GET search URL.
                # Try a conservative title-link fallback before giving up.
                rows = heuristic_search_results(site, html, url, keyword)
                warning = (
                    "CSS 规则未命中，已回退到未经验证的关键词链接候选"
                    if rows else "无匹配结果，或当前站点 CSS 规则已变化"
                )
        else:
            rows = heuristic_search_results(site, html, url, keyword)
            warning = "使用关键字链接匹配，属于未经验证的候选结果，建议补充 CSS 规则"
        if rows and verified_css and warning is None:
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


async def search_all(
    sites: list[dict], q: str, mode: str = "fast", kind: str = "all",
    refresh: bool = False,
):
    """Bounded personalized search: fast first, optional wider coverage.

    Sites without a saved search template are not searched. Reporting always
    distinguishes configured coverage, skipped sources and timed-out sources.
    """
    if mode not in ("fast", "full") or kind not in ("all", "novel", "video"):
        raise ValueError("不支持的检索模式或类型")

    query = q.strip()
    if not sites:
        return {"items": [], "warnings": [], "searched_sites": 0}

    started = time.monotonic()
    if kind == "all":
        candidates = sites
    else:
        # Mixed/unknown sources are retained because their content type has
        # not been established; they must not be misreported as a verified hit.
        candidates = [
            site for site in sites
            if site.get("kind") in (kind, "mixed", "other")
        ]
    searchable = [s for s in candidates if s.get("search_url_template")]
    unconfigured = len(candidates) - len(searchable)
    key = _cache_key(candidates, query, mode, kind)
    if not refresh:
        cached = _get_cached(key)
        if cached is not None:
            return cached

    metrics = health_map([s["id"] for s in searchable])
    ordered = rank_sites(searchable, metrics=metrics)
    limit = MAX_SEARCH_SITES if mode == "fast" else FULL_SEARCH_SITES
    budget = MAX_SEARCH_SECONDS if mode == "fast" else FULL_SEARCH_SECONDS
    concurrency = 8 if mode == "fast" else FULL_SEARCH_CONCURRENCY
    chosen = ordered[:limit]
    skipped = len(ordered) - len(chosen)

    if not chosen:
        response = {
            "items": [], "warnings": [], "searched_sites": 0,
            "completed_sites": 0, "searchable_sites": 0,
            "unconfigured_sites": unconfigured,
            "skipped_sites": 0, "timed_out_sites": 0,
            "mode": mode, "kind": kind, "cached": False,
            "elapsed_ms": round((time.monotonic() - started) * 1000),
        }
        _put_cached(key, response)
        return response

    semaphore = asyncio.Semaphore(concurrency)

    async def one(site):
        async with semaphore:
            try:
                return await asyncio.wait_for(
                    fetch_site_results(site, query), timeout=PER_SITE_SECONDS
                )
            except asyncio.TimeoutError:
                return [], "网站响应超过单站6秒限制"

    tasks = {asyncio.create_task(one(site)): site for site in chosen}
    done, pending = await asyncio.wait(tasks, timeout=budget)
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)

    by_id = {}
    timeout_count = len(pending)
    for task in done:
        site = tasks[task]
        try:
            record = task.result()
        except Exception:
            record = ([], "站点请求异常")
        by_id[site["id"]] = record
        if record[1] and "超过单站6秒" in record[1]:
            timeout_count += 1

    results, seen, warnings = [], set(), []
    for index, site in enumerate(chosen):
        rows, warning = by_id.get(site["id"], ([], "本次搜索达到时间上限"))
        if warning:
            warnings.append({
                "site_id": site["id"], "site_name": site["name"],
                "message": warning,
            })
        for row in rows:
            key_url = _dedup_key(row["url"])
            if key_url in seen:
                continue
            seen.add(key_url)
            results.append({
                **row, "source_health": metrics.get(site["id"], {}),
                "_source_order": index,
            })

    # Exact titles first, then partial matches. Preserve better source health
    # before lower-ranked sources and put uncertain heuristic hits last.
    results.sort(key=lambda item: (
        _quality(item, query),
        bool(item.get("extraction") == "heuristic"),
        item["_source_order"],
    ))
    for item in results:
        item.pop("_source_order", None)

    response = {
        "items": results, "warnings": warnings,
        "searched_sites": len(chosen),
        "completed_sites": len(done),
        "searchable_sites": len(searchable),
        "unconfigured_sites": unconfigured,
        "skipped_sites": skipped,
        "timed_out_sites": timeout_count,
        "mode": mode, "kind": kind, "cached": False,
        "elapsed_ms": round((time.monotonic() - started) * 1000),
    }
    if skipped:
        response["note"] = (
            f"本次最多选择{limit}个已配置站点；另有{skipped}个可检索站点未尝试"
        )
    _put_cached(key, response)
    return response
