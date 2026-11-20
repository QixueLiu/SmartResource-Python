import os
import hmac
import sqlite3
from contextlib import asynccontextmanager
from urllib.parse import urlsplit
from fastapi import FastAPI, HTTPException, Header, Depends, Query, BackgroundTasks
from .db import connect, initialize
from .models import SiteIn, SitePatch, UrlImport, CompareRequest, AnalyzeRequest, ScanRequest, ReaderRequest
from .security import validate_site, validate_public_url
from .search import search_all
from .novels import compare
from .reader import extract_reader_page
from .analyzer import analyze_html
from .fetcher import fetch_html
from .browser_renderer import fetch_analysis_html
from .verification import verify_search
from datetime import datetime, timezone
from .autoconfig import scan_sites, enqueue_scan, pending_site_ids


@asynccontextmanager
async def lifespan(app: FastAPI):
    initialize()
    yield


app = FastAPI(title="SmartResource Python API", version="0.6.0", lifespan=lifespan)

SITE_FIELDS = (
    "name", "base_url", "kind", "enabled", "search_url_template",
    "item_selector", "title_selector", "link_selector",
    "toc_item_selector", "toc_title_selector", "toc_link_selector"
)


def require_token(x_api_key: str | None = Header(default=None)):
    expected = os.getenv("SMARTRESOURCE_API_TOKEN", "")
    if not expected:
        raise HTTPException(503, "请先配置 SMARTRESOURCE_API_TOKEN")
    if not x_api_key or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(401, "API Token 无效")


def output_site(row):
    return {**dict(row), "enabled": bool(row["enabled"])}


def store_site(conn, site: SiteIn):
    values = [getattr(site, name) for name in SITE_FIELDS]
    values[SITE_FIELDS.index("enabled")] = int(site.enabled)
    columns = ", ".join(SITE_FIELDS)
    params = ", ".join("?" for _ in SITE_FIELDS)
    cursor = conn.execute(f"INSERT OR IGNORE INTO sites({columns}) VALUES({params})", values)
    return bool(cursor.rowcount)


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "smartresource-python", "version": "0.6.0"}


@app.get("/api/sites", dependencies=[Depends(require_token)])
def sites():
    with connect() as conn:
        rows = conn.execute("SELECT * FROM sites ORDER BY id DESC").fetchall()
    return {"items": [output_site(row) for row in rows]}


@app.post("/api/sites", status_code=201, dependencies=[Depends(require_token)])
def create_site(site: SiteIn, background_tasks: BackgroundTasks, auto_scan: bool = True):
    try:
        validate_site(site)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    with connect() as conn:
        if not store_site(conn, site):
            raise HTTPException(409, "此网站网址已存在")
        row = conn.execute("SELECT * FROM sites WHERE id = last_insert_rowid()").fetchone()
    if auto_scan:
        queued = enqueue_scan([row["id"]])
        if queued:
            background_tasks.add_task(scan_sites, queued)
    return output_site(row)


@app.patch("/api/sites/{site_id}", dependencies=[Depends(require_token)])
def update_site(site_id: int, patch: SitePatch):
    with connect() as conn:
        row = conn.execute("SELECT * FROM sites WHERE id=?", (site_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "站点不存在")
        merged = {**dict(row), **patch.model_dump(exclude_unset=True)}
        merged.pop("id", None)
        try:
            site = SiteIn(**merged)
            validate_site(site)
        except (ValueError, Exception) as exc:
            raise HTTPException(422, str(exc)) from exc
        values = [getattr(site, name) for name in SITE_FIELDS]
        values[SITE_FIELDS.index("enabled")] = int(site.enabled)
        assignments = ", ".join(f"{name}=?" for name in SITE_FIELDS)
        try:
            conn.execute(f"UPDATE sites SET {assignments} WHERE id=?", [*values, site_id])
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409, "站点网址已存在") from exc
        updated = conn.execute("SELECT * FROM sites WHERE id=?", (site_id,)).fetchone()
    return output_site(updated)


@app.delete("/api/sites/{site_id}", dependencies=[Depends(require_token)])
def delete_site(site_id: int):
    with connect() as conn:
        count = conn.execute("DELETE FROM sites WHERE id=?", (site_id,)).rowcount
    if not count:
        raise HTTPException(404, "站点不存在")
    return {"deleted": True, "id": site_id}


