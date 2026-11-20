"""Private video favorites and last-opened episode/page history.

Stores metadata and same-origin URLs only. No video downloads, stream
extraction, player interception, DRM handling or third-party services.
"""
from datetime import datetime, timezone
from urllib.parse import urlsplit
from .db import connect
from .security import validate_public_url


class VideoLibraryError(ValueError):
    pass


def _check_site_url(url: str, site_base_url: str) -> None:
    try:
        host = validate_public_url(url)
    except ValueError as exc:
        raise VideoLibraryError(str(exc)) from exc
    if host != urlsplit(site_base_url).hostname:
        raise VideoLibraryError("视频页面必须与已添加网站属于同一域名")


def list_videos():
    with connect() as conn:
        return [
            dict(row) for row in conn.execute(
                """SELECT video_library.*, sites.name AS site_name
                   FROM video_library LEFT JOIN sites
                   ON sites.id = video_library.site_id
                   ORDER BY video_library.updated_at DESC, video_library.id DESC"""
            )
        ]


def save_video(site_id: int, title: str, video_url: str):
    with connect() as conn:
        site = conn.execute(
            "SELECT * FROM sites WHERE id=? AND enabled=1", (site_id,)
        ).fetchone()
        if site is None:
            raise VideoLibraryError("网站不存在或已停用")
        if site["kind"] == "novel":
            raise VideoLibraryError("小说网站不能加入视频收藏")
        _check_site_url(video_url, site["base_url"])
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """INSERT INTO video_library
               (site_id, title, video_url, last_url, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(site_id, video_url)
               DO UPDATE SET title=CASE
                   WHEN video_library.title_custom=1 THEN video_library.title
                   ELSE excluded.title END""",
            (site_id, title.strip(), video_url, video_url, now, now),
        )
        row = conn.execute(
            "SELECT * FROM video_library WHERE site_id=? AND video_url=?",
            (site_id, video_url),
        ).fetchone()
    return dict(row)


def rename_video(video_id: int, title: str):
    """Explicit user titles override future automatic or repeat bookmarking."""
    cleaned = title.strip()
    if not cleaned:
        raise VideoLibraryError("视频名称不能为空")
    if len(cleaned) > 200:
        raise VideoLibraryError("视频名称最多200字")
    with connect() as conn:
        changed = conn.execute(
            "UPDATE video_library SET title=?, title_custom=1 WHERE id=?",
            (cleaned, video_id),
        ).rowcount
        if not changed:
            raise KeyError("视频收藏不存在")
        return dict(conn.execute(
            "SELECT * FROM video_library WHERE id=?", (video_id,)
        ).fetchone())


def reset_video_page(video_id: int):
    """Forget the last visited page without removing the favorite itself."""
    with connect() as conn:
        changed = conn.execute(
            """UPDATE video_library SET last_url=video_url, updated_at=?
               WHERE id=?""",
            (datetime.now(timezone.utc).isoformat(), video_id),
        ).rowcount
        if not changed:
            raise KeyError("视频收藏不存在")
        return dict(conn.execute(
            "SELECT * FROM video_library WHERE id=?", (video_id,)
        ).fetchone())


def remember_video_page(video_id: int, url: str):
    with connect() as conn:
        record = conn.execute(
            """SELECT video_library.*, sites.base_url AS site_url
               FROM video_library JOIN sites ON sites.id=video_library.site_id
               WHERE video_library.id=?""", (video_id,)
        ).fetchone()
        if record is None:
            raise KeyError("视频收藏不存在")
        _check_site_url(url, record["site_url"])
        now = datetime.now(timezone.utc).isoformat()
        if url != record["last_url"]:
            conn.execute(
                "UPDATE video_library SET last_url=?, updated_at=? WHERE id=?",
                (url, now, video_id)
            )
        updated = conn.execute("SELECT * FROM video_library WHERE id=?", (video_id,)).fetchone()
    return dict(updated)




def save_current_video_page(title: str, page_url: str):
    """One explicit browser bookmark action, without fetching the website.

    Existing site configuration is never changed; when a host is new, create
    a video-only source with no scan or search rules. One-page bookmarks keep
    working even if a site doesn't expose a machine-readable search form.
    """
    from urllib.parse import urldefrag

    page_url = urldefrag(page_url.strip())[0]
    try:
        host = validate_public_url(page_url)
    except ValueError as exc:
        raise VideoLibraryError(str(exc)) from exc
    parsed = urlsplit(page_url)
    if not parsed.path.strip("/") and not parsed.query:
        raise VideoLibraryError("请先打开具体视频或剧集页面，再点击收藏")
    title = title.strip()
    if not title:
        raise VideoLibraryError("视频标题不能为空")

    new_site = False
    with connect() as conn:
        # Never create a second site just because someone opened http vs https.
        existing = next(
            (row for row in conn.execute("SELECT * FROM sites ORDER BY id DESC")
             if urlsplit(row["base_url"]).hostname == host),
            None,
        )
        if existing is not None:
            if not existing["enabled"]:
                raise VideoLibraryError("此网站已停用，请在网站管理中启用后收藏")
            if existing["kind"] == "novel":
                raise VideoLibraryError("该网站已设置为小说来源，请先检查网站类型")
            site_id = existing["id"]
            site_name = existing["name"]
        else:
            base_url = f"{parsed.scheme}://{host}"
            # INSERT OR IGNORE protects concurrent explicit saves.
            cursor = conn.execute(
                """INSERT OR IGNORE INTO sites
                   (name, base_url, kind, enabled, scan_status, scan_message)
                   VALUES (?, ?, 'video', 1, 'skipped', ?)""",
                (host, base_url, "浏览器视频收藏创建；未执行自动网页扫描"),
            )
            row = conn.execute(
                "SELECT id, name FROM sites WHERE base_url=?", (base_url,)
            ).fetchone()
            if row is None:
                raise VideoLibraryError("无法创建视频来源")
            site_id, site_name = row["id"], row["name"]
            new_site = cursor.rowcount == 1
    saved = save_video(site_id, title, page_url)
    return {**saved, "site_name": site_name, "created_site": new_site}

def delete_video(video_id: int):
    with connect() as conn:
        affected = conn.execute(
            "DELETE FROM video_library WHERE id=?", (video_id,)
        ).rowcount
    if not affected:
        raise KeyError("视频收藏不存在")
    return {"deleted": True, "id": video_id}
