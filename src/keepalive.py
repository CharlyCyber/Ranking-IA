"""Servidor Flask de keep-alive (Render free) y dashboard — aislado del core del bot.

El self-ping cada5 minutos mantiene despierto el plan gratuito de Render; si el
hosting no duerme (Fly, Railway, Render paid), este módulo es inocuo.
"""

import logging
import os
from datetime import datetime

import httpx
from flask import Flask

from src.web import web

logger = logging.getLogger(__name__)


def create_flask_app() -> Flask:
    app = Flask(__name__)
    app.register_blueprint(web)

    @app.route("/")
    @app.route("/health")
    def health():
        return {
            "status": "ok",
            "message": "Ranking AI Agent is Alive!",
            "timestamp": datetime.now().isoformat(),
            "uptime_check": True,
        }

    return app


flask_app = create_flask_app()


def run_flask() -> None:
    port = int(os.environ.get("PORT", 5000))
    flask_app.run(host="0.0.0.0", port=port)


async def self_ping(context) -> None:
    """Job: ping a /health cada5 min para evitar que Render duerma el servicio."""
    port = int(os.environ.get("PORT", 5000))
    url = f"http://localhost:{port}/health"
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=10)
            if resp.status_code == 200:
                logger.info("🏓 Self-ping OK (%s)", url)
            else:
                logger.warning("⚠️ Self-ping respondió con status %s", resp.status_code)
    except Exception as exc:  # noqa: BLE001 - el ping es best-effort
        logger.warning("⚠️ Self-ping falló: %s", exc)
