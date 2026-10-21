"""Punto de entrada: bot Telegram (polling resiliente) + servidor Flask keep-alive.

v3:
- Un solo RankingBrain compartido; llamadas a Gemini vía ``asyncio.to_thread``
  (antes bloqueaban el event loop10-30 s y congelaban el bot).
- Mensajes en HTML con splitting >4096 (antes Markdown legacy se rompía con `_`).
- Multiusuario: /start y /subscribe registran suscriptores; alertas broadcast;
  rate limit por usuario y presupuesto diario global de Gemini.
- Briefing diario /hoy (rankings + tendencias GitHub/HN) a la hora DAILY_BRIEF_HOUR.
"""

import asyncio
import html
import json
import logging
import os
import re
import threading
import traceback
from collections import defaultdict, deque
from datetime import datetime
from datetime import time as dtime

import httpx
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from src.brain import budget_remaining, build_context, get_brain, snapshot_age_hours, snapshot_briefing_parts
from src.config import ADMIN_CHAT_ID, DAILY_BRIEF_HOUR, DATA_JSON_URL, TELEGRAM_TOKEN, USER_RATE_LIMIT
from src.db import Database
from src.keepalive import run_flask, self_ping
from src.trending import render_trending_brief
from src.updater import DataUpdater

# --- Configuración de Logging ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

brain = get_brain()
db = Database()

# Instancia global del updater (lazy: se construye con el bot para notificar)
updater: DataUpdater | None = None


def get_updater(bot=None) -> DataUpdater:
    """Obtiene la instancia única de DataUpdater."""
    global updater
    if updater is None:
        updater = DataUpdater(bot=bot)
    elif bot is not None:
        updater.bot = bot
    return updater


# --------------------------------------------------------------- rate limiting

_user_messages: dict[int, deque] = defaultdict(deque)


def allow_user_message(user_id: int) -> bool:
    """Ventana deslizante de1 hora por usuario (USER_RATE_LIMIT consultas)."""
    now = datetime.now()
    window = _user_messages[user_id]
    while window and (now - window[0]).total_seconds() > 3600:
        window.popleft()
    if len(window) >= USER_RATE_LIMIT:
        return False
    window.append(now)
    return True


# ------------------------------------------------------------- envío de mensajes

def split_message(text: str, limit: int = 4000) -> list[str]:
    """Parte un mensaje en trozos < limit respetando saltos de párrafo."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ""
    for paragraph in text.split("\n\n"):
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
        while len(paragraph) > limit:  # párrafo gigante: corte duro
            chunks.append(paragraph[:limit])
            paragraph = paragraph[limit:]
        current = paragraph
    if current:
        chunks.append(current)
    return chunks or [text[:limit]]


def _strip_html(text: str) -> str:
    """Fallback plano si Telegram rechaza el HTML (etiquetas mal formadas)."""
    return html.unescape(re.sub(r"<[^>]+>", "", text))


async def send_html(bot, chat_id: int, text: str) -> None:
    """Envía en HTML con fallback a texto plano por chunk."""
    for chunk in split_message(text):
        try:
            await bot.send_message(chat_id=chat_id, text=chunk, parse_mode="HTML")
        except Exception as exc:  # noqa: BLE001 - HTML inválido → texto plano
            logger.warning("Fallo envío HTML (%s); reintentando plano.", exc)
            try:
                await bot.send_message(chat_id=chat_id, text=_strip_html(chunk))
            except Exception as exc2:  # noqa: BLE001
                logger.error("Fallo envío a %s: %s", chat_id, exc2)


# ------------------------------------------------------------------- snapshot

async def load_snapshot() -> dict | None:
    """Carga el snapshot estructurado: fichero local → respaldo GitHub Raw → SQLite."""
    path = os.path.join("data", "rankings.json")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Snapshot local ilegible: %s", exc)

    if DATA_JSON_URL:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(DATA_JSON_URL, timeout=10)
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, dict):
                        logger.info("Snapshot cargado desde GitHub Raw.")
                        return data
        except Exception as exc:  # noqa: BLE001
            logger.warning("No se pudo cargar desde GitHub Raw: %s", exc)

    snap = db.get_latest_snapshot()
    if isinstance(snap, dict) and isinstance(snap.get("data"), dict):
        return snap["data"]
    return None


# ------------------------------------------------------------------ handlers

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Bienvenida + alta como suscriptor (multiusuario)."""
    chat_id = str(update.effective_chat.id)
    db.add_subscriber(chat_id)
    await update.message.reply_text(
        "¡Hola! Soy tu <b>Ranking AI Agent</b> 🏆.\n\n"
        "🔥 <b>Detección en tiempo real:</b> mis datos se actualizan cada30 minutos.\n"
        "📊 Rastreo: LMSYS Arena (8 categorías), OpenRouter, Design Arena, GitHub y HN.\n"
        "🚨 Te avisaré <b>automáticamente</b> si cambia el top de los rankings.\n"
        "🐙 Cada mañana recibirás el briefing <b>/hoy</b> con tendencias de GitHub.\n\n"
        "<b>Comandos:</b>\n"
        "• /subscribe — recibir alertas y el briefing diario\n"
        "• /unsubscribe — darte de baja\n"
        "• /hoy — briefing de hoy ahora mismo\n"
        "• /status — estado del sistema\n"
        "• /forceupdate — forzar actualización de datos\n\n"
        "Pregúntame lo que quieras sobre el estado del arte de la IA.",
        parse_mode="HTML",
    )


