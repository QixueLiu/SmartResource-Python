import os
import hmac
import sqlite3
import asyncio
from contextlib import asynccontextmanager
from typing import Literal
from urllib.parse import urlsplit
from fastapi import FastAPI, HTTPException, Header, Depends, Query, BackgroundTasks
from .db import connect, initialize
from .models import SiteIn, SitePatch, UrlImport, CompareRequest, AnalyzeRequest, ScanRequest, ReaderRequest, ShelfCreate, BookQuickSave, BookTitlePatch, ShelfProgress, AlternativesRequest, VideoFavoriteCreate, VideoQuickSave, VideoTitlePatch, VideoLastPage
from .security import validate_site, validate_public_url
from .search import search_all
from .novels import compare
from .reader import extract_reader_page, expand_toc_result
from .fallback_sources import find_sources
from .site_health import health_map, site_health_detail, observe, classify_error
from .shelf import add_book, save_current_book_page, rename_book, list_books, save_progress, remove_book, ShelfInputError
from .video_library import (
    list_videos, save_video, save_current_video_page, rename_video, reset_video_page, remember_video_page, delete_video, VideoLibraryError
)
from .video_episodes import parse_episodes
from .analyzer import analyze_html
from .fetcher import fetch_html
from .browser_renderer import fetch_analysis_html
from .verification import verify_search
from datetime import datetime, timezone
from .autoconfig import scan_sites, enqueue_scan, pending_site_ids
from .ai_rules import enabled as ai_enabled, config as ai_config, suggest as ai_suggest


@asynccontextmanager
async def lifespan(app: FastAPI):
    initialize()
    yield


app = FastAPI(title="SmartResource Python API", version="0.21.0", lifespan=lifespan)

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


def output_site(row, health=None):
    return {
        **dict(row),
        "enabled": bool(row["enabled"]),
        "health": health if health is not None else health_map([row["id"]])[row["id"]],
    }


def store_site(conn, site: SiteIn):
    values = [getattr(site, name) for name in SITE_FIELDS]
    values[SITE_FIELDS.index("enabled")] = int(site.enabled)
    columns = ", ".join(SITE_FIELDS)
    params = ", ".join("?" for _ in SITE_FIELDS)
    cursor = conn.execute(f"INSERT OR IGNORE INTO sites({columns}) VALUES({params})", values)
    return bool(cursor.rowcount)


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "smartresource-python", "version": "0.21.0"}


@app.get("/api/sites", dependencies=[Depends(require_token)])
def sites():
    with connect() as conn:
        rows = conn.execute("SELECT * FROM sites ORDER BY id DESC").fetchall()
    health = health_map([row["id"] for row in rows])
    return {"items": [output_site(row, health[row["id"]]) for row in rows]}


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
        if count:
            conn.execute("DELETE FROM site_health_events WHERE site_id=?", (site_id,))
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


@app.get("/api/sites/health", dependencies=[Depends(require_token)])
def sites_health():
    with connect() as conn:
        rows = conn.execute("SELECT id, name FROM sites ORDER BY id DESC").fetchall()
    data = health_map([row["id"] for row in rows])
    return {"items": [
        {"name": row["name"], **data[row["id"]]} for row in rows
    ], "window_days": 30}


