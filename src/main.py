import logging
import json
import os
import threading
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters, CommandHandler
from src.config import TELEGRAM_TOKEN
from src.brain import RankingBrain
from flask import Flask

# --- Configuración Flask (Para mantener vivo el bot en Render) ---
app = Flask(__name__)

@app.route('/')
def home():
    return "Ranking AI Agent is Alive! 🤖"

def run_flask():
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)

# --- Configuración del Bot ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

brain = RankingBrain()

def load_cached_data():
    """Carga los datos del JSON generado por GitHub Actions."""
    try:
        path = os.path.join("data", "rankings.json")
        if not os.path.exists(path):
            return "Aún no hay datos cacheados. Esperando primera actualización..."
        
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Convertir diccionario a texto bonito para el prompt
        return json.dumps(data, indent=2, ensure_ascii=False)
    except Exception as e:
        logging.error(f"Error cargando caché: {e}")
        return "Error leyendo base de datos local."

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mensaje de bienvenida."""
    await update.message.reply_text(
        "¡Hola! Soy tu **Ranking AI Agent** 🏆. \n\n"
        "Mis datos se actualizan automáticamente cada 6 horas gracias a GitHub Actions. "
        "Pregúntame lo que quieras sobre el estado del arte de la IA."
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Procesa las preguntas usando datos locales + Gemini."""
    user_text = update.message.text
    # Feedback rápido al usuario
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        # 1. Cargar datos del disco (Ultra rápido, sin espera de scraping)
        rankings_context = load_cached_data()
        
        # 2. Generar respuesta con Gemini
        response = brain.generate_response(user_text, rankings_context)
        
        # 3. Enviar respuesta
        await update.message.reply_text(response, parse_mode='Markdown')
    except Exception as e:
        logging.error(f"Error en chat: {e}")
        await update.message.reply_text("Tuve un pequeño cortocircuito. ¿Podrías preguntar de nuevo?")

if __name__ == '__main__':
    # Iniciar servidor web en segundo plano
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    
    # Iniciar Bot
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    
    start_handler = CommandHandler('start', start)
    message_handler = MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message)
    
    application.add_handler(start_handler)
    application.add_handler(message_handler)
    
    print("Bot y Servidor Web iniciados...")
    application.run_polling()