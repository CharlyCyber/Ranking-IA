"""Punto de entrada: Agente Telegram v4 + servidor Flask (Dashboard + API pública).

Mejoras clave v4:
- Seguridad: /forceupdate exclusivo del administrador + sanitización HTML estricta.
- Eficiencia de cuota: /hoy cacheado por snapshot (1 llamada LLM sirve a miles de lectores).
- UX a 0 tokens: menús interactivos con botones Inline, buscador /buscar, gráficas /chart
  y preferencias de alertas por categoría (/alertas).
- Crecimiento orgánico: Modo Inline (@bot <modelo>) para compartir rankings en cualquier grupo
  a coste cero + soporte de Telegram Mini App y Canal Público.
"""

import asyncio
import json
import logging
import os
import threading
import traceback
from collections import defaultdict, deque
from datetime import datetime, timezone
from datetime import time as dtime
from uuid import uuid4

import httpx
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultArticle,
    InputTextMessageContent,
    Update,
    WebAppInfo,
)
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    InlineQueryHandler,
    MessageHandler,
    filters,
)

from src.brain import (
    budget_remaining,
    build_context,
    get_brain,
    snapshot_age_hours,
    snapshot_briefing_parts,
)
from src.config import (
    ADMIN_CHAT_ID,
    DAILY_BRIEF_HOUR,
    DATA_JSON_URL,
    PUBLIC_CHANNEL_ID,
    PUBLIC_URL,
    TELEGRAM_TOKEN,
    UPDATE_INTERVAL_MINUTES,
    USER_RATE_LIMIT,
)
from src.db import Database, utc_now_iso
from src.html_utils import (
    escape_html,
    sanitize_telegram_html,
    split_html_message,
    strip_html,
)
from src.insights import (
    build_quickchart_url,
    render_category_card,
    render_model_card_html,
    search_models,
)
from src.keepalive import RUNTIME_STATE, run_flask, self_ping
from src.trending import render_trending_brief
from src.updater import DataUpdater

# --- Configuración de Logging ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

brain = get_brain()
db = Database()

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
    """Ventana deslizante de 1 hora por usuario (USER_RATE_LIMIT consultas de chat IA)."""
    now = datetime.now()
    window = _user_messages[user_id]
    while window and (now - window[0]).total_seconds() > 3600:
        window.popleft()
    if len(window) >= USER_RATE_LIMIT:
        return False
    window.append(now)
    return True


def is_admin(chat_id: int | str | None) -> bool:
    """Comprueba si el chat corresponde al administrador configurado."""
    if not ADMIN_CHAT_ID or chat_id is None:
        return False
    return str(chat_id).strip() == str(ADMIN_CHAT_ID).strip()


# ------------------------------------------------------------- envío de mensajes


def split_message(text: str, limit: int = 4000) -> list[str]:
    """Parte un mensaje en trozos <= limit respetando párrafos y etiquetas HTML."""
    return split_html_message(text, limit=limit)


def _strip_html(text: str) -> str:
    """Fallback plano si Telegram rechaza el HTML."""
    return strip_html(text)


async def send_html(bot, chat_id: int | str, text: str, reply_markup=None) -> None:
    """Envía en HTML sanitizado con fallback a texto plano por chunk."""
    chunks = split_message(text)
    for idx, chunk in enumerate(chunks):
        markup = reply_markup if idx == len(chunks) - 1 else None
        try:
            await bot.send_message(chat_id=chat_id, text=chunk, parse_mode="HTML", reply_markup=markup)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Fallo envío HTML (%s); reintentando plano.", exc)
            try:
                await bot.send_message(chat_id=chat_id, text=_strip_html(chunk), reply_markup=markup)
            except Exception as exc2:  # noqa: BLE001
                logger.error("Fallo envío a %s: %s", chat_id, exc2)


# ------------------------------------------------------------------- snapshot


async def load_snapshot() -> dict | None:
    """Carga el snapshot estructurado unificado: SQLite → fichero local → respaldo GitHub Raw."""
    snap = db.get_or_seed_latest_snapshot()
    if isinstance(snap, dict) and isinstance(snap.get("data"), dict):
        return snap["data"]

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
                        logger.info("Snapshot cargado desde respaldo remoto.")
                        return data
        except Exception as exc:  # noqa: BLE001
            logger.warning("No se pudo cargar desde respaldo remoto: %s", exc)

    return None


