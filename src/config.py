"""Configuración centralizada desde variables de entorno con defaults seguros."""

import os

from dotenv import load_dotenv

load_dotenv()


def _env_int(name: str, default: int, minimum: int | None = None, maximum: int | None = None) -> int:
    raw = (os.getenv(name) or "").strip()
    try:
        value = int(raw) if raw else default
    except ValueError:
        value = default
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _env_bool(name: str, default: bool = False) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


TELEGRAM_TOKEN = (os.getenv("TELEGRAM_TOKEN") or "").strip() or None
GEMINI_API_KEY = (os.getenv("GEMINI_API_KEY") or "").strip() or None
GROQ_API_KEY = (os.getenv("GROQ_API_KEY") or "").strip() or None
OPENROUTER_API_KEY = (os.getenv("OPENROUTER_API_KEY") or "").strip() or None

# El CHAT ID del administrador autoriza /forceupdate y recibe alertas técnicas
ADMIN_CHAT_ID = (os.getenv("CHAT_ID") or "").strip() or None

# Canal público opcional (ej. @RankingIA_ES) para difusión masiva en 1 sola llamada
PUBLIC_CHANNEL_ID = (os.getenv("PUBLIC_CHANNEL_ID") or "").strip() or None

# URL pública externa (activa ping externo real y Telegram Mini App)
# En Hugging Face Spaces se puede inferir de SPACE_HOST si no se define PUBLIC_URL
_space_host = (os.getenv("SPACE_HOST") or "").strip()
_default_public = f"https://{_space_host}" if _space_host else ""
PUBLIC_URL = (
    os.getenv("PUBLIC_URL") or os.getenv("RENDER_EXTERNAL_URL") or _default_public
).strip().rstrip("/")

# Persistencia gratuita en la nube vía HTTP API de Turso (libSQL)
TURSO_DATABASE_URL = (os.getenv("TURSO_DATABASE_URL") or "").strip().rstrip("/")
TURSO_AUTH_TOKEN = (os.getenv("TURSO_AUTH_TOKEN") or "").strip()

# URLs oficiales de Ranking (en inglés canónico para evitar roturas por traducciones de UI)
URLS = {
    "arena": "https://arena.ai/leaderboard",
    "openrouter": "https://openrouter.ai/rankings",
    "openrouter_models_api": "https://openrouter.ai/api/v1/models",
    "design_arena": "https://www.designarena.ai/leaderboard",
}

# --- Frecuencia, tendencias, briefing y límites ---
GITHUB_TOKEN = (os.getenv("GITHUB_TOKEN") or "").strip() or None

# URL raw de data/rankings.json en la rama `data` (respaldo remoto opcional)
DATA_JSON_URL = (os.getenv("DATA_JSON_URL") or "").strip()

# Intervalo de revisión automática en minutos (por defecto 60 = cada 1 hora)
UPDATE_INTERVAL_MINUTES = _env_int("UPDATE_INTERVAL_MINUTES", 60, minimum=5, maximum=1440)

# Modo respaldo silencioso (usado por GitHub Actions cuando el bot 24/7 ya está despierto)
SKIP_TELEGRAM_BROADCAST = _env_bool("SKIP_TELEGRAM_BROADCAST", default=False)

# Hora UTC del briefing diario /hoy (0-23)
DAILY_BRIEF_HOUR = _env_int("DAILY_BRIEF_HOUR", 9, minimum=0, maximum=23)

# Presupuesto global diario de llamadas a LLM (protege la cuota compartida)
GEMINI_DAILY_LIMIT = _env_int("GEMINI_DAILY_LIMIT", 200, minimum=1)

# Consultas de texto libre por usuario y hora
USER_RATE_LIMIT = _env_int("USER_RATE_LIMIT", 10, minimum=1)
