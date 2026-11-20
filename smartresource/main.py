import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Header, Depends, Query
from .db import connect, initialize
from .models import SiteIn
from .security import validate_site
from .search import search_all

@asynccontextmanager
async def lifespan(app: FastAPI):
    initialize()
    yield

app = FastAPI(title="SmartResource Python API", version="0.1.0", lifespan=lifespan)

def require_token(x_api_key: str | None = Header(default=None)):
    expected = os.getenv("SMARTRESOURCE_API_TOKEN", "")
    if expected and x_api_key != expected:
        raise HTTPException(401, "无效 API Token")
    # Without a token, the operator MUST keep the server listening on localhost.

@app.get("/api/health")
def health():
    return {"status": "ok", "service": "smartresource-python", "version": "0.1.0"}

@app.get("/api/sites", dependencies=[Depends(require_token)])
def sites():
    with connect() as conn:
        rows = conn.execute("SELECT * FROM sites ORDER BY id DESC").fetchall()
    return {"items": [{**dict(r), "enabled": bool(r["enabled"])} for r in rows]}

@app.post("/api/sites", status_code=201, dependencies=[Depends(require_token)])
def create_site(site: SiteIn):
    try:
        validate_site(site)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    with connect() as conn:
        try:
            c = conn.execute("INSERT INTO sites(name,base_url,kind,enabled,search_url_template,item_selector,title_selector,link_selector) VALUES (?,?,?,?,?,?,?,?)",
                (site.name, site.base_url.rstrip('/'), site.kind, int(site.enabled), site.search_url_template, site.item_selector, site.title_selector, site.link_selector))
        except Exception as exc:
            raise HTTPException(409, f"网站已存在或数据不合法: {str(exc)[:100]}")
    return {"id": c.lastrowid, "message": "created"}

@app.post("/api/sites/import", dependencies=[Depends(require_token)])
def import_sites(items: list[SiteIn]):
    if len(items) > 1000:
        raise HTTPException(413, "每次最多导入 1000 条")
    created, failed = 0, []
    for idx, site in enumerate(items):
        try:
            validate_site(site)
            with connect() as conn:
                conn.execute("INSERT OR IGNORE INTO sites(name,base_url,kind,enabled,search_url_template,item_selector,title_selector,link_selector) VALUES (?,?,?,?,?,?,?,?)",
                    (site.name, site.base_url.rstrip('/'), site.kind, int(site.enabled), site.search_url_template, site.item_selector, site.title_selector, site.link_selector))
                created += conn.execute("SELECT changes()").fetchone()[0]
        except (ValueError, Exception) as exc:
            failed.append({"index": idx, "name": site.name, "reason": str(exc)[:160]})
    return {"created": created, "skipped_or_existing": len(items)-created-len(failed), "failed": failed}

@app.get("/api/search", dependencies=[Depends(require_token)])
async def search(q: str = Query(min_length=1, max_length=120)):
    with connect() as conn:
        rows = conn.execute("SELECT * FROM sites WHERE enabled=1 ORDER BY id DESC LIMIT 500").fetchall()
    return await search_all([dict(row) for row in rows], q)