async def subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db.add_subscriber(str(update.effective_chat.id))
    await update.message.reply_text(
        "✅ ¡Suscrito! Recibirás las alertas de cambios en los rankings y el briefing diario.\n"
        "Usa /unsubscribe cuando quieras darte de baja.",
        parse_mode="HTML",
    )


async def unsubscribe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db.remove_subscriber(str(update.effective_chat.id))
    await update.message.reply_text(
        "👋 Baja realizada. Ya no recibirás alertas. Vuelve con /subscribe cuando quieras.",
        parse_mode="HTML",
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Chequeo de salud del sistema."""
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    snapshot = await load_snapshot()
    age = snapshot_age_hours(snapshot)
    if snapshot:
        data_status = f"✅ Snapshot hace {age:.1f} h" if age is not None else "✅ Snapshot disponible"
        stale = [
            k for k, v in (snapshot.get("sources") or {}).items()
            if isinstance(v, dict) and v.get("status") == "stale"
        ]
        if stale:
            data_status += f"\n⚠️ Fuentes stale: {', '.join(stale)}"
    else:
        data_status = "⚠️ Sin datos todavía"

    brain_ok, brain_msg = await asyncio.to_thread(brain.check_health)
    brain_status = "✅ Gemini conectado" if brain_ok else f"❌ {brain_msg}"

    await update.message.reply_text(
        f"📊 <b>Estado del Sistema</b>\n\n"
        f"<b>Base de datos:</b> {data_status}\n"
        f"<b>IA Brain:</b> {brain_status}\n"
        f"<b>cuota Gemini hoy:</b> {budget_remaining()} llamadas restantes\n"
        f"<b>Suscriptores:</b> {len(db.list_subscribers())}\n"
        f"<b>Versión:</b> 3.0 (estructurada + tendencias)\n"
        f"<b>Uptime desde:</b> {context.bot_data.get('start_time', 'N/A')}",
        parse_mode="HTML",
    )


async def force_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Fuerza una actualización manual de los datos."""
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    await update.message.reply_text("🔄 Forzando actualización de datos...")

    try:
        result = await get_updater(context.bot).check_and_update(force=True)
        if result["success"]:
            await update.message.reply_text(
                f"✅ <b>Actualización completada</b>\n\n"
                f"📊 Fuentes consultadas: {', '.join(result.get('sources_scraped', []))}\n"
                f"📝 {html.escape(result.get('message', 'Datos actualizados correctamente'))}",
                parse_mode="HTML",
            )
        else:
            await update.message.reply_text(
                f"❌ <b>Error en actualización</b>\n\n{html.escape(result.get('message', 'Error desconocido'))}",
                parse_mode="HTML",
            )
    except Exception as e:
        logger.error(f"Error en force_update: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Error: {html.escape(str(e))}", parse_mode="HTML")


# ------------------------------------------------------------------- /hoy

def _rankings_digest(snapshot: dict | None) -> str:
    """Resumen breve de rankings para el briefing: últimos cambios + top Text."""
    parts: list[str] = []

    changes = db.get_changes(days=1)
    if changes:
        parts.append("🚨 <b>Cambios detectados (últimas24 h)</b>")
        for c in changes[:3]:
            parts.append(f"• {html.escape(c['summary'][:400])}")

    if isinstance(snapshot, dict):
        arena = (snapshot.get("sources") or {}).get("arena") or {}
        top = [
            m for m in (arena.get("models") or [])
            if m.get("category") == "Text" and m.get("rank", 99) <= 5
        ]
        top.sort(key=lambda m: m.get("rank", 99))
        if top:
            parts.append("🏆 <b>Top Arena (Text)</b>")
            for m in top:
                parts.append(f"  {m['rank']}. {html.escape(m['model'])} — {m.get('score')} elo")

    if not parts:
        return "<i>Aún no hay cambios registrados hoy.</i>"
    return "\n".join(parts)


async def build_hoy_text() -> str:
    """Compone el briefing /hoy: rankings + tendencias, pulido con Gemini si hay cuota."""
    snapshot = await load_snapshot()
    rankings = _rankings_digest(snapshot)

    sources = (snapshot or {}).get("sources") or {}
    trending = render_trending_brief(sources.get("github") or {}, sources.get("hackernews") or {})

    # Gemini pule el briefing; si no hay cuota o falla, se envía la plantilla.
    try:
        _, trending_plain = snapshot_briefing_parts(snapshot)
        draft = await asyncio.to_thread(brain.draft_briefing, _strip_html(rankings), trending_plain)
        if draft:
            return draft
    except Exception as exc:  # noqa: BLE001 - el pulido es opcional
        logger.warning("Briefing sin pulir (%s)", exc)

    age = snapshot_age_hours(snapshot)
    freshness = f" (datos de hace {age:.1f} h)" if age is not None else ""
    return f"☀️ <b>Briefing de hoy</b>{freshness}\n\n{rankings}\n\n{trending}"


async def hoy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Comando: briefing inmediato de rankings + tendencias GitHub/HN."""
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    try:
        text = await build_hoy_text()
    except Exception as exc:  # noqa: BLE001
        logger.error("Error construyendo /hoy: %s", exc, exc_info=True)
        text = "⚠️ No pude construir el briefing ahora mismo. Inténtalo en unos minutos."
    await send_html(context.bot, update.effective_chat.id, text)


async def daily_briefing(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Job diario: envía el briefing a todos los suscriptores."""
    logger.info("☀️ [Job] Enviando briefing diario...")
    try:
        text = await build_hoy_text()
    except Exception as exc:  # noqa: BLE001
        logger.error("Briefing diario falló: %s", exc, exc_info=True)
        return

    chat_ids = db.list_subscribers() or ([str(ADMIN_CHAT_ID)] if ADMIN_CHAT_ID else [])
    for chat_id in chat_ids:
        try:
            await send_html(context.bot, int(chat_id), text)
        except Exception as exc:  # noqa: BLE001
            logger.error("No se pudo enviar el briefing a %s: %s", chat_id, exc)


# ------------------------------------------------------------------ chat libre

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Preguntas en lenguaje natural: rate limit → presupuesto → Gemini (to_thread)."""
    user_id = update.effective_user.id if update.effective_user else update.effective_chat.id

    if not allow_user_message(user_id):
        await update.message.reply_text(
            f"⏳ Límite de {USER_RATE_LIMIT} consultas por hora alcanzado. "
            "Prueba de nuevo más tarde o usa /hoy para el resumen del día."
        )
        return

    if budget_remaining() <= 0:
        await update.message.reply_text(
            "⏳ Se agotó la cuota diaria de IA del bot. Vuelve mañana."
        )
        return

    user_text = update.message.text
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        snapshot = await load_snapshot()
        context_data = build_context(snapshot)

        response = await asyncio.to_thread(brain.generate_response, user_text, context_data)
        if not response:
            response = "Lo siento, no pude generar una respuesta en este momento. Intenta preguntar de otra forma."

        await send_html(context.bot, update.effective_chat.id, response)

    except Exception as e:
        logger.error(f"Error en chat: {e}", exc_info=True)
        await update.message.reply_text(
            f"Tuve un pequeño cortocircuito técnico. [Error: {html.escape(str(e)[:100])}]",
            parse_mode="HTML",
        )


# ------------------------------------------------------------- error handler

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Captura errores del bot, los loguea y notifica al admin en HTML."""
    logger.error("Excepción durante procesamiento de update:", exc_info=context.error)

    tb_string = "".join(
        traceback.format_exception(None, context.error, context.error.__traceback__)
    )
    update_str = update.to_dict() if isinstance(update, Update) else str(update)
    message = (
        f"🚨 <b>Error en Ranking AI Bot</b>\n\n"
        f"<pre>{html.escape(json.dumps(update_str, indent=2, ensure_ascii=False)[:1000])}</pre>\n\n"
        f"<pre>{html.escape(tb_string[-2000:])}</pre>"
    )

    if ADMIN_CHAT_ID:
        try:
            await context.bot.send_message(
                chat_id=int(ADMIN_CHAT_ID),
                text=message[:4096],
                parse_mode="HTML",
            )
        except Exception as notify_err:
            logger.error(f"No se pudo notificar al admin: {notify_err}")


# ------------------------------------------------------------------- jobs

async def heartbeat(context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info("💓 Heartbeat — Bot activo a las %s", datetime.now().isoformat())


async def periodic_update(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Job principal: actualiza rankings + tendencias cada30 minutos."""
    logger.info("🔄 [Job] Iniciando actualización periódica...")
    try:
        result = await get_updater(context.bot).check_and_update(force=False)
        if result["changes_detected"]:
            logger.info("📣 [Job] Cambios detectados y notificados")
        else:
            logger.info("✅ [Job] %s", result.get("message", "Verificación completada"))
    except Exception as e:
        logger.error(f"❌ [Job] Error en actualización periódica: {e}", exc_info=True)


async def post_init(application) -> None:
    """Registra los jobs periódicos tras inicializar el bot."""
    application.bot_data["start_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    logger.info("🚀 Bot inicializado. Registrando jobs periódicos...")

    application.job_queue.run_repeating(self_ping, interval=300, first=30, name="self_ping")
    application.job_queue.run_repeating(heartbeat, interval=1800, first=60, name="heartbeat")
    application.job_queue.run_repeating(periodic_update, interval=1800, first=120, name="periodic_update")
    application.job_queue.run_daily(
        daily_briefing,
        time=dtime(hour=DAILY_BRIEF_HOUR, minute=0),
        name="daily_briefing",
    )

    logger.info(
        "✅ Jobs: self_ping (5 min), heartbeat (30 min), periodic_update (30 min), "
        f"daily_briefing ({DAILY_BRIEF_HOUR}:00)"
    )


# ---------------------------------------------------------------- entry point

def ensure_chromium() -> None:
    """Instala Chromium de Playwright si falta (Render solo instala el paquete pip).

    Es idempotente: si el navegador ya está descargado, Playwright sale al instante.
    Un fallo aquí no debe impedir que el bot arranque: los scrapers caerán a error
    y el safety net conservará los datos anteriores.
    """
    try:
        import subprocess
        import sys

        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium", "chromium-headless-shell"],
            check=True,
            timeout=600,
            capture_output=True,
        )
        logger.info("🌐 Chromium de Playwright disponible")
    except Exception as exc:  # noqa: BLE001 - el bot debe arrancar aunque falle
        logger.error("❌ No se pudo preparar Chromium para Playwright: %s", exc)


if __name__ == "__main__":
    os.makedirs("data", exist_ok=True)
    ensure_chromium()

    flask_thread = threading.Thread(target=run_flask, name="FlaskKeepAlive")
    flask_thread.daemon = True
    flask_thread.start()
    logger.info("🌐 Servidor Flask (keep-alive + dashboard) iniciado en hilo separado")

    if not TELEGRAM_TOKEN:
        logger.error("❌ No se encontró TELEGRAM_TOKEN en las variables de entorno.")
        flask_thread.join()
    else:
        application = (
            ApplicationBuilder()
            .token(TELEGRAM_TOKEN)
            .connect_timeout(30.0)
            .read_timeout(30.0)
            .write_timeout(30.0)
            .pool_timeout(30.0)
            .connection_pool_size(8)
            .post_init(post_init)
            .build()
        )

        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("subscribe", subscribe))
        application.add_handler(CommandHandler("unsubscribe", unsubscribe))
        application.add_handler(CommandHandler("status", status))
        application.add_handler(CommandHandler("forceupdate", force_update))
        application.add_handler(CommandHandler("hoy", hoy))
        application.add_handler(
            MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message)
        )
        application.add_error_handler(error_handler)

        logger.info("🤖 Bot y Servidor Web iniciados. Polling resiliente activado...")

        application.run_polling(
            drop_pending_updates=True,
            poll_interval=1.0,
            timeout=30,
            allowed_updates=Update.ALL_TYPES,
        )
