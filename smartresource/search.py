import asyncio
import copy
import os
import re
import time
import unicodedata
from urllib.parse import urljoin, quote_plus, urlsplit, urlunsplit
from bs4 import BeautifulSoup
from .fetcher import fetch_html
from .browser_renderer import fetch_analysis_html
from .site_health import observe, classify_error, health_map, rank_sites
from .search_pagination import discover_next_search_page

# Keep one slow/broken site from delaying every search result.
MAX_SEARCH_SITES = 24
MAX_SEARCH_SECONDS = 8.0
PER_SITE_SECONDS = 6.0
FULL_SEARCH_SITES = 96
FULL_SEARCH_SECONDS = 26.0
FULL_SEARCH_CONCURRENCY = 16
FULL_SEARCH_PAGES = 3
FULL_SITE_MAX_RESULTS = 100
CACHE_TTL_SECONDS = 90.0
CACHE_MAX_ENTRIES = 32
SITE_CACHE_MAX_ENTRIES = 160

# One user, one Python service: process-local only, no Redis or new DB.
_search_cache = {}
_site_cache = {}
_next_link_cache = {}


def _normal_title(value: str) -> str:
    """Ignore spaces and punctuation, but retain letters, digits and CJK."""
    return "".join(
        char for char in unicodedata.normalize("NFKC", value).casefold()
        if char.isalnum()
    )


_GENERIC_TITLE_SUFFIX = (
    "最新", "全文", "全本", "免费", "在线", "目录", "章节", "阅读",
    "小说", "全集", "完整", "高清", "播放", "txt下载", "完结",
)


def _core_title_match(title: str, keyword: str) -> bool:
    """Recognize 《书名》免费阅读 without treating 《书名》第二部 as exact."""
    match = re.match(r"^\s*[《「『](.{1,120})[》」』](.*)$", title)
    if not match or _normal_title(match.group(1)) != keyword:
        return False
    trailing = _normal_title(match.group(2))
    return not trailing or trailing.startswith(_GENERIC_TITLE_SUFFIX)


def _match_grade(title: str, keyword: str, context: str = "") -> str:
    needle = _normal_title(keyword)
    candidate = _normal_title(title)
    if not needle or not candidate:
        return "unrelated"
    if candidate == needle or _core_title_match(title, needle):
        return "exact"
    if candidate.startswith(needle) or needle in candidate:
        return "title"
    if len(needle) >= 2 and needle in _normal_title(context):
        return "context"
    return "unrelated"


def _dedup_key(value: str) -> str:
    parsed = urlsplit(value)
    # Fragments and insignificant trailing slashes do not distinguish works.
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, parsed.query, ""))


def _quality(item: dict, keyword: str) -> int:
    grade = item.get("match_grade") or _match_grade(item.get("title", ""), keyword)
    return {"exact": 0, "title": 1, "context": 2, "unrelated": 3}.get(grade, 3)


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


def _next_key(site: dict, keyword: str, current_url: str | None = None):
    # The source fetch function is part of the key to prevent cross-test
    # cache collisions while preserving regular quick-to-full reuse.
    return (_site_cache_key(site, keyword), current_url or "first")


def _save_next_link(site: dict, keyword: str, url: str | None, current_url=None):
    key = _next_key(site, keyword, current_url)
    _next_link_cache[key] = (time.monotonic() + CACHE_TTL_SECONDS, url)
    if len(_next_link_cache) > SITE_CACHE_MAX_ENTRIES * 3:
        now = time.monotonic()
        for old_key, (until, _) in list(_next_link_cache.items()):
            if until <= now:
                _next_link_cache.pop(old_key, None)
        while len(_next_link_cache) > SITE_CACHE_MAX_ENTRIES * 3:
            _next_link_cache.pop(next(iter(_next_link_cache)))


def _load_next_link(site: dict, keyword: str, current_url=None):
    key = _next_key(site, keyword, current_url)
    cached = _next_link_cache.get(key)
    if not cached:
        return None
    until, value = cached
    if until <= time.monotonic():
        _next_link_cache.pop(key, None)
        return None
    return value