@app.get("/api/sites/{site_id}/health", dependencies=[Depends(require_token)])
def single_site_health(site_id: int):
    with connect() as conn:
        row = conn.execute("SELECT id FROM sites WHERE id=?", (site_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "站点不存在")
    return site_health_detail(site_id)


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



@app.post("/api/sites/{site_id}/quick-check", dependencies=[Depends(require_token)])
async def quick_check_site(site_id: int):
    """One user-initiated lightweight check; no Ollama or Chromium.

    Missing search templates are filled only after a positive homepage sample
    search. Existing user rules are NEVER overwritten; a verified replacement
    is returned for explicit approval in the app.
    """
    with connect() as conn:
        row = conn.execute("SELECT * FROM sites WHERE id=? AND enabled=1", (site_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "找不到已启用的网站")
    site = dict(row)
    host = urlsplit(site["base_url"]).hostname
    old = site.get("search_url_template") or ""
    try:
        async with asyncio.timeout(8):
            # Static HTML only, 1 homepage fetch and at most 1 search probe.
            html = await fetch_html(site["base_url"], host)
            report = analyze_html(html, site["base_url"], site)
            discovered = report.get("search_candidate") or {}
            proposal = discovered.get("search_url_template") or ""
            template = proposal or old
            if not template:
                return {
                    "status": "needs_rule", "verified": False,
                    "auto_applied": False, "candidate_template": None,
                    "message": "未找到可验证的 GET 搜索入口，保留原网站浏览",
                }
            # Never trust form action without validating its same-site URL.
            validate_public_url(
                template.replace("{q}", "sample").replace("{page}", "1")
            )
            if validate_public_url(
                template.replace("{q}", "sample").replace("{page}", "1")
            ) != host:
                raise ValueError("搜索入口不是同一网站")
            result = await verify_search(
                {**site, "search_url_template": template}, html, fetch=fetch_html
            )
    except TimeoutError as exc:
        raise HTTPException(504, "快速检查超时（8秒），未修改网站规则") from exc
    except ValueError as exc:
        raise HTTPException(422, "网址或搜索表单无效：" + str(exc)[:100]) from exc
    except Exception as exc:
        raise HTTPException(502, "快速检查未完成：" + type(exc).__name__) from exc

    verified = result["status"] == "probe_passed"
    candidate_different = bool(old and proposal and proposal != old)
    if candidate_different:
        # A different form candidate can be reviewed, but not saved silently.
        return {
            "status": result["status"], "verified": verified,
            "auto_applied": False, "candidate_template": proposal if verified else None,
            "message": ("新搜索入口已通过一个作品样本验证，可确认后替换旧规则"
                        if verified else "发现不同的搜索入口，但样本未通过；旧规则未改动"),
        }

    applied = False
    if verified and not old and proposal:
        with connect() as conn:
            cursor = conn.execute(
                """UPDATE sites SET search_url_template=?, scan_status='probe_passed',
                   scan_message=?, scan_checked_at=?
                   WHERE id=? AND enabled=1 AND
                   (search_url_template IS NULL OR search_url_template='')""",
                (proposal, "快速检查：单个首页作品样本试搜通过",
                 datetime.now(timezone.utc).isoformat(), site_id),
            )
            applied = cursor.rowcount == 1
    elif verified and old:
        with connect() as conn:
            conn.execute(
                """UPDATE sites SET scan_status='probe_passed',
                   scan_message=?, scan_checked_at=?
                   WHERE id=? AND enabled=1 AND search_url_template=?""",
                ("快速检查：已保存搜索地址的单样本验证通过",
                 datetime.now(timezone.utc).isoformat(), site_id, old),
            )
    return {
        "status": result["status"], "verified": verified,
        "auto_applied": applied, "candidate_template": None,
        "message": ("已自动配置经过样本验证的搜索入口" if applied
                    else "已保存搜索地址的样本验证通过，未更改已有规则" if verified
                    else result["message"] + "；未改动原有规则"),
    }


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



@app.get("/api/ai/status", dependencies=[Depends(require_token)])
def ai_status():
    """Configuration only; it does not check whether the model is loaded."""
    return {**ai_config(), "connection_tested": False}


@app.post("/api/sites/{site_id}/ai-analyze", dependencies=[Depends(require_token)])
async def analyze_site_ai(site_id: int, request: AnalyzeRequest):
    """AI suggestions are validated on the DOM and NEVER saved automatically."""
    if not ai_enabled():
        raise HTTPException(503, "本地 AI 尚未启用：需要设置 SMARTRESOURCE_AI_ENABLED=1")
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM sites WHERE id=? AND enabled=1", (site_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(404, "未找到启用中的网站")
    site = dict(row)
    page_url = request.page_url or site["base_url"]
    host = urlsplit(site["base_url"]).hostname
    if validate_public_url(page_url) != host:
        raise HTTPException(422, "AI 只能分析已添加站点的同域名网页")
    try:
        html, fetch_mode = await fetch_analysis_html(
            page_url, host, http_fetch=fetch_html
        )
        baseline = analyze_html(html, page_url, site)
        outcome = await ai_suggest(html, page_url)
        baseline["ai_analysis"] = outcome
        baseline["fetch_mode"] = fetch_mode
        baseline["notes"].append("AI 候选只在当前 DOM 校验，不自动保存或覆盖配置")
        return baseline
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            502, "AI 网页分析失败，请使用普通规则或原网页：" + str(exc)[:150]
        ) from exc


@app.get("/api/search", dependencies=[Depends(require_token)])
async def search(
    q: str = Query(min_length=1, max_length=120),
    mode: Literal["fast", "full"] = "fast",
    kind: Literal["all", "novel", "video"] = "all",
    refresh: bool = False,
):
    if not q.strip():
        raise HTTPException(422, "请输入要搜索的作品名称")
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM sites WHERE enabled=1 ORDER BY id DESC LIMIT 500"
        ).fetchall()
    return await search_all(
        [dict(row) for row in rows], q.strip(),
        mode=mode, kind=kind, refresh=refresh
    )


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
        if result.get("kind") == "toc" and request.max_toc_pages > 1:
            async def fetch_followup(next_url, expected_host):
                page_html, _ = await fetch_analysis_html(
                    next_url, expected_host, http_fetch=fetch_html
                )
                return page_html
            result = await expand_toc_result(
                result, site, fetch_followup, max_pages=request.max_toc_pages
            )
        result["fetch_mode"] = fetch_mode
        if result.get("kind") in ("chapter", "toc"):
            observe(site["id"], "reader", "success", "ok")
        else:
            observe(site["id"], "reader", "failure", "parse_failure")
        return result
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(422, str(exc)[:180]) from exc
    except Exception as exc:
        observe(site["id"], "reader", "failure", classify_error(exc))
        raise HTTPException(
            502, "网页正文提取未完成，请使用原网页：" + type(exc).__name__
        ) from exc


