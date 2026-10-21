import os

from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
# El CHAT ID puede ser útil para logs o notificaciones administrativas
ADMIN_CHAT_ID = os.getenv("CHAT_ID")

# URLs de Ranking
URLS = {
    "arena": "https://arena.ai/es/leaderboard/",
    "openrouter": "https://openrouter.ai/rankings",
    "context7": "https://context7.com/rankings",
    "artificial_analysis": "https://artificialanalysis.ai",
    "design_arena": "https://www.designarena.ai/leaderboard"
}

# API Keys para servicios específicos extraídas del .env
API_KEYS = {
    "context7": os.getenv("CONTEXT7_API_KEY"),
    "artificial_analysis": os.getenv("ARTIFICIAL_ANALYSIS_API_KEY")
}

# --- V3: Tendencias, briefing y límites ---
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

# URL raw de data/rankings.json en la rama `data` (respaldo remoto opcional)
DATA_JSON_URL = os.getenv("DATA_JSON_URL", "")

# Hora local del briefing diario /hoy (0-23)
DAILY_BRIEF_HOUR = max(0, min(23, int(os.getenv("DAILY_BRIEF_HOUR", "9"))))

# Presupuesto global diario de llamadas a Gemini (protege la cuota compartida)
GEMINI_DAILY_LIMIT = max(1, int(os.getenv("GEMINI_DAILY_LIMIT", "200")))

# Consultas de texto por usuario y hora
USER_RATE_LIMIT = max(1, int(os.getenv("USER_RATE_LIMIT", "10")))
