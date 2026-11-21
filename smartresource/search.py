import asyncio
import copy
import json
import os
import re
import time
import unicodedata
from urllib.parse import urljoin, quote_plus, unquote_plus, urlsplit, urlunsplit
from bs4 import BeautifulSoup
from .fetcher import fetch_html, fetch_text
from .browser_renderer import fetch_analysis_html
from .site_health import observe, classify_error, health_map, rank_sites
from .search_pagination import discover_next_search_page
from .search_grouping import annotate_same_title_candidates
from .search_quality import video_core_matches_query

# Keep one slow/broken site from delaying every search result.
MAX_SEARCH_SITES = 24
MAX_SEARCH_SECONDS = 8.0
PER_SITE_SECONDS = 6.0
FULL_SEARCH_SITES = 96
FULL_SEARCH_BATCH_SITES = 48
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


# These are narrowly known marketing parameters, not media authentication,
# episode selectors, paging or arbitrary site-specific query fields.
_TRACKING_QUERY_KEYS = frozenset({
    "fbclid", "gclid", "dclid", "msclkid", "yclid", "igshid",
})


def _dedup_key(value: str) -> str:
    """Canonicalize only obvious duplicate page URLs, never the actual link.

    Preserve query ordering, duplicate keys, encoding, signed tokens and
    episode IDs. Do not fetch a URL or assume two same-name works are equal.
    """
    parsed = urlsplit(value)
    path = parsed.path.rstrip("/") or "/"
    kept = []
    for part in parsed.query.split("&"):
        if not part:
            continue
        raw_name = part.partition("=")[0]
        name = unquote_plus(raw_name).casefold()
        if name.startswith("utm_") or name in _TRACKING_QUERY_KEYS:
            continue
        kept.append(part)
    return urlunsplit((
        parsed.scheme.lower(), parsed.netloc.lower(), path, "&".join(kept), ""
    ))


def _duplicate_preference(item: dict, keyword: str) -> tuple[int, bool]:
    """Prefer stronger title evidence, then configured CSS over heuristics."""
    return (_quality(item, keyword), item.get("extraction") == "heuristic")


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


async def fetch_nasa_video_results(site: dict, keyword: str):
    api = "https://images-api.nasa.gov/search"
    url = f"{api}?q={quote_plus(keyword)}&media_type=video&page_size=40"
    payload = await fetch_text(url, "images-api.nasa.gov", ("application/json",))
    results = []
    for item in json.loads(payload)["collection"]["items"][:40]:
        for record in item.get("data", [])[:1]:
            nasa_id = record.get("nasa_id", "")
            title = record.get("title", "").strip()[:200]
            if not title or record.get("media_type") != "video" or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", nasa_id) or nasa_id in (".", ".."):
                continue
            grade = _match_grade(title, keyword)
            if grade != "unrelated":
                results.append({
                    "title": title, "url": f"https://images.nasa.gov/details/{nasa_id}",
                    "site_id": site["id"], "site_name": site["name"], "kind": site["kind"],
                    "extraction": "json", "match_grade": grade,
                })
    return results


