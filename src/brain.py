"""Cerebro del bot: Gemini vía SDK oficial ``google-genai``.

- Instancia única compartida (antes había dos: main y updater).
- Todas las llamadas a LLM son síncronas; los callers async usan ``asyncio.to_thread``
  para no bloquear el event loop.
- Presupuesto diario global de llamadas (GEMINI_DAILY_LIMIT) compartido por chat
  y alertas, para que el modo multiusuario no dispare la cuota.
- ``build_context`` convierte un snapshot estructurado en contexto compacto para
  el LLM (antes se le pasaba texto crudo de web).
"""

import logging
import threading
from datetime import date, datetime

from src.config import GEMINI_API_KEY, GEMINI_DAILY_LIMIT

logger = logging.getLogger(__name__)

# Orden de preferencia; se itera al primer error (cuota, descontinuación...).
PREFERRED_MODELS = [
    "gemini-flash-latest",
    "gemini-2.5-flash",
    "gemini-2.5-pro",
]

# Contexto máximo de rankings que se inyecta en el prompt (caracteres).
MAX_CONTEXT_CHARS = 14_000

# Etiquetas HTML permitidas por Telegram (el resto el modelo no debe usarlas).
_HTML_RULES = """
⛔ REGLAS CRÍTICAS DE FORMATO (Telegram):
1. Usa SOLO estos HTML: <b>, <i>, <u>, <a>, <code>, <pre>. Nada de Markdown ni tablas.
2. Nada de subrayados con guion bajo ni caracteres markdown (* _ ` ~).
3. Usa listas verticales con emojis, nunca tablas (se rompen en móviles).
4. No inventes datos: todo número debe venir del CONTEXTO.
"""


class QuotaExceeded(Exception):
    """Se agotó el presupuesto diario de llamadas a Gemini."""


class DailyBudget:
    """Contador global de llamadas a LLM por día (proceso actual)."""

    def __init__(self, limit: int):
        self.limit = limit
        self._date = date.today()
        self._used = 0
        self._lock = threading.Lock()

    def take(self) -> bool:
        with self._lock:
            if date.today() != self._date:
                self._date = date.today()
                self._used = 0
            if self._used >= self.limit:
                return False
            self._used += 1
            return True

    @property
    def remaining(self) -> int:
        with self._lock:
            if date.today() != self._date:
                self._date = date.today()
                self._used = 0
            return max(0, self.limit - self._used)


_budget = DailyBudget(GEMINI_DAILY_LIMIT)

_instance = None
_instance_lock = threading.Lock()


def get_brain() -> "RankingBrain":
    """Instancia única del cerebro (thread-safe)."""
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = RankingBrain()
    return _instance


def budget_remaining() -> int:
    return _budget.remaining


