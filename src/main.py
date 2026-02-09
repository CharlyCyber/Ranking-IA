import logging
import json
import os
import threading
import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters, CommandHandler
from src.config import TELEGRAM_TOKEN
from src.brain import RankingBrain
from flask import Flask

# --- Configuración Flask (Para mantener vivo el bot en Render) ---
app = Flask(__name__)

@app.route('/')
@app.route('/health')
def health():
    return {"status": "ok", "message": "Ranking AI Agent is Alive! 🤖", "timestamp": datetime.now().isoformat()}

def run_flask():
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)

# --- Configuración del Bot ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

brain = RankingBrain()

import time
from datetime import datetime

# URL Raw de GitHub para los datos (reemplazar con el repo real si es necesario)
# Por defecto intentaremos deducirlo del entorno o usar una rama específica 'data'
DATA_URL = os.getenv("DATA_JSON_URL", "https://raw.githubusercontent.com/Charli/Ranking-AI/data/data/rankings.json")

def load_cached_data():
    """Carga los datos del JSON. Intenta primero desde GitHub Raw para evitar redespliegues."""
    try:
        # 1. Intentar carga remota (siempre tendrá lo último sin reiniciar el bot)
        try:
            response = requests.get(DATA_URL, timeout=10)
            if response.status_code == 200:
                data = response.json()
                logging.info("Datos cargados exitosamente desde GitHub Raw.")
                return json.dumps(data, indent=2, ensure_ascii=False), "Online (GitHub)"
        except Exception as e:
            logging.warning(f"No se pudo cargar desde GitHub Raw, usando local: {e}")

        # 2. Fallback a archivo local
        path = os.path.join("data", "rankings.json")
        if not os.path.exists(path):
            return "Aún no hay datos cacheados. Esperando primera actualización...", None
        
        mod_time = os.path.getmtime(path)
        last_update = datetime.fromtimestamp(mod_time).strftime('%Y-%m-%d %H:%M:%S')
        
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return json.dumps(data, indent=2, ensure_ascii=False), f"Local ({last_update})"
    except Exception as e:
        logging.error(f"Error cargando caché: {e}")
        return "Error leyendo base de datos.", None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mensaje de bienvenida."""
    await update.message.reply_text(
        "¡Hola! Soy tu **Ranking AI Agent** 🏆. \n\n"
        "Mis datos se actualizan automáticamente cada 6 horas.\n"
        "Usa /status para verificar mi estado de salud.\n"
        "Pregúntame lo que quieras sobre el estado del arte de la IA."
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Chequeo de salud del sistema."""
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    
    # 1. Verificar datos
    _, last_update = load_cached_data()
    data_status = f"✅ Datos actualizados ({last_update})" if last_update else "⚠️ Sin datos o datos antiguos"
    
    # 2. Verificar Cerebro (Gemini)
    brain_ok, brain_msg = brain.check_health()
    brain_status = "✅ Gemini Conectado" if brain_ok else f"❌ Error Gemini: {brain_msg}"
    
    await update.message.reply_text(
        f"📊 **Estado del Sistema**\n\n"
        f"**Base de Datos:** {data_status}\n"
        f"**IA Brain:** {brain_status}\n"
        f"**Versión:** 1.1 (Stable)"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Procesa las preguntas usando datos locales + Gemini."""
    user_text = update.message.text
    # Feedback rápido al usuario
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        # 1. Cargar datos del disco
        rankings_context, _ = load_cached_data()
        if not rankings_context:
            rankings_context = "No hay datos de rankings disponibles en este momento."
        
        # 2. Generar respuesta con Gemini
        response = brain.generate_response(user_text, rankings_context)
        
        if not response:
            response = "Lo siento, no pude generar una respuesta en este momento. Intenta preguntar de otra forma."

        # 3. Enviar respuesta con fallback para errores de Markdown
        try:
            await update.message.reply_text(response, parse_mode='Markdown')
        except Exception as e:
            logging.warning(f"Error parseando Markdown, enviando como texto plano: {e}")
            await update.message.reply_text(response)
            
    except Exception as e:
        error_detail = str(e)
        logging.error(f"Error en chat: {error_detail}", exc_info=True)
        await update.message.reply_text(f"Tuve un pequeño cortocircuito técnico. 🛠️\nError: {error_detail[:100]}")

if __name__ == '__main__':
    # Asegurar que el directorio de datos existe
    os.makedirs("data", exist_ok=True)
    
    # Iniciar Flask en un hilo separado
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()

    # Iniciar Bot
    if not TELEGRAM_TOKEN:
        logging.error("No se encontró TELEGRAM_TOKEN en las variables de entorno.")
        # Mantener el hilo de Flask vivo si el bot falla
        flask_thread.join()
    else:
        application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
        
        start_handler = CommandHandler('start', start)
        status_handler = CommandHandler('status', status)
        message_handler = MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message)
        
        application.add_handler(start_handler)
        application.add_handler(status_handler)
        application.add_handler(message_handler)
        
        print("Bot y Servidor Web iniciados...")
        application.run_polling(drop_pending_updates=True)