@app.get("/api/shelf", dependencies=[Depends(require_token)])
def shelf_list():
    """Book metadata and last saved progress, stored on this private server."""
    return {"items": list_books()}


@app.post("/api/shelf", status_code=201, dependencies=[Depends(require_token)])
def shelf_add(book: ShelfCreate):
    # Re-adding a book does not reset its chapter or scroll percentage.
    try:
        return add_book(book.site_id, book.book_url, book.title)
    except ShelfInputError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/shelf/quick-save", status_code=201, dependencies=[Depends(require_token)])
def shelf_quick_save(request: BookQuickSave):
    """Save a user-selected browser URL. No fetching or site scanning."""
    try:
        return save_current_book_page(request.url, request.title)
    except ShelfInputError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.patch("/api/shelf/{book_id}/title", dependencies=[Depends(require_token)])
def shelf_rename(book_id: int, update: BookTitlePatch):
    try:
        return rename_book(book_id, update.title)
    except ShelfInputError as exc:
        raise HTTPException(422, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.put("/api/shelf/{book_id}/progress", dependencies=[Depends(require_token)])
def shelf_progress(book_id: int, update: ShelfProgress):
    try:
        return save_progress(
            book_id, update.chapter_url, update.chapter_title, update.progress
        )
    except ShelfInputError as exc:
        raise HTTPException(422, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.delete("/api/shelf/{book_id}", dependencies=[Depends(require_token)])
def shelf_delete(book_id: int):
    try:
        return remove_book(book_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post("/api/reader/alternatives", dependencies=[Depends(require_token)])
async def reader_alternatives(request: AlternativesRequest):
    """Find same-title books and verify one matching chapter on trusted sites.

    No book-source switching is performed by this endpoint; it returns evidence
    for the Android user to approve.
    """
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM novel_shelf WHERE id=?", (request.book_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(404, "书架中没有这本小说")
        sites = [dict(s) for s in conn.execute(
            "SELECT * FROM sites WHERE enabled=1 ORDER BY id DESC"
        ).fetchall()]
    result = await find_sources(
        dict(row), sites, request.chapter_title, max_sites=request.max_sites
    )
    return {"book_id": request.book_id, **result}


@app.get("/api/videos/shelf", dependencies=[Depends(require_token)])
def video_favorites():
    return {"items": list_videos()}


@app.post("/api/videos/shelf", status_code=201, dependencies=[Depends(require_token)])
def video_favorite_add(item: VideoFavoriteCreate):
    try:
        return save_video(item.site_id, item.title, item.video_url)
    except VideoLibraryError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/videos/quick-save", status_code=201, dependencies=[Depends(require_token)])
def video_quick_save(item: VideoQuickSave):
    """Save an explicitly chosen browser page without fetching external content."""
    try:
        return save_current_video_page(item.title, item.url)
    except VideoLibraryError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.patch("/api/videos/shelf/{video_id}/title", dependencies=[Depends(require_token)])
def video_change_title(video_id: int, item: VideoTitlePatch):
    try:
        return rename_video(video_id, item.title)
    except VideoLibraryError as exc:
        raise HTTPException(422, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.delete("/api/videos/shelf/{video_id}/last-page", dependencies=[Depends(require_token)])
def video_clear_last_page(video_id: int):
    try:
        return reset_video_page(video_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.put("/api/videos/shelf/{video_id}/last-page", dependencies=[Depends(require_token)])
def video_last_page(video_id: int, item: VideoLastPage):
    try:
        return remember_video_page(video_id, item.url)
    except VideoLibraryError as exc:
        raise HTTPException(422, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc



@app.get("/api/videos/shelf/{video_id}/episodes", dependencies=[Depends(require_token)])
async def video_episode_links(video_id: int):
    """Read one saved public detail page, never scan a full series or media."""
    with connect() as conn:
        row = conn.execute(
            """SELECT video_library.video_url, sites.base_url, sites.enabled
               FROM video_library JOIN sites ON video_library.site_id = sites.id
               WHERE video_library.id=?""", (video_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(404, "视频收藏不存在")
    if not row["enabled"]:
        return {"status": "unavailable", "items": [], "note": "来源网站已停用，请先启用后重试。"}
    host = urlsplit(row["base_url"]).hostname
    if validate_public_url(row["video_url"]) != host:
        raise HTTPException(422, "收藏地址与网站域名不一致")
    try:
        # One public HTML request with a strict overall response-time cap.
        html = await asyncio.wait_for(
            fetch_html(row["video_url"], host), timeout=7.0
        )
        return parse_episodes(html, row["video_url"], host)
    except Exception:
        return {
            "status": "unavailable", "items": [],
            "note": "暂时无法识别选集，可继续在原网站直接观看。"
        }


@app.delete("/api/videos/shelf/{video_id}", dependencies=[Depends(require_token)])
def video_favorite_delete(video_id: int):
    try:
        return delete_video(video_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