# ------------------------------------------------------------- teclados inline


def build_main_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("🏆 Top General", callback_data="view:general"),
            InlineKeyboardButton("💻 Top Coding", callback_data="view:coding"),
        ],
        [
            InlineKeyboardButton("🎬 Multimodal", callback_data="view:multimodal"),
            InlineKeyboardButton("⚡ Uso OpenRouter", callback_data="view:openrouter"),
        ],
        [
            InlineKeyboardButton("🆓 Gratis & Precio", callback_data="view:free"),
            InlineKeyboardButton("☀️ Briefing /hoy", callback_data="view:hoy"),
        ],
        [
            InlineKeyboardButton("📈 Gráfico Top", callback_data="view:chart"),
            InlineKeyboardButton("🔔 Mis Alertas", callback_data="prefs:open"),
        ],
    ]
    if PUBLIC_URL.startswith("https://"):
        rows.append(
            [InlineKeyboardButton("📱 Abrir Dashboard Interactivo", web_app=WebAppInfo(url=f"{PUBLIC_URL}/dashboard"))]
        )
    return InlineKeyboardMarkup(rows)


def build_prefs_keyboard(prefs: dict) -> InlineKeyboardMarkup:
    cur = prefs.get("alerts", "all")
    brief_on = bool(prefs.get("daily_brief", True))

    def mark(val: str, label: str) -> str:
        return f"✅ {label}" if cur == val else label

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(mark("all", "Todas las alertas"), callback_data="prefs:alerts:all"),
                InlineKeyboardButton(mark("top1", "Solo Nuevo #1"), callback_data="prefs:alerts:top1"),
            ],
            [
                InlineKeyboardButton(mark("coding", "Solo Coding/UI"), callback_data="prefs:alerts:coding"),
                InlineKeyboardButton(mark("none", "Silenciar alertas"), callback_data="prefs:alerts:none"),
            ],
            [
                InlineKeyboardButton(
                    f"{'✅' if brief_on else '⬜'} Briefing matutino /hoy",
                    callback_data="prefs:toggle_brief",
                )
            ],
            [InlineKeyboardButton("⬅️ Volver al menú", callback_data="view:menu")],
        ]
    )


# ------------------------------------------------------------------ handlers


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Bienvenida + alta como suscriptor + panel interactivo a 0 tokens."""
    chat_id = str(update.effective_chat.id)
    db.add_subscriber(chat_id)
    channel_line = f"📢 Canal público rápido: <b>{escape_html(PUBLIC_CHANNEL_ID)}</b>\n" if PUBLIC_CHANNEL_ID else ""
    await update.message.reply_text(
        "¡Hola! Soy <b>Ranking AI Agent</b> 🏆 (100% gratuito y open-source).\n\n"
        "📊 <b>Fuentes en tiempo real:</b> LMSYS Arena, OpenRouter, Design Arena, GitHub AI, Hugging Face y HN.\n"
        "⚡ <b>Consultas instantáneas (0 cuota):</b> usa los botones de abajo o <code>/buscar claude</code>.\n"
        "🔍 <b>Modo Inline en cualquier grupo:</b> escribe mi @usuario seguido de un modelo.\n"
        f"{channel_line}\n"
        "<b>Comandos principales:</b>\n"
        "• /menu — explorador interactivo de rankings\n"
        "• /hoy — briefing diario de rankings + tendencias\n"
        "• /buscar <i>modelo</i> — ficha rápida de posiciones y precios\n"
        "• /chart — gráfica visual del Top actual\n"
        "• /alertas — personalizar qué avisos recibes\n"
        "• /unsubscribe — darte de baja\n\n"
        "👇 Pulsa una categoría o escríbeme cualquier pregunta en lenguaje natural:",
        parse_mode="HTML",
        reply_markup=build_main_keyboard(),
    )


async def menu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🧭 <b>Explorador Rápido de Rankings IA</b>\nElige qué quieres consultar al instante:",
        parse_mode="HTML",
        reply_markup=build_main_keyboard(),
    )


async def subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    db.add_subscriber(chat_id)
    prefs = db.get_subscriber_prefs(chat_id)
    await update.message.reply_text(
        "✅ <b>¡Suscripción activa!</b>\n"
        "Personaliza abajo qué tipo de alertas prefieres recibir para evitar ruido:",
        parse_mode="HTML",
        reply_markup=build_prefs_keyboard(prefs),
    )


async def alertas_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.effective_chat.id)
    prefs = db.get_subscriber_prefs(chat_id)
    await update.message.reply_text(
        "🔔 <b>Preferencias de Notificaciones</b>\nElige qué avisos automáticos quieres recibir:",
        parse_mode="HTML",
        reply_markup=build_prefs_keyboard(prefs),
    )


async def unsubscribe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db.remove_subscriber(str(update.effective_chat.id))
    await update.message.reply_text(
        "👋 Baja realizada. Ya no recibirás alertas automáticas. Vuelve con /subscribe cuando quieras.",
        parse_mode="HTML",
    )


async def buscar_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Búsqueda instantánea de modelos a 0 tokens."""
    query = " ".join(context.args or []).strip()
    if not query:
        await update.message.reply_text(
            "🔎 Uso: <code>/buscar nombre</code> (ej. <code>/buscar claude</code>, <code>/buscar deepseek</code>).",
            parse_mode="HTML",
        )
        return
    snapshot = await load_snapshot()
    matches = search_models(snapshot, query, limit=4)
    if not matches:
        await update.message.reply_text(
            f"No encontré modelos que coincidan con <b>{escape_html(query[:50])}</b> en el snapshot actual.",
            parse_mode="HTML",
        )
        return
    body = "\n\n──────────────\n\n".join(render_model_card_html(m) for m in matches)
    await send_html(context.bot, update.effective_chat.id, body)