@app.post("/api/sites/import", dependencies=[Depends(require_token)])
def import_sites(items: list[SiteIn], background_tasks: BackgroundTasks, auto_scan: bool = True):
    if len(items) > 1000:
        raise HTTPException(413, "一次最多导入 1000 条")
    created, failed, new_ids = 0, [], []
    with connect() as conn:
        for index, site in enumerate(items):
            try:
                validate_site(site)
                if store_site(conn, site):
                    created += 1
                    new_ids.append(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
            except (ValueError, sqlite3.Error) as exc:
                failed.append({"index": index, "reason": str(exc)[:150]})
    queued = enqueue_scan(new_ids) if auto_scan else []
    if queued:
        background_tasks.add_task(scan_sites, queued)
    return {"created": created, "existing": len(items)-created-len(failed),
            "failed": failed, "auto_scan_queued": len(queued)}


@app.post("/api/sites/import-text", dependencies=[Depends(require_token)])
def import_text(data: UrlImport, background_tasks: BackgroundTasks, auto_scan: bool = True):
    lines = [line.strip() for line in data.text.splitlines() if line.strip()]
    if len(lines) > 1000:
        raise HTTPException(413, "一次最多导入 1000 行")
    created, failed, new_ids = 0, [], []
    with connect() as conn:
        for index, line in enumerate(lines):
            try:
                # Allows "Name | https://example.org" or one URL per line.
                if "|" in line:
                    name, url = [s.strip() for s in line.split("|", 1)]
                else:
                    url, name = line, ""
                host = validate_public_url(url)
                parsed = urlsplit(url)
                if not name:
                    name = host
                # A website is identified by the public origin, not by an article URL.
                origin = f"{parsed.scheme}://{parsed.netloc}"
                site = SiteIn(name=name[:100], base_url=origin, kind=data.kind)
                if store_site(conn, site):
                    created += 1
                    new_ids.append(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
            except (ValueError, sqlite3.Error) as exc:
                failed.append({"line": index + 1, "reason": str(exc)[:150]})
    queued = enqueue_scan(new_ids) if auto_scan else []
    if queued:
        background_tasks.add_task(scan_sites, queued)
    return {"created": created, "existing": len(lines)-created-len(failed),
            "failed": failed, "auto_scan_queued": len(queued)}


@app.post("/api/sites/scan", dependencies=[Depends(require_token)])
async def scan_sites_route(request: ScanRequest, background_tasks: BackgroundTasks):
    ids = request.site_ids if request.site_ids else pending_site_ids(request.limit)
    queued = enqueue_scan(ids)
    if queued:
        background_tasks.add_task(scan_sites, queued)
    return {"queued": len(queued), "site_ids": queued,
            "note": "后台最多并行检测 2 个站点。通过 GET /api/sites 查看每站状态；候选搜索规则并非验证成功。"}


@app.post("/api/sites/{site_id}/analyze", dependencies=[Depends(require_token)])
async def analyze_site(site_id: int, request: AnalyzeRequest):
    with connect() as conn:
        row = conn.execute("SELECT * FROM sites WHERE id=?", (site_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "站点不存在")
    site = dict(row)
    page_url = request.page_url or site["base_url"]
    host = urlsplit(site["base_url"]).hostname
    try:
        if validate_public_url(page_url) != host:
            raise ValueError("只允许分析当前站点同域名的网址")
        html, mode = await fetch_analysis_html(page_url, host, http_fetch=fetch_html)
        result = analyze_html(html, page_url, site)
        result["fetch_mode"] = mode
        return result
    except ValueError as exc:
        if "只允许分析" in str(exc) or "不允许" in str(exc) or "仅支持" in str(exc):
            raise HTTPException(422, str(exc)) from exc
        raise HTTPException(502, "网站结构分析失败：" + str(exc)[:180]) from exc
    except Exception as exc:
        raise HTTPException(502, "目标网站访问或解析失败：" + str(exc)[:180]) from exc


@app.post("/api/sites/{site_id}/verify-search", dependencies=[Depends(require_token)])
async def verify_site_search(site_id: int):
    with connect() as conn:
        row = conn.execute("SELECT * FROM sites WHERE id=? AND enabled=1", (site_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "没有找到启用中的网站")
    site = dict(row)
    if not site.get("search_url_template"):
        raise HTTPException(422, "该站点尚未配置站内搜索入口")
    host = urlsplit(site["base_url"]).hostname
    try:
        home, mode = await fetch_analysis_html(site["base_url"], host, http_fetch=fetch_html)
        async def fetch_probe(url, expected_host):
            content, _ = await fetch_analysis_html(url, expected_host, http_fetch=fetch_html)
            return content
        result = await verify_search(site, home, fetch=fetch_probe)
        with connect() as conn:
            conn.execute(
                """UPDATE sites SET scan_status=?, scan_message=?, scan_checked_at=?
                   WHERE id=? AND enabled=1 AND search_url_template=?""",
                (result["status"], result["message"],
                 datetime.now(timezone.utc).isoformat(), site_id, site["search_url_template"])
            )
        return {**result, "site_id": site_id, "fetch_mode": mode}
    except Exception as exc:
        raise HTTPException(502, "搜索验证失败：" + str(exc)[:160]) from exc


@app.get("/api/search", dependencies=[Depends(require_token)])
async def search(q: str = Query(min_length=1, max_length=120)):
    with connect() as conn:
        rows = conn.execute("SELECT * FROM sites WHERE enabled=1 ORDER BY id DESC LIMIT 500").fetchall()
    return await search_all([dict(row) for row in rows], q)


@app.post("/api/novels/compare", dependencies=[Depends(require_token)])
async def compare_novels(request: CompareRequest):
    with connect() as conn:
        rows = conn.execute("SELECT * FROM sites WHERE enabled=1").fetchall()
    return await compare({row["id"]: dict(row) for row in rows}, request.candidates)


@app.post("/api/reader/open", dependencies=[Depends(require_token)])
async def reader_open(request: ReaderRequest):
    """Extract one public chapter or index page; always preserve original URL."""
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM sites WHERE id=? AND enabled=1",
            (request.site_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(404, "未找到已启用的站点")
    site = dict(row)
    host = urlsplit(site["base_url"]).hostname
    try:
        if validate_public_url(request.url) != host:
            raise HTTPException(422, "阅读地址必须与当前站点域名一致")
        html, fetch_mode = await fetch_analysis_html(
            request.url, host, http_fetch=fetch_html
        )
        result = extract_reader_page(html, request.url, site)
        result["fetch_mode"] = fetch_mode
        return result
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(422, str(exc)[:180]) from exc
    except Exception as exc:
        raise HTTPException(
            502, "网页正文提取未完成，请使用原网页：" + str(exc)[:130]
        ) from exc
