"""Diff determinista entre snapshots estructurados. Cero llamadas a LLM.

Detecta: cambio de top-1, modelos nuevos, movimientos de ≥3 posiciones,
saltos de score ≥30 puntos y repos de GitHub en aceleración.

Devuelve eventos con ``text`` ya redactado (HTML seguro de Telegram); el
updater los une en un boletín. Solo si hay eventos se invoca Gemini (para
pulir el boletín) y siempre con plantilla de fallback.
"""

from src.validator import is_legacy_snapshot

MOVE_THRESHOLD = 3
SCORE_JUMP = 30
NEW_REPO_MIN_STARS = 500
REPO_ACCEL_STARS = 300
MAX_EVENTS = 10

# Fuentes que llevan score estilo ELO (para saltos de puntuación).
SCORE_SOURCES = {"arena", "design"}


def _groups(payload: dict) -> dict[str, dict[str, dict]]:
    """Agrupa los modelos de una fuente por categoría ('default' si no tiene)."""
    groups: dict[str, dict[str, dict]] = {}
    for m in payload.get("models") or []:
        if not isinstance(m, dict) or not m.get("model"):
            continue
        key = m.get("category") or "default"
        groups.setdefault(key, {})[m["model"]] = m
    return groups


def _top1(group: dict[str, dict]) -> str | None:
    if not group:
        return None
    return min(group.values(), key=lambda m: m.get("rank", 10**6))["model"]


def detect_changes(old: dict | None, new: dict) -> list[dict]:
    """Compara dos snapshots estructurados y devuelve eventos ordenados por importancia.

    Un snapshot legado (texto crudo) u ``old=None`` no genera eventos: es la
    línea base estructurada.
    """
    if is_legacy_snapshot(old) or is_legacy_snapshot(new):
        return []

    events: list[dict] = []
    old_sources = old.get("sources", {})
    new_sources = new.get("sources", {})

    for source, payload in new_sources.items():
        if not isinstance(payload, dict) or payload.get("status") != "ok":
            continue
        old_payload = old_sources.get(source)
        if not isinstance(old_payload, dict) or old_payload.get("status") != "ok":
            continue  # primera vez sana de esta fuente: sin comparación posible

        if source in ("github", "hackernews"):
            events.extend(_diff_items(source, old_payload, payload))
            continue

        events.extend(_diff_models(source, old_payload, payload))

    # Orden: top-1 primero, luego nuevos, luego movimientos; recorte a MAX_EVENTS.
    priority = {"top1": 0, "new": 1, "accel": 2, "move": 3, "score": 4}
    events.sort(key=lambda e: priority.get(e["kind"], 9))
    return events[:MAX_EVENTS]


def _diff_models(source: str, old_payload: dict, new_payload: dict) -> list[dict]:
    events: list[dict] = []
    old_groups = _groups(old_payload)
    new_groups = _groups(new_payload)

    for category, new_group in new_groups.items():
        old_group = old_groups.get(category) or {}
        if not old_group:
            continue
        label = f"{source}" if category == "default" else f"{source} · {category}"

        # 1. Cambio de top-1
        old_t1, new_t1 = _top1(old_group), _top1(new_group)
        if old_t1 and new_t1 and old_t1 != new_t1:
            events.append(
                {
                    "kind": "top1",
                    "source": source,
                    "text": f"🥇 <b>¡Nuevo nº1 en {label}!</b>\n<b>{new_t1}</b> desbancó a {old_t1}.",
                },
            )

        # 2. Modelos nuevos en el top-10
        for model, entry in new_group.items():
            rank = entry.get("rank", 999)
            if model not in old_group and rank <= 10:
                events.append(
                    {
                        "kind": "new",
                        "source": source,
                        "text": f"🆕 <b>Modelo nuevo en {label}:</b> {model} entra directo al puesto {rank}.",
                    },
                )

        # 3. Movimientos grandes y saltos de score
        for model, entry in new_group.items():
            old_entry = old_group.get(model)
            if not old_entry:
                continue
            delta = old_entry.get("rank", 0) - entry.get("rank", 0)
            if abs(delta) >= MOVE_THRESHOLD and delta != 0:
                arrow = "📈" if delta > 0 else "📉"
                events.append(
                    {
                        "kind": "move",
                        "source": source,
                        "text": (
                            f"{arrow} <b>{model}</b> en {label}: "
                            f"puesto {old_entry.get('rank')} → {entry.get('rank')} "
                            f"({'sube' if delta > 0 else 'baja'} {abs(delta)})"
                        ),
                    },
                )
            elif source in SCORE_SOURCES and old_entry.get("score") and entry.get("score"):
                jump = entry["score"] - old_entry["score"]
                if abs(jump) >= SCORE_JUMP:
                    arrow = "🚀" if jump > 0 else "⛰"
                    events.append(
                        {
                            "kind": "score",
                            "source": source,
                            "text": f"{arrow} <b>{model}</b> en {label}: score {old_entry['score']} → {entry['score']} ({jump:+d}).",
                        },
                    )

    return events


def _diff_items(source: str, old_payload: dict, new_payload: dict) -> list[dict]:
    """Diffs para fuentes de elementos (github): repos nuevos y en aceleración."""
    if source != "github":
        return []
    events: list[dict] = []
    old_items = {i.get("full_name"): i for i in (old_payload.get("items") or []) if isinstance(i, dict)}
    for item in new_payload.get("items") or []:
        if not isinstance(item, dict):
            continue
        name = item.get("full_name")
        stars = item.get("stars", 0)
        if name in old_items:
            delta = stars - (old_items[name].get("stars") or 0)
            if delta >= REPO_ACCEL_STARS:
                events.append(
                    {
                        "kind": "accel",
                        "source": source,
                        "text": f"🔥 <b>{name}</b> acelera en GitHub: +{delta:,} estrellas (total {stars:,}).".replace(",", "."),
                    },
                )
        elif stars >= NEW_REPO_MIN_STARS:
            events.append(
                {
                    "kind": "new",
                    "source": source,
                    "text": f"⭐ <b>Repositorio nuevo en tendencias:</b> {name} ({stars:,} estrellas).".replace(",", "."),
                },
            )
    return events


def render_alert(events: list[dict]) -> str:
    """Plantilla HTML (Telegram-safe) del boletín de cambios. Fallback sin Gemini."""
    if not events:
        return ""
    body = "\n\n".join(e["text"] for e in events)
    return f"🚨 <b>¡ALERTA DE CAMBIO EN EL RANKING!</b>\n\n{body}"


def change_summary(events: list[dict]) -> str:
    """Resumen de una línea para la tabla ``changes`` de SQLite."""
    parts = []
    for e in events:
        # texto plano: quitar etiquetas HTML y colapsar espacios
        plain = e["text"].replace("<b>", "").replace("</b>", "").replace("\n", " ")
        parts.append(plain[:120])
    return " | ".join(parts)[:800]
