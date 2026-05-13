import logging
import json
import os
import html
import threading
import traceback
import httpx
import asyncio
from datetime import datetime
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    MessageHandler,
    filters,
    CommandHandler,
)
from src.config import TELEGRAM_TOKEN, ADMIN_CHAT_ID
from src.brain import RankingBrain
from flask import Flask

# Importación lazy de DataUpdater para evitar cargar Playwright al inicio
updater = None


def get_updater(bot=None):
    """Obtiene instancia de DataUpdater (lazy import)."""
    global updater
    if updater is None:
        from src.updater import DataUpdater

        updater = DataUpdater(bot=bot)
    elif bot is not None:
        updater.bot = bot
    return updater


# --- Configuración de Logging ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Configuración Flask (Para mantener vivo el bot en Render) ---
flask_app = Flask(__name__)

# Registrar dashboard y API
from src.web import web
flask_app.register_blueprint(web)


@flask_app.route("/")
@flask_app.route("/health")
def health():
    return {
        "status": "ok",
        "message": "Ranking AI Agent is Alive!",
        "timestamp": datetime.now().isoformat(),
        "uptime_check": True,
    }


def run_flask():
    port = int(os.environ.get("PORT", 5000))
    flask_app.run(host="0.0.0.0", port=port)


# --- Inicialización del Cerebro ---
brain = RankingBrain()

# URL Raw de GitHub para los datos
DATA_URL = os.getenv("DATA_JSON_URL", "")


# --- Funciones de datos ---
async def load_cached_data():
    """Carga los datos del JSON. Intenta primero desde GitHub Raw si la URL está configurada."""
    try:
        if DATA_URL:
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.get(DATA_URL, timeout=10)
                    if response.status_code == 200:
                        data = response.json()
                        logger.info("Datos cargados exitosamente desde GitHub Raw.")
                        return json.dumps(
                            data, indent=2, ensure_ascii=False
                        ), "Online (GitHub)"
            except Exception as e:
                logger.warning(f"No se pudo cargar desde GitHub Raw: {e}")

        path = os.path.join("data", "rankings.json")
        if os.path.exists(path):
            mod_time = os.path.getmtime(path)
            last_update = datetime.fromtimestamp(mod_time).strftime("%Y-%m-%d %H:%M:%S")
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return json.dumps(
                data, indent=2, ensure_ascii=False
            ), f"Local ({last_update})"

        return "Aún no hay datos cacheados. Esperando primera actualización...", None
    except Exception as e:
        logger.error(f"Error cargando caché: {e}")
        return "Error leyendo base de datos.", None