def _build_chart_caption(arena_models: list[dict], generated_at: str | dict | None = None) -> str:
    """Construye un pie de foto completo (<1000 chars) con el Top 8 actual y fecha de corte."""
    if isinstance(generated_at, dict):
        generated_at = generated_at.get("generated_at")
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    lines = ["📈 <b>Top IA General (LMSYS Arena · ELO)</b>"]
    if generated_at:
        lines.append(f"🕒 <i>Corte actualizado: {escape_html(str(generated_at)[:16].replace('T', ' '))} UTC</i>")
    lines.append("")
    text_rows = [m for m in arena_models if m.get("category") in ("Text", None)] or arena_models
    sorted_rows = sorted(text_rows, key=lambda m: m.get("rank", 999))[:8]
    for m in sorted_rows:
        rk = m.get("rank", "?")
        badge = medals.get(rk, f"<b>{rk}.</b>")
        score = m.get("score")
        metric = f" — <code>{score}</code> pts" if score is not None else ""
        lines.append(f"{badge} <b>{escape_html(m.get('model'))}</b>{metric}")
    lines.append("\n💡 <i>Más puntos = mayor calidad votada a ciegas por humanos. Usa /menu para ver Código, Webs y Gratis.</i>")
    return "\n".join(lines)[:1000]


async def chart_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Envía gráfica PNG compartible del Top Arena actual vía QuickChart (gratis)."""
    snapshot = await load_snapshot()
    arena_models = [
        m
        for m in (((snapshot or {}).get("sources") or {}).get("arena") or {}).get("models", [])
        if m.get("category") in ("Text", None)
    ]
    url = build_quickchart_url(arena_models, title="LMSYS Arena · Top IA General (ELO)")
    if not url:
        await update.message.reply_text("⚠️ Aún no hay suficientes puntuaciones ELO para generar la gráfica.")
        return
    caption = _build_chart_caption(arena_models, (snapshot or {}).get("generated_at"))
    try:
        await context.bot.send_photo(
            chat_id=update.effective_chat.id,
            photo=url,
            caption=caption,
            parse_mode="HTML",
            reply_markup=build_main_keyboard(),
        )
    except Exception:  # noqa: BLE001 - fallback a enlace directo si Telegram no puede descargar la imagen
        await update.message.reply_text(
            f"{caption}\n\n🔗 <a href=\"{escape_html(url)}\">Abrir gráfica completa</a>",
            parse_mode="HTML",
            reply_markup=build_main_keyboard(),
        )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Chequeo de salud del sistema (sin quemar cuota de IA)."""
    snapshot = await load_snapshot()
    age = snapshot_age_hours(snapshot)
    if snapshot:
        data_status = f"✅ Snapshot hace {age:.1f} h" if age is not None else "✅ Snapshot disponible"
        stale = [
            k
            for k, v in (snapshot.get("sources") or {}).items()
            if isinstance(v, dict) and v.get("status") == "stale"
        ]
        if stale:
            data_status += f"\n⚠️ Fuentes stale: {', '.join(stale)}"
    else:
        data_status = "⚠️ Sin datos todavía"

    brain_ok, brain_msg = brain.check_health()
    brain_status = f"✅ {escape_html(brain_msg)}" if brain_ok else f"❌ {escape_html(brain_msg)}"

    await update.message.reply_text(
        f"📊 <b>Estado del Sistema</b>\n\n"
        f"<b>Datos:</b> {escape_html(data_status)}\n"
        f"<b>Revisión automática:</b> cada {UPDATE_INTERVAL_MINUTES} min (avisa solo cambios)\n"
        f"<b>IA Brain:</b> {brain_status}\n"
        f"<b>Suscriptores activos:</b> {len(db.list_subscribers())}\n"
        f"<b>Versión:</b> 4.1 (24/7 Always-On)\n"
        f"<b>Uptime desde:</b> {escape_html(context.bot_data.get('start_time', 'N/A'))}",
        parse_mode="HTML",
    )


