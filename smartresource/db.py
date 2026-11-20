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
        # The app server may have stopped while analyzing; these scans can be retried.
        conn.execute("UPDATE sites SET scan_status='pending' WHERE scan_status IN ('queued','running')")
