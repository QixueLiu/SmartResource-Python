import os
import hmac
import sqlite3
from contextlib import asynccontextmanager
from urllib.parse import urlsplit
from fastapi import FastAPI, HTTPException, Header, Depends, Query
from .db import connect, initialize
from .models import SiteIn, SitePatch, UrlImport, CompareRequest
from .security import validate_site, validate_public_url
from .search import search_all
from .novels import compare


@asynccontextmanager
async def lifespan(app: FastAPI):
    initialize()
    yield


app = FastAPI(title="SmartResource Python API", version="0.2.0", lifespan=lifespan)

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
    return {"status": "ok", "service": "smartresource-python", "version": "0.2.0"}


@app.get("/api/sites", dependencies=[Depends(require_token)])
def sites():
    with connect() as conn:
        rows = conn.execute("SELECT * FROM sites ORDER BY id DESC").fetchall()
    return {"items": [output_site(row) for row in rows]}


@app.post("/api/sites", status_code=201, dependencies=[Depends(require_token)])
def create_site(site: SiteIn):
    try:
        validate_site(site)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    with connect() as conn:
        if not store_site(conn, site):
            raise HTTPException(409, "此网站网址已存在")
        row = conn.execute("SELECT * FROM sites WHERE id = last_insert_rowid()").fetchone()
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
def import_sites(items: list[SiteIn]):
    if len(items) > 1000:
        raise HTTPException(413, "一次最多导入 1000 条")
    created, failed = 0, []
    with connect() as conn:
        for index, site in enumerate(items):
            try:
                validate_site(site)
                created += int(store_site(conn, site))
            except (ValueError, sqlite3.Error) as exc:
                failed.append({"index": index, "reason": str(exc)[:150]})
    return {"created": created, "existing": len(items) - created - len(failed), "failed": failed}


@app.post("/api/sites/import-text", dependencies=[Depends(require_token)])
def import_text(data: UrlImport):
    lines = [line.strip() for line in data.text.splitlines() if line.strip()]
    if len(lines) > 1000:
        raise HTTPException(413, "一次最多导入 1000 行")
    created, failed = 0, []
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
                created += int(store_site(conn, site))
            except (ValueError, sqlite3.Error) as exc:
                failed.append({"line": index + 1, "reason": str(exc)[:150]})
    return {"created": created, "existing": len(lines) - created - len(failed), "failed": failed}


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