async def force_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Fuerza una actualización manual (restringido exclusivamente al administrador)."""
    if not is_admin(update.effective_chat.id):
        await update.message.reply_text(
            "🔒 El comando <code>/forceupdate</code> está reservado al administrador.\n"
            f"Los datos se revisan solos cada {UPDATE_INTERVAL_MINUTES} min; usa /menu o /hoy para ver el último corte.",
            parse_mode="HTML",
        )
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    await update.message.reply_text("🔄 Ejecutando actualización manual de fuentes...")

    try:
        result = await get_updater(context.bot).check_and_update(force=True)
        if result["success"]:
            await update.message.reply_text(
                f"✅ <b>Actualización completada</b>\n\n"
                f"📊 Fuentes consultadas: {escape_html(', '.join(result.get('sources_scraped', [])))}\n"
                f"📝 {escape_html(result.get('message', 'Datos actualizados correctamente'))}",
                parse_mode="HTML",
            )
        else:
            await update.message.reply_text(
                f"⚠️ <b>Aviso en actualización</b>\n\n{escape_html(result.get('message', 'Error desconocido'))}",
                parse_mode="HTML",
            )
    except Exception as e:
        logger.error("Error en force_update: %s", e, exc_info=True)
        await update.message.reply_text(f"❌ Error: {escape_html(str(e)[:200])}", parse_mode="HTML")


# ------------------------------------------------------------------- /hoy (con caché)


def _rankings_digest(snapshot: dict | None) -> str:
    """Resumen completo y claro de rankings para el briefing: cambios + General + Código/Web."""
    parts: list[str] = []

    changes = db.get_changes(days=1)
    if changes:
        parts.append("🚨 <b>Cambios detectados (últimas 24 h)</b>")
        for c in changes[:3]:
            parts.append(f"• {escape_html(c['summary'][:400])}")

    if isinstance(snapshot, dict):
        arena = (snapshot.get("sources") or {}).get("arena") or {}
        arena_models = arena.get("models") or []
        top_text = sorted(
            [m for m in arena_models if m.get("category") in ("Text", None) and m.get("rank", 99) <= 6],
            key=lambda m: m.get("rank", 99),
        )
        if top_text:
            parts.append("🏆 <b>Top IA General (LMSYS Arena · ELO)</b>")
            for m in top_text:
                parts.append(f"  {m['rank']}. <b>{escape_html(m['model'])}</b> — <code>{m.get('score')}</code> elo")

        top_code = sorted(
            [m for m in arena_models if m.get("category") == "Code" and m.get("rank", 99) <= 4],
            key=lambda m: m.get("rank", 99),
        )
        if top_code:
            parts.append("\n💻 <b>Top Programación (Arena · Code)</b>")
            for m in top_code:
                parts.append(f"  {m['rank']}. <b>{escape_html(m['model'])}</b> — <code>{m.get('score')}</code> elo")

        top_web = sorted(
            [m for m in arena_models if m.get("category") == "WebDev" and m.get("rank", 99) <= 3],
            key=lambda m: m.get("rank", 99),
        )
        if top_web:
            parts.append("\n🌐 <b>Top Desarrollo Web (Arena · WebDev)</b>")
            for m in top_web:
                parts.append(f"  {m['rank']}. <b>{escape_html(m['model'])}</b> — <code>{m.get('score')}</code> elo")

    if not parts:
        return "<i>Aún no hay cambios registrados hoy.</i>"
    return "\n".join(parts)


async def build_hoy_text() -> str:
    """Compone el briefing /hoy y lo cachea por snapshot para no gastar cuota repetida."""
    snapshot = await load_snapshot()
    cache_key = (
        f"hoy:v4.3:{snapshot.get('generated_at')}"
        if isinstance(snapshot, dict) and snapshot.get("generated_at")
        else ""
    )
    if cache_key:
        cached = db.get_briefing_cache(cache_key)
        if cached:
            return cached

    rankings = _rankings_digest(snapshot)
    sources = (snapshot or {}).get("sources") or {}
    trending = render_trending_brief(
        sources.get("github") or {},
        sources.get("hackernews") or {},
        sources.get("huggingface"),
    )

    try:
        _, trending_plain = snapshot_briefing_parts(snapshot)
        draft = await asyncio.to_thread(brain.draft_briefing, _strip_html(rankings), trending_plain)
        if draft:
            if cache_key:
                db.save_briefing_cache(cache_key, draft)
            return draft
    except Exception as exc:  # noqa: BLE001
        logger.warning("Briefing sin pulir (%s)", exc)

    age = snapshot_age_hours(snapshot)
    freshness = f" (datos hace {age:.1f} h)" if age is not None else ""
    fallback = f"☀️ <b>Briefing de hoy</b>{freshness}\n\n{rankings}\n\n{trending}"
    if cache_key:
        db.save_briefing_cache(cache_key, fallback)
    return fallback


async def hoy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Comando: briefing inmediato cacheado de rankings + tendencias."""
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    try:
        text = await build_hoy_text()
    except Exception as exc:  # noqa: BLE001
        logger.error("Error construyendo /hoy: %s", exc, exc_info=True)
        text = "⚠️ No pude construir el briefing ahora mismo. Inténtalo en unos minutos."
    await send_html(context.bot, update.effective_chat.id, text, reply_markup=build_main_keyboard())


