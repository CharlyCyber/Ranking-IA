"""Orquestador de actualizaciones: fetch → validate → persist → detect → notify.

Reglas del safety net:
- Cada fuente se valida ANTES de persistir; si falla, se conservan los datos
  antiguos de ESA fuente (status "stale") en lugar de sobrescribirlos.
- Los cambios se detectan con diff local determinista (detector.py); Gemini solo
  interviene para pulir el boletín, y con plantilla de fallback si falla.
- Las alertas se emiten a todos los suscriptores; los errores, solo al admin.
"""

import asyncio
import json
import logging
import os
from datetime import datetime

from src.config import ADMIN_CHAT_ID, TELEGRAM_TOKEN
from src.db import Database
from src.detector import change_summary, detect_changes, render_alert
from src.fetcher import DataFetcher
from src.trending import fetch_trending
from src.validator import is_legacy_snapshot, validate_source

logger = logging.getLogger(__name__)


class DataUpdater:
    """Maneja la actualización periódica de datos de rankings y tendencias."""

    def __init__(self, bot=None, brain=None):
        self.bot = bot
        self.fetcher = DataFetcher()
        self._brain = brain
        self.db = Database()
        self.output_path = os.path.join("data", "rankings.json")
        self.last_check = None
        self.last_change = None

    @property
    def brain(self):
        if self._brain is None:
            from src.brain import get_brain  # importación diferida: evita ciclos y carga Gemini al arrancar

            self._brain = get_brain()
        return self._brain

    # ------------------------------------------------------------------ datos

    def _load_snapshot(self) -> dict | None:
        """Último snapshot estructurado (SQLite primero, fichero como respaldo)."""
        snap = self.db.get_latest_snapshot()
        if isinstance(snap, dict) and isinstance(snap.get("data"), dict):
            return snap["data"]
        if os.path.exists(self.output_path):
            try:
                with open(self.output_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return data
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning("No se pudo leer el snapshot local: %s", exc)
        return None

    async def check_and_update(self, force=False) -> dict:
        result = {
            "success": False,
            "updated": False,
            "changes_detected": False,
            "message": "",
            "timestamp": datetime.now().isoformat(),
        }

        try:
            logger.info("🔄 Iniciando verificación de actualizaciones...")
            old = self._load_snapshot()
            if isinstance(old, dict) and old.get("generated_at"):
                result["last_update"] = old["generated_at"]

            # 1. Captura: rankings + tendencias (cada fuente falla de forma independiente)
            fetched = await self.fetcher.fetch_all()
            fetched.update(await fetch_trending())
            result["sources_scraped"] = list(fetched)

            # 2. Safety net: fuente inválida conserva sus datos anteriores
            merged: dict[str, dict] = {}
            stale: list[str] = []
            now_iso = datetime.now().isoformat()
            old_sources = (old or {}).get("sources") if isinstance(old, dict) else None
            old_sources = old_sources if isinstance(old_sources, dict) else {}

            for source, payload in fetched.items():
                errors = validate_source(source, payload)
                if not errors:
                    merged[source] = payload
                    continue

                stale.append(source)
                previous = old_sources.get(source)
                if isinstance(previous, dict) and previous.get("status") == "ok":
                    merged[source] = {
                        **previous,
                        "status": "stale",
                        "error": "; ".join(errors)[:300],
                        "stale_since": now_iso,
                    }
                    logger.warning("🛡️ Safety net: %s inválida; se conservan datos previos. %s", source, errors[:3])
                else:
                    merged[source] = (
                        payload if isinstance(payload, dict) else {"status": "error", "error": str(payload)[:300]}
                    )
                    logger.error("Fuente %s falló y no hay datos previos: %s", source, errors[:3])

            snapshot = {"generated_at": now_iso, "sources": merged}

            # 3. Persistencia: SQLite + fichero JSON (respaldo rama `data`)
            self.db.save_snapshot(snapshot)
            os.makedirs("data", exist_ok=True)
            with open(self.output_path, "w", encoding="utf-8") as f:
                json.dump(snapshot, f, ensure_ascii=False, indent=2)
            result["success"] = True
            result["updated"] = True
            self.last_check = datetime.now()

            # Historial de estrellas para deltas de tendencia
            gh = merged.get("github") or {}
            if gh.get("status") == "ok" and gh.get("items"):
                try:
                    self.db.save_repo_stars(gh["items"])
                except Exception as exc:  # noqa: BLE001 - el historial no puede tumbar la actualización
                    logger.warning("No se pudo guardar historial de estrellas: %s", exc)

            # 4. Detección de cambios (diff local; cero tokens)
            events = detect_changes(old, snapshot)
            baseline = is_legacy_snapshot(old)

            if force:
                result["message"] = "Actualización forzada completada"
            elif stale and not events:
                result["message"] = f"Datos actualizados con {len(stale)} fuente(s) en stale: {', '.join(stale)}"
            elif events:
                result["changes_detected"] = True
                summary = change_summary(events)
                result["comparison_result"] = summary
                alert = await self._compose_alert(events, summary)
                self.db.save_change(summary)
                self.last_change = datetime.now()
                delivered = await self._broadcast(alert)
                result["message"] = f"Cambios detectados ({len(events)} eventos), enviado a {delivered} chat(s)"
                logger.info("📣 %s", result["message"])
            elif baseline:
                result["message"] = "Línea base estructurada establecida (primer snapshot v3)"
            else:
                result["message"] = "Sin cambios detectados"
                logger.info("✅ Sin cambios significativos.")

            self.db.cleanup_old_data()
            return result

        except Exception as e:
            error_msg = f"Error en actualización: {str(e)}"
            logger.error(error_msg, exc_info=True)
            result["message"] = error_msg
            result["error"] = str(e)
            await self._notify_admin(f"⚠️ <b>Error en actualización automática</b>\n\n{error_msg[:500]}", is_error=True)
            return result

    # ------------------------------------------------------------- notificaciones

    async def _compose_alert(self, events: list[dict], summary: str) -> str:
        """Plantilla determinista; Gemini solo pule el boletín si está disponible."""
        template = render_alert(events)
        try:
            draft = await asyncio.to_thread(self.brain.draft_newsletter, template, summary)
            if draft and len(draft.strip()) > 50:
                return draft
        except Exception as exc:  # noqa: BLE001 - el pulido es opcional
            logger.warning("No se pudo pulir el boletín con Gemini: %s", exc)
        return template

    async def _broadcast(self, text: str) -> int:
        """Envía un mensaje a todos los suscriptores (o al admin si no hay nadie)."""
        if not TELEGRAM_TOKEN or not text:
            logger.warning("Broadcast omitido: falta TELEGRAM_TOKEN o mensaje")
            return 0

        chat_ids = [c for c in self.db.list_subscribers() if c and c != "None"]
        if not chat_ids and ADMIN_CHAT_ID:
            chat_ids = [str(ADMIN_CHAT_ID)]
        if not chat_ids:
            logger.warning("Broadcast omitido: sin suscriptores ni admin")
            return 0

        delivered = 0
        for chat_id in chat_ids:
            try:
                await self._send_to(chat_id, text)
                delivered += 1
            except Exception as exc:  # noqa: BLE001 - un chat caído no debe abortar el resto
                logger.error("No se pudo enviar a %s: %s", chat_id, exc)
        return delivered

    async def _send_to(self, chat_id: str, text: str) -> None:
        if self.bot:
            await self.bot.send_message(chat_id=int(chat_id), text=text[:4096], parse_mode="HTML")
        else:
            import requests

            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            resp = requests.post(
                url,
                json={"chat_id": chat_id, "text": text[:4096], "parse_mode": "HTML"},
                timeout=10,
            )
            resp.raise_for_status()

    async def _notify_admin(self, text: str, is_error: bool = False) -> None:
        if not ADMIN_CHAT_ID:
            return
        try:
            await self._send_to(str(ADMIN_CHAT_ID), text)
        except Exception as exc:  # noqa: BLE001
            logger.error("No se pudo notificar al admin: %s", exc)

    # ------------------------------------------------------------------ estado

    def get_status(self) -> dict:
        return {
            "last_check": self.last_check.isoformat() if self.last_check else None,
            "last_change": self.last_change.isoformat() if self.last_change else None,
            "data_exists": os.path.exists(self.output_path),
            "output_path": self.output_path,
            "subscribers": len(self.db.list_subscribers()),
        }


async def update_data(bot=None, force=False) -> dict:
    """Función standalone (GitHub Actions / manual): python -m src.update_data."""
    updater = DataUpdater(bot=bot)
    return await updater.check_and_update(force=force)