def _put_cached(key, value):
    now = time.monotonic()
    for old_key, (expiry, _) in list(_search_cache.items()):
        if expiry <= now:
            _search_cache.pop(old_key, None)
    while len(_search_cache) >= CACHE_MAX_ENTRIES:
        _search_cache.pop(next(iter(_search_cache)))
    _search_cache[key] = (now + CACHE_TTL_SECONDS, copy.deepcopy(value))


def extract_search_results(
    site: dict, html: str, search_url: str, keyword: str | None = None
):
    """Parse site CSS, ignoring unrelated recommendations for actual searches.

    Description/author text within the *same result row* may retain a lower
    confidence match even when the work title itself differs.
    """
    soup = BeautifulSoup(html, "html.parser")
    results = []
    seen = set()
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
        title = title_el.get_text(" ", strip=True)[:200]
        if not title:
            continue
        grade = (
            _match_grade(title, keyword, row.get_text(" ", strip=True)[:600])
            if keyword else ""
        )
        if grade == "unrelated":
            continue
        dedup = _dedup_key(target)
        if dedup in seen:
            continue
        seen.add(dedup)
        result = {
            "title": title,
            "url": target, "site_id": site["id"],
            "site_name": site["name"], "kind": site["kind"]
        }
        if grade:
            result["match_grade"] = grade
            result["extraction"] = "css"
        results.append(result)
    return results


def heuristic_search_results(site: dict, html: str, search_url: str, keyword: str):
    """Fallback for GET search forms without maintained selectors.

    Candidates are deliberately limited to links containing the full search
    string. This can miss books; do not call them complete or validated.
    """
    soup = BeautifulSoup(html, "html.parser")
    host = urlsplit(site["base_url"]).hostname
    needle = _normal_title(keyword)
    seen, result = set(), []
    for a in soup.select("a[href]")[:8000]:
        title = a.get_text(" ", strip=True)[:200]
        if not title or len(title) < 2 or len(title) > 160:
            continue
        if needle not in _normal_title(title):
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
            "extraction": "heuristic",
            "match_grade": _match_grade(title, keyword)
        })
        if len(result) == 40:
            break
    return result


async def fetch_site_results(site: dict, keyword: str):
    if not site.get("search_url_template"):
        return [], "未发现已保存的站内搜索地址（仍可原网页浏览）"
    url = site["search_url_template"].replace("{q}", quote_plus(keyword)).replace("{page}", "1")
    try:
        host = urlsplit(site["base_url"]).hostname
        html, _ = await fetch_analysis_html(url, host, http_fetch=fetch_html)
        if "{page}" not in (site.get("search_url_template") or ""):
            _save_next_link(
                site, keyword, discover_next_search_page(html, url, host)
            )
        fields = ("item_selector", "title_selector", "link_selector")
        verified_css = all(site.get(k) for k in fields)
        if verified_css:
            rows = extract_search_results(site, html, url, keyword)
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
        if rows and verified_css and warning is None and any(
            row.get("match_grade") in ("exact", "title") for row in rows
        ):
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


async def fetch_additional_search_page(
    site: dict, keyword: str, page: int
) -> tuple[list[dict], str | None]:
    """Fetch a later result page only for an explicit, same-origin {page} rule.

    Normal quick search never calls this helper. Later pages do not create
    extra health success/failure samples for the same user search.
    """
    template = site.get("search_url_template") or ""
    if "{q}" not in template or "{page}" not in template or page < 2:
        return [], "该来源未提供可用的搜索分页规则"

    url = template.replace("{q}", quote_plus(keyword)).replace("{page}", str(page))
    host = urlsplit(site["base_url"]).hostname
    # fetch_analysis_html / fetch_html validates that the destination is
    # public and within the saved site's origin before making any request.
    html, _ = await fetch_analysis_html(url, host, http_fetch=fetch_html)
    fields = ("item_selector", "title_selector", "link_selector")
    if all(site.get(field) for field in fields):
        items = extract_search_results(site, html, url, keyword)
        if items:
            return items, None
        fallback = heuristic_search_results(site, html, url, keyword)
        return fallback, (
            "后续页 CSS 未命中，使用未经验证的关键词候选" if fallback else None
        )
    return (
        heuristic_search_results(site, html, url, keyword),
        "后续页使用未经验证的关键词候选",
    )


