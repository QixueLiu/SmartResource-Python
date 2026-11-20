"""Single-user private novel shelf and reading progress.

The FastAPI token protects these endpoints. The SQLite database lives on the
user's own server; no third-party content is stored, just URLs and progress.
"""
from datetime import datetime, timezone
from urllib.parse import urlsplit
from .db import connect
from .security import validate_public_url


class ShelfInputError(ValueError):
    pass


def _same_site(url: str, site_host: str):
    try:
        host = validate_public_url(url)
    except ValueError as exc:
        raise ShelfInputError(str(exc)) from exc
    if host != site_host:
        raise ShelfInputError("作品和章节必须属于同一已添加的网站")
    if len(url) > 1500:
        raise ShelfInputError("网址过长")


def list_books():
    with connect() as conn:
        rows = conn.execute(
            """SELECT shelf.*, sites.name AS site_name
               FROM novel_shelf AS shelf
               LEFT JOIN sites ON sites.id=shelf.site_id
               ORDER BY shelf.updated_at DESC, shelf.id DESC"""
        ).fetchall()
    return [dict(row) for row in rows]


def add_book(site_id: int, book_url: str, title: str):
    with connect() as conn:
        site = conn.execute("SELECT * FROM sites WHERE id=? AND enabled=1", (site_id,)).fetchone()
        if site is None:
            raise ShelfInputError("网站不存在或已停用")
        host = urlsplit(site["base_url"]).hostname
        _same_site(book_url, host)
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """INSERT INTO novel_shelf(
                site_id, book_url, title, last_chapter_url,
                last_chapter_title, progress, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 0, ?, ?)
            ON CONFLICT(site_id,book_url) DO UPDATE SET
                title=excluded.title""",
            (site_id, book_url, title.strip(), book_url, "", now, now)
        )
        row = conn.execute(
            "SELECT * FROM novel_shelf WHERE site_id=? AND book_url=?",
            (site_id, book_url)
        ).fetchone()
    return dict(row)


def save_progress(book_id: int, chapter_url: str, chapter_title: str, progress: float):
    with connect() as conn:
        row = conn.execute(
            """SELECT shelf.*, sites.base_url AS site_url
               FROM novel_shelf AS shelf
               JOIN sites ON shelf.site_id=sites.id
               WHERE shelf.id=?""",
            (book_id,)
        ).fetchone()
        if row is None:
            raise KeyError("书架中没有这本书")
        host = urlsplit(row["site_url"]).hostname
        _same_site(chapter_url, host)
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """UPDATE novel_shelf SET
               last_chapter_url=?, last_chapter_title=?, progress=?,
               updated_at=? WHERE id=?""",
            (chapter_url, chapter_title.strip(), round(progress, 4), now, book_id)
        )
        saved = conn.execute("SELECT * FROM novel_shelf WHERE id=?", (book_id,)).fetchone()
    return dict(saved)


def remove_book(book_id: int):
    with connect() as conn:
        result = conn.execute("DELETE FROM novel_shelf WHERE id=?", (book_id,))
    if not result.rowcount:
        raise KeyError("书架中没有这本书")
    return {"deleted": True, "id": book_id}