async def daily_briefing(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Job diario: envía el briefing al canal público y a suscriptores que lo tengan activo."""
    logger.info("☀️ [Job] Enviando briefing diario...")
    try:
        text = await build_hoy_text()
    except Exception as exc:  # noqa: BLE001
        logger.error("Briefing diario falló: %s", exc, exc_info=True)
        return

    if PUBLIC_CHANNEL_ID:
        try:
            await send_html(context.bot, PUBLIC_CHANNEL_ID, text)
        except Exception as exc:  # noqa: BLE001
            logger.error("No se pudo enviar briefing al canal público %s: %s", PUBLIC_CHANNEL_ID, exc)

    subs = db.list_subscribers_with_prefs()
    if not subs and not PUBLIC_CHANNEL_ID and ADMIN_CHAT_ID:
        subs = [{"chat_id": str(ADMIN_CHAT_ID), "preferences": {"daily_brief": True}}]

    for idx, sub in enumerate(subs):
        if not (sub.get("preferences") or {}).get("daily_brief", True):
            continue
        cid = sub["chat_id"]
        try:
            await send_html(context.bot, int(cid), text)
        except Exception as exc:  # noqa: BLE001
            if any(t in str(exc).lower() for t in ("forbidden", "bot was blocked", "chat not found")):
                db.remove_subscriber(cid)
            else:
                logger.error("No se pudo enviar el briefing a %s: %s", cid, exc)
        if idx + 1 < len(subs):
            await asyncio.sleep(0.05)


# --------------------------------------------------------- callbacks de botones


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Atiende pulsaciones del teclado interactivo (0 llamadas a LLM salvo /hoy no cacheado)."""
    query = update.callback_query
    if not query:
        return
    await query.answer()
    data = query.data or ""
    chat_id = query.message.chat_id if query.message else update.effective_chat.id

    if data == "view:menu":
        await query.edit_message_text(
            "🧭 <b>Explorador Rápido de Rankings IA</b>\nElige una vista:",
            parse_mode="HTML",
            reply_markup=build_main_keyboard(),
        )
        return

    if data == "prefs:open":
        prefs = db.get_subscriber_prefs(chat_id)
        await query.edit_message_text(
            "🔔 <b>Preferencias de Notificaciones</b>\nSelecciona qué alertas deseas recibir:",
            parse_mode="HTML",
            reply_markup=build_prefs_keyboard(prefs),
        )
        return

    if data.startswith("prefs:alerts:"):
        mode = data.split(":", 2)[2]
        if mode in ("all", "top1", "coding", "none"):
            prefs = db.set_subscriber_prefs(chat_id, {"alerts": mode})
            await query.edit_message_text(
                f"✅ Preferencia de alertas actualizada a: <b>{escape_html(mode)}</b>",
                parse_mode="HTML",
                reply_markup=build_prefs_keyboard(prefs),
            )
        return

    if data == "prefs:toggle_brief":
        cur = db.get_subscriber_prefs(chat_id)
        prefs = db.set_subscriber_prefs(chat_id, {"daily_brief": not bool(cur.get("daily_brief", True))})
        await query.edit_message_text(
            "☀️ Preferencia de briefing matutino actualizada:",
            parse_mode="HTML",
            reply_markup=build_prefs_keyboard(prefs),
        )
        return

    if data == "view:hoy":
        text = await build_hoy_text()
        await send_html(context.bot, chat_id, text, reply_markup=build_main_keyboard())
        return

    if data == "view:chart":
        snapshot = await load_snapshot()
        arena_models = [
            m
            for m in (((snapshot or {}).get("sources") or {}).get("arena") or {}).get("models", [])
            if m.get("category") in ("Text", None)
        ]
        url = build_quickchart_url(arena_models, title="LMSYS Arena · Top IA General (ELO)")
        if url:
            caption = _build_chart_caption(arena_models, (snapshot or {}).get("generated_at"))
            try:
                await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=url,
                    caption=caption,
                    parse_mode="HTML",
                    reply_markup=build_main_keyboard(),
                )
                return
            except Exception:  # noqa: BLE001
                await send_html(
                    context.bot,
                    chat_id,
                    f"{caption}\n\n🔗 <a href=\"{escape_html(url)}\">Abrir gráfica completa</a>",
                    reply_markup=build_main_keyboard(),
                )
                return
        await send_html(context.bot, chat_id, "⚠️ Gráfica no disponible en este momento.", reply_markup=build_main_keyboard())
        return

    if data.startswith("view:"):
        view = data.split(":", 1)[1]
        snapshot = await load_snapshot()
        card = render_category_card(snapshot, view)
        await query.edit_message_text(
            sanitize_telegram_html(card),
            parse_mode="HTML",
            reply_markup=build_main_keyboard(),
        )