# --- Handlers del Bot ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mensaje de bienvenida."""
    await update.message.reply_text(
        "¡Hola! Soy tu **Ranking AI Agent** 🏆. \n\n"
        "🔥 **Detección en Tiempo Real:** Mis datos se actualizan cada 30 minutos automáticamente.\n"
        "📊 Rastreo constante de: LMSYS Arena, OpenRouter, Design Arena y más.\n"
        "🚨 Te notificaré **inmediatamente** si hay cambios importantes en los rankings.\n\n"
        "Comandos disponibles:\n"
        "• /status - Ver estado del sistema\n"
        "• /forceupdate - Forzar actualización manual\n\n"
        "Pregúntame lo que quieras sobre el estado del arte de la IA."
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Chequeo de salud del sistema."""
    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id, action="typing"
    )

    _, last_update = await load_cached_data()
    data_status = (
        f"✅ Datos actualizados ({last_update})"
        if last_update
        else "⚠️ Sin datos o datos antiguos"
    )

    brain_ok, brain_msg = brain.check_health()
    brain_status = (
        "✅ Gemini Conectado" if brain_ok else f"❌ Error Gemini: {brain_msg}"
    )

    await update.message.reply_text(
        f"📊 **Estado del Sistema**\n\n"
        f"**Base de Datos:** {data_status}\n"
        f"**IA Brain:** {brain_status}\n"
        f"**Versión:** 2.0 (Real-Time)\n"
        f"**Uptime desde:** {context.bot_data.get('start_time', 'N/A')}"
    )


async def force_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fuerza una actualización manual de los datos."""
    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id, action="typing"
    )

    await update.message.reply_text("🔄 Forzando actualización de datos...")

    try:
        # Inicializar updater con el bot para poder notificar
        updater_instance = get_updater(context.bot)
        result = await updater_instance.check_and_update(force=True)

        if result["success"]:
            await update.message.reply_text(
                f"✅ **Actualización completada**\n\n"
                f"📊 Fuentes consultadas: {', '.join(result.get('sources_scraped', []))}\n"
                f"📝 {result.get('message', 'Datos actualizados correctamente')}"
            )
        else:
            await update.message.reply_text(
                f"❌ **Error en actualización**\n\n{result.get('message', 'Error desconocido')}"
            )
    except Exception as e:
        logger.error(f"Error en force_update: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Error: {str(e)}")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Procesa las preguntas usando datos locales + Gemini."""
    user_text = update.message.text
    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id, action="typing"
    )

    try:
        rankings_context, _ = await load_cached_data()
        if not rankings_context:
            rankings_context = "No hay datos de rankings disponibles en este momento."

        response = brain.generate_response(user_text, rankings_context)

        if not response:
            response = "Lo siento, no pude generar una respuesta en este momento. Intenta preguntar de otra forma."

        try:
            await update.message.reply_text(response, parse_mode="Markdown")
        except Exception as e:
            logger.warning(f"Error parseando Markdown, enviando como texto plano: {e}")
            await update.message.reply_text(response)

    except Exception as e:
        error_detail = str(e)
        logger.error(f"Error en chat: {error_detail}", exc_info=True)
        await update.message.reply_text(
            f"Tuve un pequeño cortocircuito técnico. [Error: {error_detail[:100]}]"
        )


# --- Error Handler Global ---
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Captura TODOS los errores del bot, loguea y notifica al admin."""
    logger.error("Excepción durante procesamiento de update:", exc_info=context.error)

    tb_list = traceback.format_exception(
        None, context.error, context.error.__traceback__
    )
    tb_string = "".join(tb_list)

    update_str = update.to_dict() if isinstance(update, Update) else str(update)
    message = (
        f"🚨 <b>Error en Ranking AI Bot</b>\n\n"
        f"<pre>update = {html.escape(json.dumps(update_str, indent=2, ensure_ascii=False)[:1000])}</pre>\n\n"
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


# --- Jobs Periódicos (Keep-Alive + Heartbeat) ---
async def self_ping(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ping al propio endpoint /health cada 5 min para evitar que Render duerma el servicio."""
    port = int(os.environ.get("PORT", 5000))
    url = f"http://localhost:{port}/health"
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=10)
            if resp.status_code == 200:
                logger.info(f"🏓 Self-ping OK ({url})")
            else:
                logger.warning(f"⚠️ Self-ping respondió con status {resp.status_code}")
    except Exception as e:
        logger.warning(f"⚠️ Self-ping falló: {e}")


async def heartbeat(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log periódico que confirma que el bot sigue vivo."""
    now = datetime.now().isoformat()
    logger.info(f"💓 Heartbeat — Bot activo a las {now}")


async def periodic_update(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Job periódico: Actualiza datos de rankings cada 30 minutos."""
    logger.info("🔄 [Job] Iniciando actualización periódica de rankings...")
    try:
        # Obtener instancia de updater con el bot para notificaciones
        updater_instance = get_updater(context.bot)
        result = await updater_instance.check_and_update(force=False)

        if result["changes_detected"]:
            logger.info("📣 [Job] Cambios detectados y notificados")
        else:
            logger.info("✅ [Job] Verificación completada - Sin cambios")

    except Exception as e:
        logger.error(f"❌ [Job] Error en actualización periódica: {e}", exc_info=True)


async def post_init(application) -> None:
    """Se ejecuta después de que el bot se inicializa correctamente."""
    application.bot_data["start_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    logger.info("🚀 Bot inicializado correctamente. Registrando jobs periódicos...")

    # Job: Self-ping cada 5 minutos (300 seg) para mantener vivo Render
    application.job_queue.run_repeating(
        self_ping,
        interval=300,
        first=30,  # Primer ping 30 seg después de arranque
        name="self_ping",
    )

    # Job: Heartbeat cada 30 minutos (1800 seg) para monitorear en logs
    application.job_queue.run_repeating(
        heartbeat,
        interval=1800,
        first=60,
        name="heartbeat",
    )

    # Job: Actualización de rankings cada 30 minutos (1800 seg)
    # Este es el job principal para detección en tiempo real
    application.job_queue.run_repeating(
        periodic_update,
        interval=1800,  # 30 minutos
        first=120,  # Primer chequeo 2 minutos después de iniciar
        name="periodic_update",
    )

    logger.info(
        "✅ Jobs registrados: self_ping (5 min), heartbeat (30 min), periodic_update (30 min)"
    )


# --- Entry Point ---
if __name__ == "__main__":
    os.makedirs("data", exist_ok=True)

    # Iniciar Flask en un hilo separado (NO daemon, para que sobreviva si el polling se reinicia)
    flask_thread = threading.Thread(target=run_flask, name="FlaskKeepAlive")
    flask_thread.daemon = (
        True  # Seguimos usando daemon para que no bloquee el cierre del proceso
    )
    flask_thread.start()
    logger.info("🌐 Servidor Flask iniciado en hilo separado")

    if not TELEGRAM_TOKEN:
        logger.error("❌ No se encontró TELEGRAM_TOKEN en las variables de entorno.")
        flask_thread.join()
    else:
        # Construir Application con timeouts y pool de conexiones robustos
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

        # Registrar handlers
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("status", status))
        application.add_handler(CommandHandler("forceupdate", force_update))
        application.add_handler(
            MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message)
        )

        # Registrar error handler global
        application.add_error_handler(error_handler)

        logger.info("🤖 Bot y Servidor Web iniciados. Polling resiliente activado...")

        # Polling con parámetros de resiliencia
        application.run_polling(
            drop_pending_updates=True,
            poll_interval=1.0,  # Intervalo entre polls (seg)
            timeout=30,  # Long polling timeout
            allowed_updates=Update.ALL_TYPES,
        )
