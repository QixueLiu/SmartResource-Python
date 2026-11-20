"""Bounded automatic site onboarding with optional Chromium and search probes.

Scans never overwrite user-defined selectors or search URLs. Passing one positive
search example is evidence, not a universal claim of site compatibility.
"""
import asyncio
from datetime import datetime, timezone
from urllib.parse import urlsplit
from .analyzer import analyze_html
from .db import connect
from .fetcher import fetch_html
from .browser_renderer import fetch_analysis_html
from .verification import verify_search
from .models import SiteIn
from .security import validate_site

_SCAN_LOCK = asyncio.Lock()
_SCAN_CONCURRENCY = 2


def enqueue_scan(ids: list[int]) -> list[int]:
    unique = list(dict.fromkeys(i for i in ids if isinstance(i, int) and i > 0))
    queued = []
    with connect() as conn:
        for site_id in unique:
            updated = conn.execute(
                """UPDATE sites SET scan_status='queued', scan_message='等待自动检测'
                   WHERE id=? AND enabled=1 AND scan_status NOT IN ('queued','running')""",
                (site_id,)
            )
            if updated.rowcount:
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
            (status, note[:250], datetime.now(timezone.utc).isoformat(), site_id)
        )


async def _scan_one(site_id: int):
    with connect() as conn:
        row = conn.execute("SELECT * FROM sites WHERE id=?", (site_id,)).fetchone()
    if row is None or not row["enabled"]:
        return
    site = dict(row)
    _set_status(site_id, "running", "正在检测公开网页结构")
    try:
        host = urlsplit(site["base_url"]).hostname
        html, source = await fetch_analysis_html(
            site["base_url"], host, http_fetch=fetch_html
        )
        sample = html[:25000].lower()
        if any(term in sample for term in
               ("cf-chl-", "captcha-container", "verify you are human")):
            _set_status(site_id, "needs_rule", "出现人机验证，已停止自动分析")
            return
        result = analyze_html(html, site["base_url"], site)
        candidate = result.get("search_candidate") or {}
        inferred = result.get("kind_guess")
        kind = (inferred if site["kind"] == "other"
                and inferred in ("novel", "video", "mixed") else site["kind"])
        proposed = candidate.get("search_url_template")
        existing = site.get("search_url_template")
        search_url = existing
        evidence = [f"来源：{source}"]
        if not existing and proposed:
            merged = {field: site.get(field) for field in SiteIn.model_fields}
            merged["kind"] = kind
            merged["search_url_template"] = proposed
            validate_site(SiteIn(**merged))
            search_url = proposed
            evidence.append("已发现 GET 搜索入口")
        if kind != site["kind"]:
            evidence.append("首页推断类型：" + kind)
        if result.get("toc_candidate"):
            evidence.append("首页疑似存在章节列表，作品详情页另行识别")

        # One matching title/link pair is a limited positive control.
        if search_url:
            async def fetch_probe(url, expected_host):
                content, _ = await fetch_analysis_html(url, expected_host, http_fetch=fetch_html)
                return content
            probed = await verify_search(
                {**site, "kind": kind, "search_url_template": search_url},
                html, fetch=fetch_probe
            )
            status = probed["status"]
            evidence.append(probed["message"])
        else:
            status = "needs_rule"
            evidence.append("没有发现可自动配置的 GET 搜索入口")

        with connect() as conn:
            # A manual edit during scanning always wins over the inferred URL.
            conn.execute(
                """UPDATE sites SET
                   kind=CASE WHEN kind='other' THEN ? ELSE kind END,
                   search_url_template=COALESCE(search_url_template, ?),
                   scan_status=?, scan_message=?, scan_checked_at=?
                   WHERE id=? AND enabled=1""",
                (kind, proposed if not existing else None,
                 status, "；".join(evidence)[:250],
                 datetime.now(timezone.utc).isoformat(), site_id)
            )
    except Exception as exc:
        _set_status(site_id, "error", f"{type(exc).__name__}：{str(exc)[:130]}")


async def scan_sites(ids: list[int]):
    """Single-process queue; up to two scans run concurrently."""
    async with _SCAN_LOCK:
        semaphore = asyncio.Semaphore(_SCAN_CONCURRENCY)
        async def one(site_id):
            async with semaphore:
                await _scan_one(site_id)
        await asyncio.gather(*(one(site_id) for site_id in ids))
