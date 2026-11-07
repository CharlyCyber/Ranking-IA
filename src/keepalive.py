"""Servidor HTTP (Dashboard + API pública + healthcheck 24/7) — aislado del core del bot.

- Arranca en <100 ms antes que cualquier descarga de navegador para que Render / HF Spaces /
  Koyeb detecten el puerto abierto al instante sin timeouts de arranque.
- ``/health`` devuelve telemetría real del scheduler horario (``last_update_check``,
  ``bot_polling_active``, ``update_interval_minutes``) para monitores como cron-job.org.
"""

import logging
import os

import httpx
from flask import Flask, redirect, request
from werkzeug.serving import make_server

from src.config import PUBLIC_URL, UPDATE_INTERVAL_MINUTES
from src.db import utc_now_iso
from src.web import web

logger = logging.getLogger(__name__)

RUNTIME_STATE: dict[str, object] = {
    "started_at": utc_now_iso(),
    "bot_polling_active": False,
    "chromium_ready": os.getenv("SKIP_CHROMIUM_INSTALL", "").lower() in ("1", "true", "yes"),
    "last_heartbeat": None,
    "last_update_check": None,
    "last_update_message": None,
}


def resolve_http_port() -> int:
    default_port = "7860" if os.getenv("SPACE_ID") else "5000"
    try:
        return int(os.environ.get("PORT", default_port))
    except ValueError:
        return int(default_port)


def create_flask_app() -> Flask:
    app = Flask(__name__)
    app.register_blueprint(web)

    @app.route("/health")
    def health():
        return {
            "status": "ok",
            "message": "Ranking AI Agent is Alive!",
            "version": "4.3.0",
            "timestamp": utc_now_iso(),
            "uptime_check": True,
            "update_interval_minutes": UPDATE_INTERVAL_MINUTES,
            "started_at": RUNTIME_STATE["started_at"],
            "bot_polling_active": RUNTIME_STATE["bot_polling_active"],
            "chromium_ready": RUNTIME_STATE["chromium_ready"],
            "last_heartbeat": RUNTIME_STATE["last_heartbeat"],
            "last_update_check": RUNTIME_STATE["last_update_check"],
            "last_update_message": RUNTIME_STATE["last_update_message"],
        }

    @app.route("/")
    def index():
        accept = request.headers.get("Accept", "")
        if "text/html" in accept and "application/json" not in accept:
            return redirect("/dashboard")
        return health()

    return app


flask_app = create_flask_app()


def run_flask() -> None:
    port = resolve_http_port()
    server = make_server("0.0.0.0", port, flask_app, threaded=True)
    logger.info("🌐 Servidor HTTP escuchando en 0.0.0.0:%d", port)
    server.serve_forever()


async def self_ping(context) -> None:
    """Job cada 5 min: usa la URL pública externa si existe (evita sleep en PaaS con proxy)."""
    port = resolve_http_port()
    base = PUBLIC_URL if PUBLIC_URL.startswith("http") else f"http://127.0.0.1:{port}"
    url = f"{base.rstrip('/')}/health"
    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.get(url, timeout=12)
            if resp.status_code == 200:
                logger.info("🏓 Health ping OK (%s)", url)
            else:
                logger.warning("⚠️ Health ping respondió con status %s (%s)", resp.status_code, url)
    except Exception as exc:  # noqa: BLE001
        logger.warning("⚠️ Health ping falló (%s): %s", url, exc)
