"""Orquestador de actualizaciones: fetch → validate → persist → detect → notify.

Reglas del safety net y mejoras v4.1 (24/7):
- Lock asíncrono global para impedir ejecuciones solapadas de Playwright (protege RAM).
- Cada fuente se valida ANTES de persistir; si falla, se conservan los datos previos como ``stale``.
- Deduplicación idempotente de cambios recientes (evita alertas duplicadas si conviven bot + backup).
- Soporte ``SKIP_TELEGRAM_BROADCAST`` para respaldo silencioso desde GitHub Actions cuando el bot
  24/7 ya está activo, con failover automático si el bot cae.
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone

import httpx

import src.config as cfg
from src.config import ADMIN_CHAT_ID, PUBLIC_CHANNEL_ID, TELEGRAM_TOKEN
from src.db import Database, utc_now_iso
from src.detector import (
    change_summary,
    detect_changes,
    filter_events_for_preference,
    render_alert,
)
from src.fetcher import DataFetcher
from src.html_utils import escape_html, split_html_message, strip_html
from src.keepalive import RUNTIME_STATE
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
        self.last_check: str | None = None
        self.last_change: str | None = None
        self._lock = asyncio.Lock()

    @property
    def brain(self):
        if self._brain is None:
            from src.brain import get_brain

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

    def _is_recent_duplicate_change(self, summary: str, hours: int = 2) -> bool:
        """Evita emitir dos veces exactamente el mismo cambio si comparten BD (ej. Turso)."""
        cutoff = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=hours)).isoformat()
        for ch in self.db.get_changes(days=1):
            if ch.get("detected_at", "") >= cutoff and ch.get("summary") == summary:
                return True
        return False

    async def check_and_update(self, force: bool = False) -> dict:
        now_iso = utc_now_iso()
        result = {
            "success": False,
            "updated": False,
            "changes_detected": False,
            "message": "",
            "timestamp": now_iso,
        }

        if self._lock.locked():
            result["message"] = "Ya hay una actualización en curso; espera unos segundos."
            return result

        async with self._lock:
            try:
                logger.info("🔄 Iniciando verificación horaria de actualizaciones...")
                old = self._load_snapshot()
                if isinstance(old, dict) and old.get("generated_at"):
                    result["last_update"] = old["generated_at"]

                star_baselines = self.db.get_repo_stars_baseline(hours=24)

                # 1. Captura: rankings + tendencias
                fetched = await self.fetcher.fetch_all()
                fetched.update(await fetch_trending())
                result["sources_scraped"] = list(fetched)

                # 2. Safety net: fuente inválida conserva sus datos anteriores
                merged: dict[str, dict] = {}
                stale: list[str] = []
                snap_iso = utc_now_iso()
                old_sources = (old or {}).get("sources") if isinstance(old, dict) else None
                old_sources = old_sources if isinstance(old_sources, dict) else {}

                for source, payload in fetched.items():
                    errors = validate_source(source, payload)
                    if not errors:
                        if (
                            source == "arena"
                            and payload.get("method") == "api"
                            and isinstance(old_sources.get("arena"), dict)
                        ):
                            new_models = list(payload.get("models") or [])
                            new_cats = {m.get("category") for m in new_models if isinstance(m, dict) and m.get("category")}
                            prev_models = old_sources["arena"].get("models") or []
                            extra_multimodal = [
                                m
                                for m in prev_models
                                if isinstance(m, dict) and m.get("category") and m.get("category") not in new_cats
                            ]
                            if extra_multimodal:
                                payload = {**payload, "models": new_models + extra_multimodal}
                        merged[source] = payload
                        continue

                    stale.append(source)
                    previous = old_sources.get(source)
                    if isinstance(previous, dict) and previous.get("status") in ("ok", "stale"):
                        merged[source] = {
                            **previous,
                            "status": "stale",
                            "error": "; ".join(errors)[:300],
                            "stale_since": previous.get("stale_since") or snap_iso,
                        }
                        logger.warning("🛡️ Safety net: %s inválida; se conservan datos previos. %s", source, errors[:3])
                    else:
                        merged[source] = (
                            payload if isinstance(payload, dict) else {"status": "error", "error": str(payload)[:300]}
                        )
                        logger.error("Fuente %s falló y no hay datos previos: %s", source, errors[:3])

                snapshot = {"generated_at": snap_iso, "sources": merged}

                # 3. Persistencia atómica: SQLite + fichero JSON
                self.db.save_snapshot(snapshot)
                out_dir = os.path.dirname(self.output_path)
                if out_dir:
                    os.makedirs(out_dir, exist_ok=True)
                tmp_path = f"{self.output_path}.tmp"
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(snapshot, f, ensure_ascii=False, indent=2)
                os.replace(tmp_path, self.output_path)

                result["success"] = True
                result["updated"] = True
                self.last_check = snap_iso

                gh = merged.get("github") or {}
                if gh.get("status") == "ok" and gh.get("items"):
                    try:
                        self.db.save_repo_stars(gh["items"])
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("No se pudo guardar historial de estrellas: %s", exc)

                # 4. Detección de cambios (diff local determinista; 0 tokens)
                events = detect_changes(old, snapshot, star_baselines=star_baselines)
                baseline = is_legacy_snapshot(old)
                skip_broadcast = bool(cfg.SKIP_TELEGRAM_BROADCAST)

                if events:
                    summary = change_summary(events)
                    already_sent = self._is_recent_duplicate_change(summary)
                    if not already_sent:
                        self.db.save_change(summary)
                    self.last_change = snap_iso
                    RUNTIME_STATE["last_change"] = snap_iso
                    result["events_count"] = len(events)
                    result["events_preview"] = render_alert(events)

                    if force:
                        result["message"] = f"Actualización forzada completada ({len(events)} cambio(s) registrados)"
                    elif skip_broadcast or already_sent:
                        reason = "modo respaldo silencioso" if skip_broadcast else "cambio ya notificado recientemente"
                        result["message"] = f"Cambios detectados ({len(events)}), broadcast omitido ({reason})"
                        logger.info("ℹ️ %s", result["message"])
                    else:
                        result["changes_detected"] = True
                        result["comparison_result"] = summary
                        if ADMIN_CHAT_ID:
                            self.db.ensure_subscriber(ADMIN_CHAT_ID)
                        alert = await self._compose_alert(events, summary)
                        delivered = await self._broadcast(alert, events=events)
                        RUNTIME_STATE["last_broadcast_count"] = delivered
                        result["message"] = f"Cambios detectados ({len(events)} eventos), enviado a {delivered} destino(s)"
                        logger.info("📣 %s", result["message"])
                elif force:
                    result["message"] = "Actualización forzada completada"
                elif stale:
                    result["message"] = f"Datos actualizados con {len(stale)} fuente(s) en stale: {', '.join(stale)}"
                elif baseline:
                    result["message"] = "Línea base estructurada establecida (primer snapshot)"
                else:
                    result["message"] = "Sin cambios detectados"
                    logger.info("✅ Sin cambios significativos.")

                RUNTIME_STATE["last_update_check"] = snap_iso
                RUNTIME_STATE["last_update_message"] = result["message"]

                self.db.cleanup_old_data()
                return result

            except Exception as e:
                error_msg = f"Error en actualización: {str(e)}"
                logger.error(error_msg, exc_info=True)
                result["message"] = error_msg
                result["error"] = str(e)
                RUNTIME_STATE["last_update_check"] = utc_now_iso()
                RUNTIME_STATE["last_update_message"] = error_msg[:200]
                if not cfg.SKIP_TELEGRAM_BROADCAST:
                    await self._notify_admin(
                        f"⚠️ <b>Error en actualización automática</b>\n\n<code>{escape_html(error_msg[:500])}</code>",
                        is_error=True,
                    )
                return result

    # ------------------------------------------------------------- notificaciones

    async def _compose_alert(self, events: list[dict], summary: str) -> str:
        """Plantilla determinista; el LLM solo pule el boletín si está disponible."""
        template = render_alert(events)
        try:
            draft = await asyncio.to_thread(self.brain.draft_newsletter, template, summary)
            if draft and len(draft.strip()) > 50:
                return draft
        except Exception as exc:  # noqa: BLE001
            logger.warning("No se pudo pulir el boletín con LLM: %s", exc)
        return template

    async def _broadcast(self, text: str, events: list[dict] | None = None) -> int:
        """Envía alertas al canal público (si existe) y a los suscriptores según sus preferencias."""
        if not TELEGRAM_TOKEN or not text:
            logger.warning("Broadcast omitido: falta TELEGRAM_TOKEN o mensaje")
            return 0

        delivered = 0
        channel_ok = False

        if PUBLIC_CHANNEL_ID:
            try:
                await self._send_to(PUBLIC_CHANNEL_ID, text)
                delivered += 1
                channel_ok = True
            except Exception as exc:  # noqa: BLE001
                logger.error("No se pudo publicar en el canal público %s: %s", PUBLIC_CHANNEL_ID, exc)

        subs = [s for s in self.db.list_subscribers_with_prefs() if s["chat_id"] and s["chat_id"] != "None"]
        if not subs and (not PUBLIC_CHANNEL_ID or not channel_ok) and ADMIN_CHAT_ID:
            subs = [{"chat_id": str(ADMIN_CHAT_ID), "preferences": {"alerts": "all"}}]

        if not subs and not channel_ok:
            logger.warning("Broadcast omitido: sin suscriptores ni canal público operativo")
            return 0

        for idx, sub in enumerate(subs):
            chat_id = sub["chat_id"]
            pref = (sub.get("preferences") or {}).get("alerts", "all")
            if pref == "none":
                continue

            msg_to_send = text
            if events is not None and pref != "all":
                subset = filter_events_for_preference(events, pref)
                if not subset:
                    continue
                msg_to_send = render_alert(subset)

            try:
                await self._send_to(chat_id, msg_to_send)
                delivered += 1
            except Exception as exc:  # noqa: BLE001
                err_lower = str(exc).lower()
                if any(tok in err_lower for tok in ("forbidden", "bot was blocked", "chat not found", "deactivated")):
                    logger.info("Suscriptor %s bloqueó o cerró el chat; desactivando suscripción.", chat_id)
                    self.db.remove_subscriber(chat_id)
                else:
                    logger.error("No se pudo enviar a %s: %s", chat_id, exc)

            if idx + 1 < len(subs):
                await asyncio.sleep(0.05)

        return delivered

    async def _send_to(self, chat_id: str, text: str) -> None:
        target: int | str = int(chat_id) if str(chat_id).lstrip("-").isdigit() else str(chat_id)
        chunks = split_html_message(text, limit=4000)
        for chunk in chunks:
            if self.bot:
                try:
                    await self.bot.send_message(chat_id=target, text=chunk, parse_mode="HTML")
                except Exception as exc:  # noqa: BLE001
                    err_lower = str(exc).lower()
                    if any(tok in err_lower for tok in ("forbidden", "bot was blocked", "chat not found", "deactivated")):
                        raise
                    logger.warning("Fallo envío HTML a %s (%s); reintentando plano.", chat_id, exc)
                    await self.bot.send_message(chat_id=target, text=strip_html(chunk))
            else:
                url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
                async with httpx.AsyncClient(timeout=12.0) as client:
                    resp = await client.post(
                        url,
                        json={"chat_id": target, "text": chunk, "parse_mode": "HTML"},
                    )
                    if resp.status_code == 400:
                        logger.warning("Telegram rechazó HTML para %s; reintentando plano.", chat_id)
                        resp = await client.post(
                            url,
                            json={"chat_id": target, "text": strip_html(chunk)},
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
            "last_check": self.last_check,
            "last_change": self.last_change,
            "data_exists": os.path.exists(self.output_path),
            "output_path": self.output_path,
            "subscribers": len(self.db.list_subscribers()),
        }


async def update_data(bot=None, force: bool = False) -> dict:
    """Función standalone (GitHub Actions / manual): python -m src.update_data."""
    updater = DataUpdater(bot=bot)
    return await updater.check_and_update(force=force)
