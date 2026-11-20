import os
import sqlite3
from pathlib import Path

DEFAULT_DB = "./data/smartresource.db"

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
            link_selector TEXT
        )
        """)