async def fetch_discovered_search_page(
    site: dict, keyword: str, next_url: str
) -> tuple[list[dict], str | None, str | None]:
    """Fetch only a previously observed validated next link, never guesses."""
    host = urlsplit(site["base_url"]).hostname
    from .security import validate_public_url

    if validate_public_url(next_url) != host:
        raise ValueError("发现的分页地址不是原站安全网址")
    html, _ = await fetch_analysis_html(next_url, host, http_fetch=fetch_html)
    following = discover_next_search_page(html, next_url, host)
    fields = ("item_selector", "title_selector", "link_selector")
    if all(site.get(field) for field in fields):
        items = extract_search_results(site, html, next_url, keyword)
        if items:
            return items, None, following
        fallback = heuristic_search_results(site, html, next_url, keyword)
        return fallback, (
            "自动识别分页的 CSS 未命中，使用未经验证候选" if fallback else None
        ), following
    return (
        heuristic_search_results(site, html, next_url, keyword),
        "自动识别分页使用未经验证候选",
        following,
    )


def _merge_site_pages(
    initial: list[dict], additional: list[dict], limit: int = FULL_SITE_MAX_RESULTS
) -> list[dict]:
    """Keep the first result for a URL, in source-page order."""
    seen = set()
    combined = []
    for item in (*initial, *additional):
        identifier = _dedup_key(item["url"])
        if identifier in seen:
            continue
        seen.add(identifier)
        combined.append(item)
        if len(combined) >= limit:
            break
    return combined


def _site_cache_key(site: dict, keyword: str):
    fields = (
        "id", "name", "base_url", "kind", "search_url_template",
        "item_selector", "title_selector", "link_selector", "scan_status"
    )
    return (os.getenv("SMARTRESOURCE_DB", ""), id(fetch_site_results), keyword,
            tuple(site.get(field) for field in fields))


def _read_site_cache(key):
    value = _site_cache.get(key)
    if value is None:
        return None
    expires, payload = value
    if expires <= time.monotonic():
        _site_cache.pop(key, None)
        return None
    return copy.deepcopy(payload)


