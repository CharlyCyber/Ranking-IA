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