# --------------------------------------------------------- modo inline (@bot)


async def handle_inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Permite compartir rankings en cualquier chat/grupo escribiendo @bot <modelo> (0 tokens)."""
    if not update.inline_query:
        return
    q = (update.inline_query.query or "").strip()
    snapshot = await load_snapshot()
    articles: list[InlineQueryResultArticle] = []

    if not q:
        presets = [
            ("general", "🏆 Top 10 IA General (LMSYS Arena)", "Clasificación ELO actual de modelos de texto"),
            ("coding", "💻 Top IA para Programación & UI", "Líderes en Arena Coding/WebDev y Design Arena"),
            ("openrouter", "⚡ Top Uso Real en OpenRouter", "Modelos con más tokens procesados esta semana"),
            ("free", "🆓 Mejores Modelos con API Gratis", "Modelos $0 y mejor relación Calidad/Precio"),
        ]
        for key, title, desc in presets:
            html_card = render_category_card(snapshot, key)
            articles.append(
                InlineQueryResultArticle(
                    id=f"preset-{key}",
                    title=title,
                    description=desc,
                    input_message_content=InputTextMessageContent(html_card, parse_mode="HTML"),
                )
            )
    else:
        for item in search_models(snapshot, q, limit=10):
            card = render_model_card_html(item)
            top_app = item["appearances"][0] if item.get("appearances") else {}
            desc = f"Mejor puesto: #{item['best_rank']} ({top_app.get('source', '')})"
            articles.append(
                InlineQueryResultArticle(
                    id=str(uuid4()),
                    title=f"🤖 {item['model']}",
                    description=desc,
                    input_message_content=InputTextMessageContent(card, parse_mode="HTML"),
                )
            )

    await update.inline_query.answer(articles, cache_time=120, is_personal=False)


# ------------------------------------------------------------------ chat libre


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Preguntas abiertas en lenguaje natural: rate limit → presupuesto → Multi-LLM."""
    if not update.message or not update.message.text:
        return
    user_id = update.effective_user.id if update.effective_user else update.effective_chat.id

    if not allow_user_message(user_id):
        await update.message.reply_text(
            f"⏳ Alcanzaste el límite gratuito de {USER_RATE_LIMIT} consultas abiertas por hora.\n"
            "💡 Puedes seguir usando sin límite los botones instantáneos de <b>/menu</b>, <b>/buscar</b> y <b>/hoy</b>.",
            parse_mode="HTML",
            reply_markup=build_main_keyboard(),
        )
        return

    if budget_remaining() <= 0:
        await update.message.reply_text(
            "⏳ Se agotó la cuota diaria compartida de chat libre.\n"
            "Sigue consultando todos los rankings gratis y al instante con <b>/menu</b> o <b>/buscar</b>.",
            parse_mode="HTML",
            reply_markup=build_main_keyboard(),
        )
        return

    user_text = update.message.text
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        snapshot = await load_snapshot()
        context_data = build_context(snapshot)

        response = await asyncio.to_thread(brain.generate_response, user_text, context_data)
        if not response:
            response = "No pude generar una respuesta ahora mismo. Prueba con /menu para ver los rankings directos."

        await send_html(context.bot, update.effective_chat.id, response)

    except Exception as e:
        logger.error("Error en chat: %s", e, exc_info=True)
        await update.message.reply_text(
            f"Tuve un contratiempo técnico procesando la consulta. [<code>{escape_html(str(e)[:90])}</code>]",
            parse_mode="HTML",
        )