def _write_site_cache(key, payload):
    # Do not make transient network outages or timeouts sticky for 90 seconds.
    warning = payload[1]
    if warning and (
        warning.startswith("检索失败：") or "超过单站" in warning
    ):
        return
    now = time.monotonic()
    for old_key, (expires, _) in list(_site_cache.items()):
        if expires <= now:
            _site_cache.pop(old_key, None)
    while len(_site_cache) >= SITE_CACHE_MAX_ENTRIES:
        _site_cache.pop(next(iter(_site_cache)))
    _site_cache[key] = (now + CACHE_TTL_SECONDS, copy.deepcopy(payload))


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
    # Check both explicit templates and links cached from prior quick
    # searches. A fresh first-page fetch can discover more links below.

    if not chosen:
        response = {
            "items": [], "warnings": [], "searched_sites": 0,
            "completed_sites": 0, "searchable_sites": 0,
            "unconfigured_sites": unconfigured,
            "skipped_sites": 0, "timed_out_sites": 0,
            "reused_sites": 0,
            "extra_pages_fetched": 0,
            "paginated_sites": 0,
            "discovered_pagination_sites": 0,
            "mode": mode, "kind": kind, "cached": False,
            "elapsed_ms": round((time.monotonic() - started) * 1000),
        }
        _put_cached(key, response)
        return response

    semaphore = asyncio.Semaphore(concurrency)

    async def one(site):
        first_key = _site_cache_key(site, query)
        template = site.get("search_url_template") or ""
        explicit_pages = "{q}" in template and "{page}" in template
        discovered = _load_next_link(site, query) if not refresh else None
        supports_pages = explicit_pages or bool(discovered)
        full_key = first_key + ("full_pages",)
        if mode == "full" and supports_pages and not refresh:
            full_cached = _read_site_cache(full_key)
            if full_cached is not None:
                return (*full_cached, True, 0)

        first_cached = _read_site_cache(first_key) if not refresh else None
        if (mode != "full" or not supports_pages) and first_cached is not None:
            return (*first_cached, True, 0)

        async with semaphore:
            started_site = time.monotonic()
            reused = first_cached is not None
            if reused:
                first_rows, first_warning = first_cached
            else:
                try:
                    first_rows, first_warning = await asyncio.wait_for(
                        fetch_site_results(site, query), timeout=PER_SITE_SECONDS
                    )
                except asyncio.TimeoutError:
                    return [], "网站响应超过单站6秒限制", False, 0
                _write_site_cache(first_key, (first_rows, first_warning))

            discovered = _load_next_link(site, query)
            if mode != "full" or not (explicit_pages or discovered) or (
                first_warning or ""
            ).startswith("检索失败："):
                return first_rows, first_warning, reused, 0

            results = _merge_site_pages(first_rows, [])
            next_url = discovered
            seen_page_urls = set()
            warning = first_warning
            extra_pages = 0
            complete = True
            for page in range(2, FULL_SEARCH_PAGES + 1):
                if len(results) >= FULL_SITE_MAX_RESULTS:
                    break
                remaining = PER_SITE_SECONDS - (time.monotonic() - started_site)
                if remaining <= 0:
                    warning = (warning + "；" if warning else "") + "后续分页达到单站时间上限"
                    complete = False
                    break
                if not explicit_pages:
                    if not next_url or next_url in seen_page_urls:
                        break
                    seen_page_urls.add(next_url)
                try:
                    if explicit_pages:
                        rows, page_warning = await asyncio.wait_for(
                            fetch_additional_search_page(site, query, page),
                            timeout=remaining
                        )
                    else:
                        rows, page_warning, next_url = await asyncio.wait_for(
                            fetch_discovered_search_page(site, query, next_url),
                            timeout=remaining
                        )
                    extra_pages += 1
                except asyncio.TimeoutError:
                    warning = (warning + "；" if warning else "") + "后续分页读取超时"
                    complete = False
                    break
                except Exception:
                    warning = (warning + "；" if warning else "") + "后续分页读取失败"
                    complete = False
                    break

                if page_warning and rows:
                    warning = (warning + "；" if warning else "") + page_warning
                if not rows:
                    break
                merged = _merge_site_pages(results, rows)
                if len(merged) == len(results):
                    # A site may serve page one for all requested numbers.
                    break
                results = merged

            if complete:
                _write_site_cache(full_key, (results, warning))
            return results, warning, reused, extra_pages

    tasks = {asyncio.create_task(one(site)): site for site in chosen}
    done, pending = await asyncio.wait(tasks, timeout=budget)
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)

    by_id = {}
    timeout_count = len(pending)
    per_site_timeouts = 0
    reused_count = 0
    extra_pages_fetched = 0
    for task in done:
        site = tasks[task]
        try:
            record = task.result()
        except Exception:
            record = ([], "站点请求异常", False, 0)
        by_id[site["id"]] = record[:2]
        if record[2]:
            reused_count += 1
        extra_pages_fetched += record[3]
        if record[1] and "超过单站6秒" in record[1]:
            timeout_count += 1
            per_site_timeouts += 1

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

    paginated_sites = sum(
        ("{q}" in (site.get("search_url_template") or "") and
         "{page}" in (site.get("search_url_template") or ""))
        or bool(_load_next_link(site, query))
        for site in chosen
    )
    discovered_pagination_sites = sum(
        "{page}" not in (site.get("search_url_template") or "")
        and bool(_load_next_link(site, query))
        for site in chosen
    )
    response = {
        "items": results, "warnings": warnings,
        "searched_sites": len(chosen),
        "completed_sites": len(done) - per_site_timeouts,
        "searchable_sites": len(searchable),
        "unconfigured_sites": unconfigured,
        "skipped_sites": skipped,
        "timed_out_sites": timeout_count,
        "reused_sites": reused_count,
        "extra_pages_fetched": extra_pages_fetched,
        "paginated_sites": paginated_sites,
        "discovered_pagination_sites": discovered_pagination_sites,
        "mode": mode, "kind": kind, "cached": False,
        "elapsed_ms": round((time.monotonic() - started) * 1000),
    }
    if skipped:
        response["note"] = (
            f"本次最多选择{limit}个已配置站点；另有{skipped}个可检索站点未尝试"
        )
    _put_cached(key, response)
    return response
