"""Bounded automatic site setup. No JS execution, no third-party services.

Observed GET form templates can be saved as UNVERIFIED candidates so the
existing keyword-link search fallback works without CSS configuration.
The analyzer never overwrites human-configured rules.
"""
import asyncio
from datetime import datetime, timezone
from urllib.parse import urlsplit
from .analyzer import analyze_html
from .db import connect
from .fetcher import fetch_html
from .models import SiteIn
from .security import validate_site

_SCAN_LOCK = asyncio.Lock()
_SCAN_CONCURRENCY = 2
_ALLOWED_STATES = ("pending", "needs_rule", "error", "queued", "running")


def enqueue_scan(ids: list[int]) -> list[int]:
    unique = list(dict.fromkeys(i for i in ids if isinstance(i, int) and i > 0))
    queued = []
    with connect() as conn:
        for site_id in unique:
            row = conn.execute("SELECT id FROM sites WHERE id=? AND enabled=1", (site_id,)).fetchone()
            if row:
                conn.execute(
                    "UPDATE sites SET scan_status='queued', scan_message='等待自动检测' WHERE id=?",
                    (site_id,)
                )
                queued.append(site_id)
    return queued


def pending_site_ids(limit: int = 100) -> list[int]:
    with connect() as conn:
        rows = conn.execute(
            """SELECT id FROM sites WHERE enabled=1
               AND scan_status IN ('pending','needs_rule','error')
               ORDER BY id ASC LIMIT ?""",
            (max(1, min(limit, 200)),)
        ).fetchall()
    return [row["id"] for row in rows]


def _set_status(site_id: int, status: str, note: str):
    with connect() as conn:
        conn.execute(
            """UPDATE sites SET scan_status=?, scan_message=?,
               scan_checked_at=? WHERE id=?""",
            (status, note[:200], datetime.now(timezone.utc).isoformat(), site_id)
        )


async def _scan_one(site_id: int):
    with connect() as conn:
        row = conn.execute("SELECT * FROM sites WHERE id=?", (site_id,)).fetchone()
    if row is None or not row["enabled"]:
        return
    site = dict(row)
    _set_status(site_id, "running", "正在检查网站首页")
    try:
        html = await fetch_html(site["base_url"], urlsplit(site["base_url"]).hostname)
        # A challenge/login page is not a site search form; don't configure from it.
        sample = html[:20000].lower()
        if any(term in sample for term in ("cf-chl-", "captcha-container", "verify you are human")):
            _set_status(site_id, "needs_rule", "页面包含人机验证，已停止自动分析")
            return
        result = analyze_html(html, site["base_url"], site)
        candidate = result.get("search_candidate") or {}
        inferred = result.get("kind_guess")
        kind = inferred if site["kind"] == "other" and inferred in ("novel","video","mixed") else site["kind"]
        search_url = site.get("search_url_template")
        evidence = []
        if not search_url and candidate.get("search_url_template"):
            proposed = candidate["search_url_template"]
            merged = {field: site.get(field) for field in SiteIn.model_fields}
            merged["kind"] = kind
            merged["search_url_template"] = proposed
            validate_site(SiteIn(**merged))
            search_url = proposed
            evidence.append("发现 GET 搜索表单，已保存候选模板（尚未验证搜索结果）")
        if kind != site["kind"]:
            evidence.append("按首页特征推断网站类型为 " + kind)
        if result.get("toc_candidate"):
            evidence.append("首页存在疑似章节列表（目录将在打开作品时自动检测）")
        status = "candidate" if search_url else "needs_rule"
        if not evidence:
            evidence.append("首页未发现可自动配置的 GET 搜索表单" if not search_url else "保留已有搜索规则")
        with connect() as conn:
            # Do not overwrite a search URL manually added while scan was in flight.
            conn.execute(
                """UPDATE sites SET
                   kind=CASE WHEN kind='other' THEN ? ELSE kind END,
                   search_url_template=COALESCE(search_url_template, ?),
                   scan_status=?, scan_message=?, scan_checked_at=?
                   WHERE id=? AND enabled=1""",
                (kind, search_url if not site.get("search_url_template") else None,
                 status, "；".join(evidence)[:200],
                 datetime.now(timezone.utc).isoformat(), site_id)
            )
    except Exception as exc:
        _set_status(site_id, "error", f"{type(exc).__name__}：{str(exc)[:130]}")


async def scan_sites(ids: list[int]):
    """Single in-process queue, two public-page fetches at a time."""
    async with _SCAN_LOCK:
        semaphore = asyncio.Semaphore(_SCAN_CONCURRENCY)
        async def one(site_id):
            async with semaphore:
                await _scan_one(site_id)
        await asyncio.gather(*(one(site_id) for site_id in ids))
