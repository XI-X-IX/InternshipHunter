from __future__ import annotations

import sqlite3
from pathlib import Path

from .models import Job


class Storage:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS seen (
                   key TEXT PRIMARY KEY,
                   fingerprint TEXT NOT NULL,
                   title TEXT, company TEXT, location TEXT, url TEXT,
                   seen_at TEXT DEFAULT CURRENT_TIMESTAMP
               )"""
        )
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_fp ON seen(fingerprint)")
        self.conn.execute("CREATE TABLE IF NOT EXISTS meta (name TEXT PRIMARY KEY, value TEXT)")
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS requests (
                   key TEXT, kind TEXT, done_at TEXT DEFAULT CURRENT_TIMESTAMP,
                   PRIMARY KEY (key, kind)
               )"""
        )
        self.conn.commit()

    def is_empty(self) -> bool:
        return self.conn.execute("SELECT 1 FROM seen LIMIT 1").fetchone() is None

    def is_seen(self, job: Job) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM seen WHERE key = ? OR fingerprint = ? LIMIT 1",
            (job.key, job.fingerprint),
        ).fetchone()
        return row is not None

    def mark_seen(self, job: Job) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO seen (key, fingerprint, title, company, location, url)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (job.key, job.fingerprint, job.title, job.company, job.location, job.url),
        )
        self.conn.commit()

    def get_job(self, key: str) -> dict | None:
        row = self.conn.execute(
            "SELECT key, title, company, location, url FROM seen WHERE key = ?", (key,)
        ).fetchone()
        if not row:
            return None
        return dict(zip(("key", "title", "company", "location", "url"), row))

    def get_meta(self, name: str, default: str = "") -> str:
        row = self.conn.execute("SELECT value FROM meta WHERE name = ?", (name,)).fetchone()
        return row[0] if row else default

    def set_meta(self, name: str, value: str) -> None:
        self.conn.execute("INSERT OR REPLACE INTO meta (name, value) VALUES (?, ?)", (name, value))
        self.conn.commit()

    def request_done(self, key: str, kind: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM requests WHERE key = ? AND kind = ?", (key, kind)
        ).fetchone() is not None

    def mark_request_done(self, key: str, kind: str) -> None:
        self.conn.execute("INSERT OR IGNORE INTO requests (key, kind) VALUES (?, ?)", (key, kind))
        self.conn.commit()

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM seen").fetchone()[0]

    def close(self) -> None:
        self.conn.close()