class RankingBrain:
    def __init__(self):
        self.client = None
        if not GEMINI_API_KEY:
            logger.warning("ADVERTENCIA: GEMINI_API_KEY no encontrada. El bot no podrá generar respuestas.")
        else:
            try:
                from google import genai

                self.client = genai.Client(api_key=GEMINI_API_KEY)
            except Exception as exc:  # noqa: BLE001 - configuración inválida no debe tumbar el arranque
                logger.error("No se pudo inicializar google-genai: %s", exc)
                self.client = None

    # --------------------------------------------------------------- helpers

    def _generate(self, prompt: str, max_tokens: int | None = None) -> str | None:
        """Genera contenido probando los modelos en orden. Devuelve None si todos fallan."""
        if not self.client:
            return None
        from google.genai import types

        config = types.GenerateContentConfig(
            max_output_tokens=max_tokens,
            temperature=0.7,
        )
        for model_name in PREFERRED_MODELS:
            try:
                response = self.client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config=config,
                )
                text = (response.text or "").strip() if response else ""
                if text:
                    return text
                logger.warning("Modelo %s no devolvió texto utilizable.", model_name)
            except Exception as exc:  # noqa: BLE001 - se itera al siguiente modelo
                msg = str(exc).lower()
                if "safety" in msg:
                    logger.warning("Bloqueo de seguridad con %s. Probando siguiente.", model_name)
                else:
                    logger.warning("Error con %s: %s. Probando siguiente.", model_name, str(exc)[:200])
        return None

    def _generate_or_raise(self, prompt: str, max_tokens: int | None = None) -> str:
        if not _budget.take():
            raise QuotaExceeded(f"Presupuesto diario de Gemini agotado (límite {GEMINI_DAILY_LIMIT})")
        text = self._generate(prompt, max_tokens=max_tokens)
        if not text:
            raise RuntimeError("Gemini no devolvió texto con ningún modelo disponible")
        return text

    # ------------------------------------------------------------- API pública

    def check_health(self) -> tuple[bool, str]:
        """Verifica que el cerebro responde (llamada de1 token)."""
        if not self.client:
            return False, "API Key no configurada."
        try:
            self._generate("Ping", max_tokens=1)
            return True, f"Cerebro operativo (quota restante hoy: {budget_remaining()})."
        except Exception as exc:  # noqa: BLE001
            return False, f"Error de conexión con Gemini: {str(exc)[:200]}"

    def generate_response(self, user_query: str, context_data: str) -> str:
        """Respuesta al chat del usuario. Síncrono: llamar con asyncio.to_thread."""
        system_prompt = f"""
        Eres el 'Ranking AI Agent', un analista de IA curioso, entusiasta y muy organizado.
        Tu misión es responder en español con los datos de rankings y tendencias aportados.

        CONTEXTO DE DATOS ACTUALES:
        {context_data[:MAX_CONTEXT_CHARS]}

        {_HTML_RULES}

        🎨 ESTILO DE RESPUESTA:
        - Empieza con un "Hook" curioso o un dato interesante.
        - Para tops usa este formato:
          🏆 <b>1. [Nombre Modelo]</b>
          ├─ 📊 Score: [Puntaje/Elo]
          └─ 🧠 Insight: [Dato breve o por qué destaca]
        - Agrupa por categorías si hay varias (General, Coding, Velocidad...).
        - Si el contexto no contiene la respuesta, dilo con honestidad y sugiere qué preguntar.
        - Tono: Techie, informativo y directo. Máximo 300 palabras.
        """
        prompt = f"{system_prompt}\n\nPregunta del Usuario: {user_query}"
        try:
            return self._generate_or_raise(prompt)
        except QuotaExceeded:
            return (
                "⏳ Se agotó la cuota diaria de consultas de IA del bot. "
                f"Vuelve mañana (presupuesto: {GEMINI_DAILY_LIMIT} llamadas/día)."
            )
        except RuntimeError:
            return "Lo siento, todos los modelos de IA están ocupados o no disponibles en este momento."

    def draft_newsletter(self, template: str, summary: str) -> str | None:
        """Pule el boletín determinista con Gemini. Síncrono; None si falla (usa plantilla).

        Regla estricta: no añadir hechos que no estén en la plantilla.
        """
        if not self.client or not _budget.take():
            return None
        prompt = f"""
        Redacta un boletín de Telegram en español, vibrante pero VERAZ, a partir del
        boletín base de abajo. Puedes reordenar y pulir el estilo, pero NO inventar
        ni alterar ningún dato, modelo, número o puesto: solo se permite jugar con
        el tono y las palabras. Máximo 180 palabras. Emojis sí.

        {_HTML_RULES}

        BOLETÍN BASE:
        {template}

        RESUMEN TÉCNICO (para tu referencia):
        {summary[:600]}
        """
        text = self._generate(prompt, max_tokens=700)
        if text and 50 < len(text) < 3500:
            return text
        return None

    def draft_briefing(self, rankings_part: str, trending_part: str) -> str | None:
        """Pule el briefing diario /hoy. Síncrono; None si falla (usa plantilla)."""
        if not self.client or not _budget.take():
            return None
        prompt = f"""
        Escribe el briefing matutino de un usuario sobre IA y dev (Telegram, español).
        Combina AMBOS bloques de abajo en un mensaje coherente de máximo 220 palabras,
        con un gancho inicial. NO inventes datos que no estén en los bloques.

        {_HTML_RULES}

        RANKINGS:
        {rankings_part[:4000]}

        TENDENCIAS:
        {trending_part[:4000]}
        """
        text = self._generate(prompt, max_tokens=900)
        if text and 50 < len(text) < 3500:
            return text
        return None


# --------------------------------------------------------------------- contexto