# ------------------------------------------------------------- error handler


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Captura errores del bot, los loguea y notifica al admin en HTML escapado."""
    logger.error("Excepción durante procesamiento de update:", exc_info=context.error)

    tb_string = "".join(traceback.format_exception(None, context.error, context.error.__traceback__))
    update_str = update.to_dict() if isinstance(update, Update) else str(update)
    message = (
        f"🚨 <b>Error en Ranking AI Bot</b>\n\n"
        f"<pre>{escape_html(json.dumps(update_str, indent=2, ensure_ascii=False)[:1000])}</pre>\n\n"
        f"<pre>{escape_html(tb_string[-2000:])}</pre>"
    )

    if ADMIN_CHAT_ID:
        try:
            await context.bot.send_message(
                chat_id=int(ADMIN_CHAT_ID),
                text=message[:4096],
                parse_mode="HTML",
            )
        except Exception as notify_err:  # noqa: BLE001
            logger.error("No se pudo notificar al admin: %s", notify_err)


# ------------------------------------------------------------------- jobs


async def heartbeat(context: ContextTypes.DEFAULT_TYPE) -> None:
    now_str = utc_now_iso()
    RUNTIME_STATE["last_heartbeat"] = now_str
    logger.info("💓 Heartbeat — Bot activo a las %s UTC", now_str)


async def periodic_update(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Job principal: revisa rankings + tendencias cada UPDATE_INTERVAL_MINUTES (60 min por defecto)."""
    logger.info("🔄 [Job] Iniciando revisión periódica (%d min)...", UPDATE_INTERVAL_MINUTES)
    try:
        result = await get_updater(context.bot).check_and_update(force=False)
        if result["changes_detected"]:
            logger.info("📣 [Job] Cambios detectados y notificados")
        else:
            logger.info("✅ [Job] %s", result.get("message", "Verificación completada"))
    except Exception as e:
        logger.error("❌ [Job] Error en actualización periódica: %s", e, exc_info=True)


