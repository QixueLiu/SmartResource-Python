import os
import sqlite3
from pathlib import Path

DEFAULT_DB = "./data/smartresource.db"
OPTIONAL_COLUMNS = {
    "toc_item_selector": "TEXT",
    "toc_title_selector": "TEXT",
    "toc_link_selector": "TEXT",
    "scan_status": "TEXT NOT NULL DEFAULT 'pending'",
    "scan_message": "TEXT",
    "scan_checked_at": "TEXT",
}


def connect():
    path = Path(os.getenv("SMARTRESOURCE_DB", DEFAULT_DB))
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def initialize():
    with connect() as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS sites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            base_url TEXT NOT NULL UNIQUE,
            kind TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            search_url_template TEXT,
            item_selector TEXT,
            title_selector TEXT,
            link_selector TEXT,
            toc_item_selector TEXT,
            toc_title_selector TEXT,
            toc_link_selector TEXT,
            scan_status TEXT NOT NULL DEFAULT 'pending',
            scan_message TEXT,
            scan_checked_at TEXT
        )
        """)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(sites)")}
        for column, kind in OPTIONAL_COLUMNS.items():
            if column not in columns:
                conn.execute(f"ALTER TABLE sites ADD COLUMN {column} {kind}")
        conn.execute("""
        CREATE TABLE IF NOT EXISTS novel_shelf (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            site_id INTEGER NOT NULL,
            book_url TEXT NOT NULL,
            title TEXT NOT NULL,
            last_chapter_url TEXT NOT NULL,
            last_chapter_title TEXT NOT NULL DEFAULT '',
            progress REAL NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (site_id, book_url)
        )
        """)
        shelf_columns = {row[1] for row in conn.execute("PRAGMA table_info(novel_shelf)")}
        if "title_custom" not in shelf_columns:
            conn.execute("ALTER TABLE novel_shelf ADD COLUMN title_custom INTEGER NOT NULL DEFAULT 0")
        conn.execute("CREATE INDEX IF NOT EXISTS ix_novel_shelf_updated ON novel_shelf(updated_at)")
        conn.execute("""
        CREATE TABLE IF NOT EXISTS site_health_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            site_id INTEGER NOT NULL,
            operation TEXT NOT NULL,
            outcome TEXT NOT NULL,
            reason TEXT NOT NULL,
            observed_at TEXT NOT NULL
        )
        """)
        conn.execute("""CREATE INDEX IF NOT EXISTS ix_health_site_date
            ON site_health_events(site_id, observed_at DESC)""")
        conn.execute("""
        CREATE TABLE IF NOT EXISTS video_library (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            site_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            video_url TEXT NOT NULL,
            last_url TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(site_id, video_url)
        )
        """)
        video_columns = {row[1] for row in conn.execute("PRAGMA table_info(video_library)")}
        if "title_custom" not in video_columns:
            conn.execute("ALTER TABLE video_library ADD COLUMN title_custom INTEGER NOT NULL DEFAULT 0")
        conn.execute("CREATE INDEX IF NOT EXISTS ix_video_library_updated ON video_library(updated_at)")
        # The app server may have stopped while analyzing; these scans can be retried.
        conn.execute("UPDATE sites SET scan_status='pending' WHERE scan_status IN ('queued','running')")
