"""Capa de persistencia: SQLite local (WAL concurrente) + sincronización opcional Turso Cloud.

- Sin configuración extra: usa SQLite local en ``data/rankings.db`` con ``journal_mode=WAL``.
- Con ``TURSO_DATABASE_URL`` y ``TURSO_AUTH_TOKEN``: replica lecturas/escrituras contra
  Turso (libSQL Serverless gratuito) vía HTTP ``/v2/pipeline`` sin dependencias nativas,
  evitando perder suscriptores o histórico en despliegues con disco efímero.
"""

import json
import logging
import os
import sqlite3
from datetime import datetime, timedelta, timezone

import httpx

from src.config import TURSO_AUTH_TOKEN, TURSO_DATABASE_URL

logger = logging.getLogger(__name__)

DB_PATH = os.path.join("data", "rankings.db")
DEFAULT_PREFS = {"alerts": "all", "daily_brief": True}


def utc_now_iso() -> str:
    """Timestamp UTC canónico ISO-8601 (ordenable lexicográficamente en SQLite)."""
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


def _normalize_turso_url(url: str) -> str:
    if not url:
        return ""
    if url.startswith("libsql://"):
        url = "https://" + url[len("libsql://") :]
    return url.rstrip("/") + "/v2/pipeline"


class Database:
    def __init__(self, db_path: str = DB_PATH, turso_url: str | None = None, turso_token: str | None = None):
        self.db_path = db_path
        # Solo usar Turso global cuando se instancia sobre la ruta por defecto (no en tests con tmp_path)
        use_global_turso = db_path == DB_PATH and turso_url is None
        raw_url = turso_url if turso_url is not None else (TURSO_DATABASE_URL if use_global_turso else "")
        raw_tok = turso_token if turso_token is not None else (TURSO_AUTH_TOKEN if use_global_turso else "")
        self._turso_endpoint = _normalize_turso_url(raw_url) if raw_url and raw_tok else ""
        self._turso_token = raw_tok if self._turso_endpoint else ""
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        parent = os.path.dirname(self.db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=15.0)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def _turso_exec(self, sql: str, params: tuple = ()) -> list[tuple] | None:
        """Ejecuta una consulta en Turso vía HTTP Pipeline API. Devuelve filas o None si falla."""
        if not self._turso_endpoint:
            return None
        args = []
        for p in params:
            if p is None:
                args.append({"type": "null"})
            elif isinstance(p, int):
                args.append({"type": "integer", "value": str(p)})
            elif isinstance(p, float):
                args.append({"type": "float", "value": p})
            else:
                args.append({"type": "text", "value": str(p)})

        payload = {
            "requests": [
                {"type": "execute", "stmt": {"sql": sql, "args": args}},
                {"type": "close"},
            ]
        }
        try:
            resp = httpx.post(
                self._turso_endpoint,
                headers={"Authorization": f"Bearer {self._turso_token}"},
                json=payload,
                timeout=8.0,
            )
            resp.raise_for_status()
            body = resp.json()
            res0 = ((body.get("results") or [{}])[0]).get("response") or {}
            rows_raw = (res0.get("result") or {}).get("rows") or []
            out = []
            for row in rows_raw:
                converted = []
                for cell in row:
                    ctype = cell.get("type")
                    val = cell.get("value")
                    if ctype == "null":
                        converted.append(None)
                    elif ctype == "integer":
                        converted.append(int(val))
                    elif ctype == "float":
                        converted.append(float(val))
                    else:
                        converted.append(val)
                out.append(tuple(converted))
            return out
        except Exception as exc:  # noqa: BLE001 - fallback transparente a SQLite local
            logger.warning("Turso HTTP API falló (%s); usando SQLite local.", exc)
            return None

    def _init_db(self) -> None:
        ddl_statements = [
            """
            CREATE TABLE IF NOT EXISTS snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                data TEXT NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_snapshots_created ON snapshots(created_at)",
            """
            CREATE TABLE IF NOT EXISTS changes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                detected_at TEXT NOT NULL,
                summary TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS subscribers (
                chat_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                preferences TEXT NOT NULL DEFAULT '{}'
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS repo_stars (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                full_name TEXT NOT NULL,
                stars INTEGER NOT NULL,
                observed_at TEXT NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_repo_stars_name ON repo_stars(full_name, id)",
            """
            CREATE TABLE IF NOT EXISTS briefing_cache (
                cache_key TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
        ]
        with self._connect() as conn:
            for ddl in ddl_statements:
                conn.execute(ddl)
            # Migración suave si la tabla subscribers ya existía sin columna preferences
            cols = {r[1] for r in conn.execute("PRAGMA table_info(subscribers)").fetchall()}
            if "preferences" not in cols:
                conn.execute("ALTER TABLE subscribers ADD COLUMN preferences TEXT NOT NULL DEFAULT '{}'")
            conn.commit()

        if self._turso_endpoint:
            for ddl in ddl_statements:
                self._turso_exec(ddl)

    # --------------------------------------------------------------- snapshots

    def save_snapshot(self, data: dict) -> None:
        now = utc_now_iso()
        serialized = json.dumps(data, ensure_ascii=False)
        sql = "INSERT INTO snapshots (created_at, data) VALUES (?, ?)"
        with self._connect() as conn:
            conn.execute(sql, (now, serialized))
            conn.commit()
        self._turso_exec(sql, (now, serialized))

    def get_latest_snapshot(self) -> dict | None:
        sql = "SELECT created_at, data FROM snapshots ORDER BY id DESC LIMIT 1"
        remote = self._turso_exec(sql)
        if remote:
            return {"created_at": remote[0][0], "data": json.loads(remote[0][1])}
        with self._connect() as conn:
            row = conn.execute(sql).fetchone()
            if row:
                return {"created_at": row[0], "data": json.loads(row[1])}
        return None

    def get_or_seed_latest_snapshot(self, json_path: str = os.path.join("data", "rankings.json")) -> dict | None:
        """Devuelve el último snapshot; si está vacío o el rankings.json local es más reciente, sincroniza."""
        snap = self.get_latest_snapshot()
        seed_data: dict | None = None
        if json_path and os.path.exists(json_path) and (snap is None or self.db_path == DB_PATH):
            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict) and loaded:
                    seed_data = loaded
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning("No se pudo leer %s: %s", json_path, exc)

        if seed_data is not None:
            seed_ts = str(seed_data.get("generated_at") or "")
            snap_ts = str(((snap or {}).get("data") or {}).get("generated_at") or (snap or {}).get("created_at") or "")
            if snap is None or (seed_ts and seed_ts > snap_ts):
                created = seed_ts or utc_now_iso()
                serialized = json.dumps(seed_data, ensure_ascii=False)
                sql = "INSERT INTO snapshots (created_at, data) VALUES (?, ?)"
                with self._connect() as conn:
                    conn.execute(sql, (created, serialized))
                    conn.commit()
                self._turso_exec(sql, (created, serialized))
                return {"created_at": created, "data": seed_data}

        return snap

    def get_snapshots(self, days: int = 30) -> list[dict]:
        cutoff = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)).isoformat()
        sql = "SELECT created_at, data FROM snapshots WHERE created_at >= ? ORDER BY id DESC"
        remote = self._turso_exec(sql, (cutoff,))
        if remote is not None:
            return [{"created_at": r[0], "data": json.loads(r[1])} for r in remote]
        with self._connect() as conn:
            rows = conn.execute(sql, (cutoff,)).fetchall()
            return [{"created_at": r[0], "data": json.loads(r[1])} for r in rows]

    # ----------------------------------------------------------------- changes

    def save_change(self, summary: str) -> None:
        now = utc_now_iso()
        sql = "INSERT INTO changes (detected_at, summary) VALUES (?, ?)"
        with self._connect() as conn:
            conn.execute(sql, (now, summary))
            conn.commit()
        self._turso_exec(sql, (now, summary))

    def get_changes(self, days: int = 30) -> list[dict]:
        cutoff = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)).isoformat()
        sql = "SELECT detected_at, summary FROM changes WHERE detected_at >= ? ORDER BY id DESC"
        remote = self._turso_exec(sql, (cutoff,))
        if remote is not None:
            return [{"detected_at": r[0], "summary": r[1]} for r in remote]
        with self._connect() as conn:
            rows = conn.execute(sql, (cutoff,)).fetchall()
            return [{"detected_at": r[0], "summary": r[1]} for r in rows]

    # ------------------------------------------------------------- subscribers

    def add_subscriber(self, chat_id: str | int) -> None:
        cid = str(chat_id)
        now = utc_now_iso()
        default_prefs = json.dumps(DEFAULT_PREFS, ensure_ascii=False)
        sql = (
            "INSERT INTO subscribers (chat_id, created_at, active, preferences) VALUES (?, ?, 1, ?) "
            "ON CONFLICT(chat_id) DO UPDATE SET active = 1"
        )
        with self._connect() as conn:
            conn.execute(sql, (cid, now, default_prefs))
            conn.commit()
        self._turso_exec(sql, (cid, now, default_prefs))

    def remove_subscriber(self, chat_id: str | int) -> None:
        cid = str(chat_id)
        sql = "UPDATE subscribers SET active = 0 WHERE chat_id = ?"
        with self._connect() as conn:
            conn.execute(sql, (cid,))
            conn.commit()
        self._turso_exec(sql, (cid,))

    def is_subscriber(self, chat_id: str | int) -> bool:
        cid = str(chat_id)
        sql = "SELECT active FROM subscribers WHERE chat_id = ?"
        remote = self._turso_exec(sql, (cid,))
        if remote is not None:
            return bool(remote and remote[0][0])
        with self._connect() as conn:
            row = conn.execute(sql, (cid,)).fetchone()
            return bool(row and row[0])

    def get_subscriber_prefs(self, chat_id: str | int) -> dict:
        cid = str(chat_id)
        sql = "SELECT preferences FROM subscribers WHERE chat_id = ?"
        raw = None
        remote = self._turso_exec(sql, (cid,))
        if remote:
            raw = remote[0][0]
        else:
            with self._connect() as conn:
                row = conn.execute(sql, (cid,)).fetchone()
                if row:
                    raw = row[0]
        prefs = dict(DEFAULT_PREFS)
        if raw:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    prefs.update(parsed)
            except json.JSONDecodeError:
                pass
        return prefs

    def set_subscriber_prefs(self, chat_id: str | int, updates: dict) -> dict:
        self.add_subscriber(chat_id)
        prefs = self.get_subscriber_prefs(chat_id)
        prefs.update(updates)
        serialized = json.dumps(prefs, ensure_ascii=False)
        sql = "UPDATE subscribers SET preferences = ? WHERE chat_id = ?"
        cid = str(chat_id)
        with self._connect() as conn:
            conn.execute(sql, (serialized, cid))
            conn.commit()
        self._turso_exec(sql, (serialized, cid))
        return prefs

    def list_subscribers(self) -> list[str]:
        sql = "SELECT chat_id FROM subscribers WHERE active = 1"
        remote = self._turso_exec(sql)
        if remote is not None:
            return [str(r[0]) for r in remote]
        with self._connect() as conn:
            rows = conn.execute(sql).fetchall()
            return [str(r[0]) for r in rows]

    def list_subscribers_with_prefs(self) -> list[dict]:
        sql = "SELECT chat_id, preferences FROM subscribers WHERE active = 1"
        rows = self._turso_exec(sql)
        if rows is None:
            with self._connect() as conn:
                rows = conn.execute(sql).fetchall()
        result = []
        for cid, raw in rows:
            prefs = dict(DEFAULT_PREFS)
            if raw:
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, dict):
                        prefs.update(parsed)
                except json.JSONDecodeError:
                    pass
            result.append({"chat_id": str(cid), "preferences": prefs})
        return result

    # -------------------------------------------------------------- repo_stars

    def save_repo_stars(self, repos: list[dict]) -> None:
        """Registra la observación de estrellas de los repos de tendencia."""
        now = utc_now_iso()
        entries = [(r["full_name"], int(r.get("stars", 0)), now) for r in repos if r.get("full_name")]
        if not entries:
            return
        with self._connect() as conn:
            conn.executemany(
                "INSERT INTO repo_stars (full_name, stars, observed_at) VALUES (?, ?, ?)",
                entries,
            )
            conn.commit()
        if self._turso_endpoint:
            for full_name, stars, ts in entries[:25]:
                self._turso_exec(
                    "INSERT INTO repo_stars (full_name, stars, observed_at) VALUES (?, ?, ?)",
                    (full_name, stars, ts),
                )

    def get_latest_repo_stars(self) -> dict[str, int]:
        """Último valor observado por repo."""
        sql = (
            "SELECT full_name, stars FROM repo_stars rs1 "
            "WHERE id = (SELECT MAX(id) FROM repo_stars rs2 WHERE rs2.full_name = rs1.full_name)"
        )
        remote = self._turso_exec(sql)
        if remote is not None:
            return {r[0]: int(r[1]) for r in remote}
        with self._connect() as conn:
            rows = conn.execute(sql).fetchall()
            return {r[0]: int(r[1]) for r in rows}

    def get_repo_stars_baseline(self, hours: int = 24) -> dict[str, int]:
        """Valor mínimo observado en la ventana reciente para detectar aceleración real."""
        cutoff = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=hours)).isoformat()
        sql = "SELECT full_name, MIN(stars) FROM repo_stars WHERE observed_at >= ? GROUP BY full_name"
        remote = self._turso_exec(sql, (cutoff,))
        if remote is not None:
            return {r[0]: int(r[1]) for r in remote}
        with self._connect() as conn:
            rows = conn.execute(sql, (cutoff,)).fetchall()
            return {r[0]: int(r[1]) for r in rows}

    # ---------------------------------------------------------- briefing_cache

    def get_briefing_cache(self, cache_key: str) -> str | None:
        if not cache_key:
            return None
        sql = "SELECT content FROM briefing_cache WHERE cache_key = ?"
        remote = self._turso_exec(sql, (cache_key,))
        if remote:
            return str(remote[0][0])
        with self._connect() as conn:
            row = conn.execute(sql, (cache_key,)).fetchone()
            return str(row[0]) if row else None

    def save_briefing_cache(self, cache_key: str, content: str) -> None:
        if not cache_key or not content:
            return
        now = utc_now_iso()
        sql = "INSERT OR REPLACE INTO briefing_cache (cache_key, content, created_at) VALUES (?, ?, ?)"
        with self._connect() as conn:
            conn.execute(sql, (cache_key, content, now))
            conn.commit()
        self._turso_exec(sql, (cache_key, content, now))

    def cleanup_old_data(self, days: int = 31) -> None:
        cutoff = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)).isoformat()
        brief_cutoff = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=3)).isoformat()
        with self._connect() as conn:
            conn.execute("DELETE FROM snapshots WHERE created_at < ?", (cutoff,))
            conn.execute("DELETE FROM changes WHERE detected_at < ?", (cutoff,))
            conn.execute("DELETE FROM repo_stars WHERE observed_at < ?", (cutoff,))
            conn.execute("DELETE FROM briefing_cache WHERE created_at < ?", (brief_cutoff,))
            conn.commit()
        if self._turso_endpoint:
            self._turso_exec("DELETE FROM snapshots WHERE created_at < ?", (cutoff,))
            self._turso_exec("DELETE FROM changes WHERE detected_at < ?", (cutoff,))
            self._turso_exec("DELETE FROM repo_stars WHERE observed_at < ?", (cutoff,))
            self._turso_exec("DELETE FROM briefing_cache WHERE created_at < ?", (brief_cutoff,))
