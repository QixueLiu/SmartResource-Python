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
                title=CASE WHEN novel_shelf.title_custom=1
                     THEN novel_shelf.title ELSE excluded.title END""",
            (site_id, book_url, title.strip(), book_url, "", now, now)
        )
        row = conn.execute(
            "SELECT * FROM novel_shelf WHERE site_id=? AND book_url=?",
            (site_id, book_url)
        ).fetchone()
    return dict(row)


def save_current_book_page(page_url: str, title: str):
    """Bookmark a user-selected novel page without crawling or reading it.

    The page may be an index rather than a confirmed book detail; the reader
    later attempts to identify its chapter links. Existing site rules and
    reading progress are never modified by this quick-save action.
    """
    from urllib.parse import urldefrag

    page_url = urldefrag(page_url.strip())[0]
    try:
        host = validate_public_url(page_url)
    except ValueError as exc:
        raise ShelfInputError(str(exc)) from exc
    parts = urlsplit(page_url)
    if not parts.path.strip("/") and not parts.query:
        raise ShelfInputError("请先打开小说详情或目录页，再加入书架")
    cleaned_title = title.strip()
    if not cleaned_title:
        raise ShelfInputError("小说名称不能为空")
    if len(cleaned_title) > 200:
        raise ShelfInputError("小说名称最多200字")

    created_site = False
    with connect() as conn:
        existing = next(
            (row for row in conn.execute("SELECT * FROM sites ORDER BY id DESC")
             if urlsplit(row["base_url"]).hostname == host),
            None
        )
        if existing is not None:
            if not existing["enabled"]:
                raise ShelfInputError("网站已停用，请先在网站管理中启用")
            if existing["kind"] == "video":
                raise ShelfInputError("该网站已设置为视频来源，请先检查网站类型")
            site_id, site_name = existing["id"], existing["name"]
        else:
            base_url = f"{parts.scheme}://{host}"
            cursor = conn.execute(
                """INSERT OR IGNORE INTO sites
                   (name, base_url, kind, enabled, scan_status, scan_message)
                   VALUES (?, ?, 'novel', 1, 'skipped', ?)""",
                (host, base_url, "浏览器小说收藏创建；未自动扫描网站")
            )
            row = conn.execute(
                "SELECT id, name FROM sites WHERE base_url=?", (base_url,)
            ).fetchone()
            if row is None:
                raise ShelfInputError("无法添加小说来源")
            site_id, site_name = row["id"], row["name"]
            created_site = cursor.rowcount == 1

        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """INSERT INTO novel_shelf
               (site_id, book_url, title, last_chapter_url,
                last_chapter_title, progress, created_at, updated_at)
               VALUES (?, ?, ?, ?, '', 0, ?, ?)
               ON CONFLICT(site_id, book_url) DO NOTHING""",
            (site_id, page_url, cleaned_title, page_url, now, now)
        )
        row = conn.execute(
            """SELECT * FROM novel_shelf WHERE site_id=? AND book_url=?""",
            (site_id, page_url)
        ).fetchone()
    return {**dict(row), "site_name": site_name, "created_site": created_site}


def rename_book(book_id: int, title: str):
    """Rename a book explicitly without touching reading state or sort order."""
    cleaned = title.strip()
    if not cleaned or len(cleaned) > 200:
        raise ShelfInputError("小说名称应为1至200字")
    with connect() as conn:
        changed = conn.execute(
            "UPDATE novel_shelf SET title=?, title_custom=1 WHERE id=?",
            (cleaned, book_id),
        ).rowcount
        if not changed:
            raise KeyError("书架中没有这本书")
        return dict(conn.execute(
            "SELECT * FROM novel_shelf WHERE id=?", (book_id,)
        ).fetchone())


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