async def post_init(application) -> None:
    """Registra comandos visibles y jobs periódicos tras inicializar el bot."""
    application.bot_data["start_time"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    RUNTIME_STATE["bot_polling_active"] = True
    RUNTIME_STATE["last_heartbeat"] = utc_now_iso()
    logger.info("🚀 Bot inicializado. Registrando jobs periódicos...")

    try:
        await application.bot.set_my_commands(
            [
                ("menu", "Explorador rápido de rankings (0 cuota)"),
                ("hoy", "Briefing del día: rankings + GitHub + HN"),
                ("buscar", "Buscar posición y precio de un modelo"),
                ("chart", "Gráfica visual del Top actual"),
                ("alertas", "Personalizar notificaciones"),
                ("status", "Estado del sistema"),
                ("unsubscribe", "Darse de baja de alertas"),
            ]
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("No se pudo registrar menú de comandos en Telegram: %s", exc)

    update_seconds = max(300, UPDATE_INTERVAL_MINUTES * 60)
    application.job_queue.run_repeating(self_ping, interval=300, first=30, name="self_ping")
    application.job_queue.run_repeating(heartbeat, interval=1800, first=60, name="heartbeat")
    application.job_queue.run_repeating(
        periodic_update,
        interval=update_seconds,
        first=45,
        name="periodic_update",
    )
    application.job_queue.run_daily(
        daily_briefing,
        time=dtime(hour=DAILY_BRIEF_HOUR, minute=0, tzinfo=timezone.utc),
        name="daily_briefing",
    )

    logger.info(
        "✅ Jobs registrados: self_ping (5 min), heartbeat (30 min), "
        "periodic_update (%d min), daily_briefing (%02d:00 UTC)",
        UPDATE_INTERVAL_MINUTES,
        DAILY_BRIEF_HOUR,
    )


# ---------------------------------------------------------------- entry point


def ensure_chromium() -> None:
    """Instala Chromium en segundo plano si falta (sin bloquear el puerto HTTP al arrancar)."""
    if os.getenv("SKIP_CHROMIUM_INSTALL", "").lower() in ("1", "true", "yes"):
        RUNTIME_STATE["chromium_ready"] = True
        logger.info("⏭️ SKIP_CHROMIUM_INSTALL activo: Chromium preinstalado en contenedor.")
        return
    try:
        import subprocess
        import sys

        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium", "chromium-headless-shell"],
            check=True,
            timeout=600,
            capture_output=True,
        )
        RUNTIME_STATE["chromium_ready"] = True
        logger.info("🌐 Chromium de Playwright disponible")
    except Exception as exc:  # noqa: BLE001
        logger.error("❌ No se pudo preparar Chromium para Playwright: %s", exc)


if __name__ == "__main__":
    os.makedirs("data", exist_ok=True)

    # 1. Abrir el puerto HTTP ANTES que nada (<100 ms) para evitar timeouts en Render / HF Spaces
    flask_thread = threading.Thread(target=run_flask, name="FlaskKeepAlive", daemon=True)
    flask_thread.start()
    logger.info("🌐 Servidor HTTP iniciado inmediatamente (puerto abierto para healthcheck 24/7)")

    # 2. Verificar/instalar Chromium en hilo de fondo sin bloquear el arranque del bot
    chromium_thread = threading.Thread(target=ensure_chromium, name="ChromiumSetup", daemon=True)
    chromium_thread.start()

    if not TELEGRAM_TOKEN:
        logger.error("❌ No se encontró TELEGRAM_TOKEN en las variables de entorno; sirviendo solo Dashboard Web.")
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
        application.add_handler(CommandHandler("menu", menu_cmd))
        application.add_handler(CommandHandler("subscribe", subscribe))
        application.add_handler(CommandHandler("alertas", alertas_cmd))
        application.add_handler(CommandHandler("unsubscribe", unsubscribe))
        application.add_handler(CommandHandler("buscar", buscar_cmd))
        application.add_handler(CommandHandler("chart", chart_cmd))
        application.add_handler(CommandHandler("status", status))
        application.add_handler(CommandHandler("forceupdate", force_update))
        application.add_handler(CommandHandler("hoy", hoy))
        application.add_handler(CallbackQueryHandler(handle_callback))
        application.add_handler(InlineQueryHandler(handle_inline_query))
        application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
        application.add_error_handler(error_handler)

        logger.info("🤖 Bot y Dashboard Web iniciados. Polling resiliente activado...")

        application.run_polling(
            drop_pending_updates=True,
            poll_interval=1.0,
            timeout=30,
            allowed_updates=Update.ALL_TYPES,
        )
