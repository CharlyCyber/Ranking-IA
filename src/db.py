import sqlite3
import json
import os
from datetime import datetime, timedelta

DB_PATH = os.path.join("data", "rankings.db")


class Database:
    def __init__(self, db_path=DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    data TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS changes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    detected_at TEXT NOT NULL,
                    summary TEXT NOT NULL
                )
            """)
            conn.commit()

    def save_snapshot(self, data):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO snapshots (created_at, data) VALUES (?, ?)",
                (datetime.now().isoformat(), json.dumps(data, ensure_ascii=False))
            )
            conn.commit()

    def get_latest_snapshot(self):
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT created_at, data FROM snapshots ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if row:
                return {"created_at": row[0], "data": json.loads(row[1])}
            return None

    def get_snapshots(self, days=30):
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT created_at, data FROM snapshots WHERE created_at >= ? ORDER BY id DESC",
                (cutoff,)
            ).fetchall()
            return [{"created_at": r[0], "data": json.loads(r[1])} for r in rows]

    def save_change(self, summary):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO changes (detected_at, summary) VALUES (?, ?)",
                (datetime.now().isoformat(), summary)
            )
            conn.commit()

    def get_changes(self, days=30):
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT detected_at, summary FROM changes WHERE detected_at >= ? ORDER BY id DESC",
                (cutoff,)
            ).fetchall()
            return [{"detected_at": r[0], "summary": r[1]} for r in rows]

    def cleanup_old_data(self, days=31):
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM snapshots WHERE created_at < ?", (cutoff,))
            conn.execute("DELETE FROM changes WHERE detected_at < ?", (cutoff,))
            conn.commit()