async def fetch_site_results(site: dict, keyword: str):
    if not site.get("search_url_template"):
        return [], "未发现已保存的站内搜索地址（仍可原网页浏览）"
    url = site["search_url_template"].replace("{q}", quote_plus(keyword)).replace("{page}", "1")
    try:
        host = urlsplit(site["base_url"]).hostname
        if host == "images.nasa.gov" and site["kind"] == "video" and urlsplit(url).path == "/search":
            rows = await fetch_nasa_video_results(site, keyword)
            observe(site["id"], "search", "success" if rows else "neutral", "ok" if rows else "empty_results")
            return rows, None
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
        detail = {
            "timeout": "请求超时",
            "upstream_error": "上游站点暂不可用",
            "rate_limited": "站点限制请求频率",
            "access_restricted": "站点限制访问",
            "network_error": "无法连接站点或站点返回请求错误",
            "parse_failure": "站点响应格式无法解析",
        }.get(reason, "站点请求异常")
        return [], f"检索失败：{detail}（{reason}）"


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
    refresh: bool = False, after_site_id: int | None = None,
):
    """Bounded personalized search: fast first, optional wider coverage.

    Sites without a saved search template are not searched. Reporting always
    distinguishes configured coverage, skipped sources and timed-out sources.
    """
    if mode not in ("fast", "full") or kind not in ("all", "novel", "video"):
        raise ValueError("不支持的检索模式或类型")

    query = q.strip()
    if not sites and after_site_id is None:
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
    key = _cache_key(candidates, query, mode, kind) + (after_site_id,)
    if not refresh:
        cached = _get_cached(key)
        if cached is not None:
            return cached

    metrics = health_map([s["id"] for s in searchable])
    ordered = rank_sites(searchable, metrics=metrics)
    if kind in ("novel", "video"):
        # In the 24-source fast-search budget, favor sites specifically
        # configured for the selected content type. Mixed/unknown sites are
        # still searched when capacity remains; full search retains them all.
        # Python's stable sort preserves existing health ordering per tier.
        ordered.sort(key=lambda site: site.get("kind") != kind)
    limit = MAX_SEARCH_SITES if mode == "fast" else FULL_SEARCH_SITES
    budget = MAX_SEARCH_SECONDS if mode == "fast" else FULL_SEARCH_SECONDS
    concurrency = 8 if mode == "fast" else FULL_SEARCH_CONCURRENCY
    if after_site_id is None:
        chosen = ordered[:limit]
    else:
        # Site health can change after each batch, so it cannot order a cursor.
        limit = min(limit, FULL_SEARCH_BATCH_SITES)
        ordered = sorted((site for site in searchable if site["id"] > after_site_id),
                         key=lambda site: site["id"])
        chosen = ordered[:limit]
    skipped = len(ordered) - len(chosen)
    cursor = {"next_site_id": chosen[-1]["id"] if skipped else None} if after_site_id is not None else {}
    # Check both explicit templates and links cached from prior quick
    # searches. A fresh first-page fetch can discover more links below.

    if not chosen:
        response = {
            "items": [], "warnings": [], "searched_sites": 0,
            "collapsed_duplicate_urls": 0,
            "completed_sites": 0, "searchable_sites": len(searchable),
            "unconfigured_sites": unconfigured,
            "skipped_sites": 0, "timed_out_sites": 0,
            "reused_sites": 0,
            "extra_pages_fetched": 0,
            "paginated_sites": 0,
            "discovered_pagination_sites": 0,
            "mode": mode, "kind": kind, "cached": False,
            "elapsed_ms": round((time.monotonic() - started) * 1000), **cursor,
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

    results, result_indexes, warnings = [], {}, []
    collapsed_duplicate_urls = 0
    for index, site in enumerate(chosen):
        rows, warning = by_id.get(site["id"], ([], "本次搜索达到时间上限"))
        if warning:
            warnings.append({
                "site_id": site["id"], "site_name": site["name"],
                "message": warning,
            })
        for row in rows:
            key_url = _dedup_key(row["url"])
            candidate = {
                **row, "source_health": metrics.get(site["id"], {}),
                "_source_order": index,
            }
            existing_index = result_indexes.get(key_url)
            if existing_index is not None:
                collapsed_duplicate_urls += 1
                existing = results[existing_index]
                if _duplicate_preference(candidate, query) < _duplicate_preference(
                    existing, query
                ):
                    # One logical page, better textual evidence. Preserve its
                    # actual original URL and source; never invent a URL.
                    results[existing_index] = candidate
                continue
            result_indexes[key_url] = len(results)
            results.append(candidate)

    # Metadata evidence improves ranking without asserting that two websites
    # host the same work, edition or playable media.
    for item in results:
        if item.get("kind") == "video":
            item["video_title_core_match"] = video_core_matches_query(item, query)

    # Relevance remains the primary ranking signal. Among equally relevant
    # candidates, favor verified CSS extraction. Video core-title matches
    # can then beat loosely related titles, followed by measured site health
    # (only after at least four real success/failure observations).
    def stable_site_score(item):
        health = item.get("source_health") or {}
        return health.get("score", 50) if health.get("samples", 0) >= 4 else 50

    results.sort(key=lambda item: (
        _quality(item, query),
        bool(item.get("extraction") == "heuristic"),
        not item.get("video_title_core_match", False),
        -stable_site_score(item),
        item["_source_order"],
    ))
    for item in results:
        item.pop("_source_order", None)
    annotate_same_title_candidates(results)

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
        "collapsed_duplicate_urls": collapsed_duplicate_urls,
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
        "elapsed_ms": round((time.monotonic() - started) * 1000), **cursor,
    }
    if skipped:
        response["note"] = (
            f"本次最多选择{limit}个已配置站点；另有{skipped}个可检索站点未尝试"
        )
    _put_cached(key, response)
    return response
