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
               DO UPDATE SET title=excluded.title""",
            (site_id, title.strip(), video_url, video_url, now, now),
        )
        row = conn.execute(
            "SELECT * FROM video_library WHERE site_id=? AND video_url=?",
            (site_id, video_url),
        ).fetchone()
    return dict(row)


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
        conn.execute(
            "UPDATE video_library SET last_url=?, updated_at=? WHERE id=?",
            (url, now, video_id)
        )
        updated = conn.execute("SELECT * FROM video_library WHERE id=?", (video_id,)).fetchone()
    return dict(updated)


def delete_video(video_id: int):
    with connect() as conn:
        affected = conn.execute(
            "DELETE FROM video_library WHERE id=?", (video_id,)
        ).rowcount
    if not affected:
        raise KeyError("视频收藏不存在")
    return {"deleted": True, "id": video_id}
