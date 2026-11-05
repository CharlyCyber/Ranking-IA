"""Servidor Flask (Dashboard + API pública + healthcheck) — aislado del core del bot.

En planes gratuitos con sleep (Render), ``self_ping`` usa ``PUBLIC_URL`` /
``RENDER_EXTERNAL_URL`` si existe para atravesar el proxy externo; si no, hace
chequeo local.
"""

import logging
import os

import httpx
from flask import Flask, redirect, request

from src.config import PUBLIC_URL
from src.db import utc_now_iso
from src.web import web

logger = logging.getLogger(__name__)


def create_flask_app() -> Flask:
    app = Flask(__name__)
    app.register_blueprint(web)

    @app.route("/health")
    def health():
        return {
            "status": "ok",
            "message": "Ranking AI Agent is Alive!",
            "version": "4.0.0",
            "timestamp": utc_now_iso(),
            "uptime_check": True,
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
    port = int(os.environ.get("PORT", 5000))
    flask_app.run(host="0.0.0.0", port=port, use_reloader=False, threaded=True)


async def self_ping(context) -> None:
    """Job cada 5 min: usa la URL pública externa si existe (evita sleep real en Render)."""
    port = int(os.environ.get("PORT", 5000))
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