def _fmt_number(n) -> str:
    """1234567 → '1.23M'-ish compacto (formato corto para el LLM)."""
    if not isinstance(n, int):
        return str(n)
    for divisor, suffix in ((1_000_000_000_000, "T"), (1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if n >= divisor:
            return f"{n / divisor:.1f}{suffix}"
    return str(n)


def build_context(snapshot: dict | None) -> str:
    """Convierte un snapshot estructurado en contexto compacto de texto para el LLM."""
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("sources"), dict):
        return "No hay datos de rankings disponibles en este momento."

    generated = snapshot.get("generated_at", "")[:16].replace("T", " ")
    lines = [f"DATOS ACTUALIZADOS: {generated}"]

    order = ("arena", "openrouter", "design", "github", "hackernews")
    for source in order + tuple(k for k in snapshot["sources"] if k not in order):
        payload = snapshot["sources"].get(source)
        if not isinstance(payload, dict):
            continue

        status = payload.get("status")
        if status == "stale":
            lines.append(f"\n[{source}] DATOS STALE (fetch falló): {str(payload.get('error', ''))[:150]}")
            continue
        if status != "ok":
            lines.append(f"\n[{source}] NO DISPONIBLE: {str(payload.get('error', ''))[:150]}")
            continue

        if source == "github":
            ai = payload.get("ai") or []
            general = payload.get("general") or []
            if ai:
                lines.append("\n[GITHUB - repos nuevos de IA en tendencia]")
                for r in ai[:8]:
                    desc = (r.get("description") or "").strip()[:100]
                    lines.append(f"  - {r['full_name']} ({_fmt_number(r.get('stars'))}★) {desc}")
            if general:
                lines.append("[GITHUB - repos generales en tendencia]")
                for r in general[:5]:
                    lines.append(f"  - {r['full_name']} ({_fmt_number(r.get('stars'))}★)")
            continue

        if source == "hackernews":
            items = payload.get("items") or []
            if items:
                lines.append("\n[HACKER NEWS - historias recientes]")
                for s in items[:8]:
                    lines.append(f"  - {s['title']} ({s.get('points', 0)} pts, {s.get('comments', 0)} comentarios)")
            continue

        models = payload.get("models") or []
        by_cat: dict[str, list] = {}
        for m in models:
            by_cat.setdefault(m.get("category") or "default", []).append(m)

        lines.append(f"\n[{source.upper()}]")
        for cat, entries in by_cat.items():
            entries = sorted(entries, key=lambda x: x.get("rank", 999))[:10]
            label = f"{source}/{cat}" if cat != "default" else source
            lines.append(f"  {label}:")
            for m in entries:
                if "tokens_week" in m:
                    metric = f"{_fmt_number(m['tokens_week'])} tokens/sem, +{m.get('growth_pct', 0)}%"
                elif "votes" in m:
                    metric = f"{m.get('score')} elo, {_fmt_number(m.get('votes'))} votos"
                elif "win_rate_pct" in m:
                    metric = f"{m.get('score')} elo, win {m.get('win_rate_pct')}%"
                else:
                    metric = f"score {m.get('score')}"
                lines.append(f"    {m.get('rank')}. {m.get('model')} — {metric}")

    text = "\n".join(lines)
    return text[:MAX_CONTEXT_CHARS]


def snapshot_age_hours(snapshot: dict | None) -> float | None:
    """Horas desde que se generó el snapshot (None si no se puede determinar)."""
    if not isinstance(snapshot, dict) or not snapshot.get("generated_at"):
        return None
    try:
        generated = datetime.fromisoformat(snapshot["generated_at"])
    except ValueError:
        return None
    return (datetime.now() - generated).total_seconds() / 3600


def snapshot_briefing_parts(snapshot: dict | None) -> tuple[str, str]:
    """Extrae (parte de rankings, parte de tendencias) en texto para el briefing /hoy."""
    context = build_context(snapshot)
    parts = context.split("[GITHUB")
    rankings = parts[0]
    trending = ("[GITHUB" + parts[1]) if len(parts) > 1 else "Las tendencias no están disponibles ahora mismo."
    return rankings, trending


__all__ = [
    "RankingBrain",
    "get_brain",
    "build_context",
    "budget_remaining",
    "snapshot_age_hours",
    "snapshot_briefing_parts",
    "QuotaExceeded",
]
