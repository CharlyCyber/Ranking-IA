import json
import os
import sqlite3
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
            conn.execute("""
                CREATE TABLE IF NOT EXISTS subscribers (
                    chat_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS repo_stars (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    full_name TEXT NOT NULL,
                    stars INTEGER NOT NULL,
                    observed_at TEXT NOT NULL
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

    # --- Suscriptores (modo multiusuario) ---

    def add_subscriber(self, chat_id):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO subscribers (chat_id, created_at, active) VALUES (?, ?, 1)",
                (str(chat_id), datetime.now().isoformat()),
            )
            conn.commit()

    def remove_subscriber(self, chat_id):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE subscribers SET active = 0 WHERE chat_id = ?", (str(chat_id),))
            conn.commit()

    def is_subscriber(self, chat_id) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT active FROM subscribers WHERE chat_id = ?", (str(chat_id),)
            ).fetchone()
            return bool(row and row[0])

    def list_subscribers(self) -> list[str]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute("SELECT chat_id FROM subscribers WHERE active = 1").fetchall()
            return [r[0] for r in rows]

    # --- Historial de estrellas de GitHub (deltas de tendencia) ---

    def save_repo_stars(self, repos: list[dict]):
        """Registra la observación de estrellas de los repos de tendencia."""
        now = datetime.now().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.executemany(
                "INSERT INTO repo_stars (full_name, stars, observed_at) VALUES (?, ?, ?)",
                [(r["full_name"], int(r.get("stars", 0)), now) for r in repos if r.get("full_name")],
            )
            conn.commit()

    def get_latest_repo_stars(self) -> dict[str, int]:
        """Último valor observado por repo (para calcular deltas de estrellas)."""
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT full_name, stars FROM repo_stars rs1 "
                "WHERE id = (SELECT MAX(id) FROM repo_stars rs2 WHERE rs2.full_name = rs1.full_name)"
            ).fetchall()
            return {r[0]: r[1] for r in rows}

    def cleanup_old_data(self, days=31):
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM snapshots WHERE created_at < ?", (cutoff,))
            conn.execute("DELETE FROM changes WHERE detected_at < ?", (cutoff,))
            # El historial de estrellas solo hace falta para deltas recientes.
            conn.execute("DELETE FROM repo_stars WHERE observed_at < ?", (cutoff,))
            conn.commit()
