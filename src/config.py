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
TAVILY_API_KEY = (os.getenv("TAVILY_API_KEY") or "").strip() or None

# Modelos configurables sin editar código. OpenRouter puede enrutar a un modelo gratuito
# actualmente disponible; mantener una alternativa fija como respaldo.
def _env_csv(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    values = tuple(item.strip() for item in (os.getenv(name) or "").split(",") if item.strip())
    return values or default


GEMINI_MODELS = _env_csv("GEMINI_MODELS", ("gemini-2.5-flash", "gemini-2.5-flash-lite"))
GROQ_MODELS = _env_csv("GROQ_MODELS", ("llama-3.3-70b-versatile", "llama-3.1-8b-instant"))
OPENROUTER_FREE_MODELS = _env_csv(
    "OPENROUTER_FREE_MODELS",
    ("openrouter/free", "meta-llama/llama-3.3-70b-instruct:free"),
)

# El CHAT ID del administrador autoriza /forceupdate y recibe alertas técnicas
ADMIN_CHAT_ID = (os.getenv("CHAT_ID") or "").strip() or None
# Identidad del usuario que puede ejecutar comandos administrativos; si se omite, conserva compatibilidad con CHAT_ID.
ADMIN_USER_ID = (os.getenv("ADMIN_USER_ID") or "").strip() or None

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
    "arena_mirror_json": "https://raw.githubusercontent.com/Tliens/ai-model-rank/main/data.json",
    "openrouter": "https://openrouter.ai/rankings",
    "openrouter_models_api": "https://openrouter.ai/api/v1/models",
    "design_arena": "https://www.designarena.ai/leaderboard",
}

# --- Frecuencia, tendencias, briefing y límites ---
GITHUB_TOKEN = (os.getenv("GITHUB_TOKEN") or "").strip() or None

# URL raw de data/rankings.json en la rama `data` (respaldo remoto automático)
DATA_JSON_URL = (
    os.getenv("DATA_JSON_URL")
    or "https://raw.githubusercontent.com/CharlyCyber/Ranking-IA/data/data/rankings.json"
).strip()

# Intervalo objetivo de revisión en minutos (1 min por defecto; cada fuente puede publicar menos a menudo).
UPDATE_INTERVAL_MINUTES = _env_int("UPDATE_INTERVAL_MINUTES", 1, minimum=1, maximum=1440)

# all = compatibilidad (web + bot); bot permite escalar el dashboard por separado.
APP_MODE = (os.getenv("APP_MODE") or "all").strip().lower()
if APP_MODE not in {"all", "bot", "web"}:
    APP_MODE = "all"

# Modo respaldo silencioso (usado por GitHub Actions cuando el bot 24/7 ya está despierto)
SKIP_TELEGRAM_BROADCAST = _env_bool("SKIP_TELEGRAM_BROADCAST", default=False)

# Desactivar Playwright en contenedores de 512 MB RAM (usa solo APIs + HTTP ligero)
DISABLE_PLAYWRIGHT = _env_bool("DISABLE_PLAYWRIGHT", default=False)

# Hora UTC del briefing diario /hoy (0-23)
DAILY_BRIEF_HOUR = _env_int("DAILY_BRIEF_HOUR", 9, minimum=0, maximum=23)

# Presupuesto global diario compartido por todos los proveedores LLM.
# GEMINI_DAILY_LIMIT se conserva como alias para despliegues antiguos.
LLM_DAILY_LIMIT = _env_int(
    "LLM_DAILY_LIMIT", _env_int("GEMINI_DAILY_LIMIT", 200, minimum=1), minimum=1
)
GEMINI_DAILY_LIMIT = LLM_DAILY_LIMIT

# Consultas de texto libre por usuario y hora
USER_RATE_LIMIT = _env_int("USER_RATE_LIMIT", 10, minimum=1)
# Protección básica por proceso para API pública; en réplicas, complementar con CDN/API gateway.
API_RATE_LIMIT_PER_MINUTE = _env_int("API_RATE_LIMIT_PER_MINUTE", 120, minimum=10)
LIVE_SEARCH_DAILY_LIMIT = _env_int("LIVE_SEARCH_DAILY_LIMIT", 100, minimum=1)
