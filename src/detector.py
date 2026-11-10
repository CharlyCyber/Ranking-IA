"""Diff determinista entre snapshots estructurados. Cero llamadas a LLM.

Detecta: cambio de top-1, modelos nuevos, movimientos de ≥3 posiciones,
saltos de score ≥30 puntos y repos de GitHub en aceleración.

Devuelve eventos con ``text`` ya redactado (HTML seguro de Telegram, escapado);
el updater los une en un boletín. Solo si hay eventos se invoca el LLM (para
pulir el boletín) y siempre con plantilla de fallback.
"""

from src.html_utils import escape_html, strip_html
from src.validator import is_legacy_snapshot

MOVE_THRESHOLD = 3
SCORE_JUMP = 15
NEW_REPO_MIN_STARS = 200
REPO_ACCEL_STARS = 150
HF_MIN_LIKES = 100
MAX_EVENTS = 10

SCORE_SOURCES = {"arena", "design"}
CODING_CATEGORIES = {"code", "coding", "webdev", "image-to-webdev"}


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


def detect_changes(
    old: dict | None,
    new: dict,
    star_baselines: dict[str, int] | None = None,
) -> list[dict]:
    """Compara dos snapshots estructurados y devuelve eventos ordenados por importancia."""
    if is_legacy_snapshot(old) or is_legacy_snapshot(new):
        return []

    events: list[dict] = []
    old_sources = old.get("sources", {})
    new_sources = new.get("sources", {})

    for source, payload in new_sources.items():
        if not isinstance(payload, dict) or payload.get("status") != "ok":
            continue
        old_payload = old_sources.get(source)
        if not isinstance(old_payload, dict) or old_payload.get("status") not in ("ok", "stale"):
            continue

        if source in ("github", "hackernews", "huggingface"):
            events.extend(_diff_items(source, old_payload, payload, star_baselines=star_baselines))
            continue

        events.extend(_diff_models(source, old_payload, payload))

    priority = {"top1": 0, "podium": 1, "new": 2, "accel": 3, "move": 4, "score": 5}
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
        raw_label = f"{source}" if category == "default" else f"{source} · {category}"
        label = escape_html(raw_label)

        # 1. Cambio de top-1
        old_t1, new_t1 = _top1(old_group), _top1(new_group)
        if old_t1 and new_t1 and old_t1 != new_t1:
            events.append(
                {
                    "kind": "top1",
                    "source": source,
                    "category": category,
                    "text": (
                        f"🥇 <b>¡Nuevo nº1 en {label}!</b>\n"
                        f"<b>{escape_html(new_t1)}</b> desbancó a {escape_html(old_t1)}."
                    ),
                },
            )

        # 2. Modelos nuevos en el top-10 (o entrando desde #11+)
        for model, entry in new_group.items():
            rank = entry.get("rank", 999)
            old_entry = old_group.get(model)
            if not old_entry and rank <= 10:
                events.append(
                    {
                        "kind": "new",
                        "source": source,
                        "category": category,
                        "text": f"🆕 <b>Modelo nuevo en {label}:</b> {escape_html(model)} entra directo al puesto {rank}.",
                    },
                )
            elif (
                old_entry
                and old_entry.get("rank", 999) > 10
                and rank <= 10
                and abs(old_entry.get("rank", 0) - rank) < MOVE_THRESHOLD
            ):
                events.append(
                    {
                        "kind": "new",
                        "source": source,
                        "category": category,
                        "text": (
                            f"🆕 <b>{escape_html(model)}</b> entra al Top 10 de {label} "
                            f"(puesto {old_entry.get('rank')} → {rank})."
                        ),
                    },
                )

        # 3. Subidas al podio Top-3, movimientos grandes y saltos de score
        for model, entry in new_group.items():
            old_entry = old_group.get(model)
            if not old_entry:
                continue
            old_rank = old_entry.get("rank", 0)
            new_rank = entry.get("rank", 0)
            delta = old_rank - new_rank
            if abs(delta) >= MOVE_THRESHOLD and delta != 0:
                arrow = "📈" if delta > 0 else "📉"
                events.append(
                    {
                        "kind": "move",
                        "source": source,
                        "category": category,
                        "text": (
                            f"{arrow} <b>{escape_html(model)}</b> en {label}: "
                            f"puesto {old_rank} → {new_rank} "
                            f"({'sube' if delta > 0 else 'baja'} {abs(delta)})"
                        ),
                    },
                )
            elif new_rank in (2, 3) and old_rank > new_rank and delta > 0:
                events.append(
                    {
                        "kind": "podium",
                        "source": source,
                        "category": category,
                        "text": (
                            f"🏅 <b>{escape_html(model)}</b> sube en el podio de {label}: "
                            f"puesto {old_rank} → {new_rank}."
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
                            "category": category,
                            "text": (
                                f"{arrow} <b>{escape_html(model)}</b> en {label}: "
                                f"score {old_entry['score']} → {entry['score']} ({jump:+d})."
                            ),
                        },
                    )

    if source == "openrouter":
        old_free = old_payload.get("free_models")
        new_free = new_payload.get("free_models")
        if isinstance(old_free, list) and old_free and isinstance(new_free, list) and new_free:
            old_ids = {m.get("id") or m.get("name") for m in old_free if isinstance(m, dict)}
            for fm in new_free[:5]:
                if not isinstance(fm, dict):
                    continue
                fid = fm.get("id") or fm.get("name")
                if fid and fid not in old_ids and int(fm.get("context_length") or 0) >= 64_000:
                    name = escape_html(fm.get("name") or fid)
                    ctx_k = int(fm["context_length"]) // 1000
                    events.append(
                        {
                            "kind": "new",
                            "source": "openrouter",
                            "category": "free",
                            "text": f"🆓 <b>Nuevo modelo API gratis ($0):</b> {name} ({ctx_k}K contexto).",
                        }
                    )

    return events


def _diff_items(
    source: str,
    old_payload: dict,
    new_payload: dict,
    star_baselines: dict[str, int] | None = None,
) -> list[dict]:
    """Diffs para fuentes de elementos (github y huggingface)."""
    if source == "huggingface":
        old_items = [i for i in (old_payload.get("items") or []) if isinstance(i, dict) and i.get("id")]
        new_items = [i for i in (new_payload.get("items") or []) if isinstance(i, dict) and i.get("id")]
        if not old_items or not new_items:
            return []
        old_top = old_items[0]["id"]
        new_top_item = new_items[0]
        new_top = new_top_item["id"]
        likes = int(new_top_item.get("likes") or 0)
        if new_top != old_top and likes >= HF_MIN_LIKES:
            return [
                {
                    "kind": "top1",
                    "source": "huggingface",
                    "category": "huggingface",
                    "text": (
                        f"🤗 <b>Nuevo nº1 en tendencia Open-Weights (Hugging Face):</b> "
                        f"{escape_html(new_top)} ({likes:,}❤️).".replace(",", ".")
                    ),
                }
            ]
        return []

    if source != "github":
        return []
    events: list[dict] = []
    old_items = {i.get("full_name"): i for i in (old_payload.get("items") or []) if isinstance(i, dict)}
    baselines = star_baselines or {}

    for item in new_payload.get("items") or []:
        if not isinstance(item, dict):
            continue
        name = item.get("full_name")
        if not name:
            continue
        safe_name = escape_html(name)
        stars = int(item.get("stars") or 0)
        if name in old_items or name in baselines:
            prev_stars = int((old_items.get(name) or {}).get("stars") or stars)
            if name in baselines:
                prev_stars = min(prev_stars, int(baselines[name]))
            delta = stars - prev_stars
            if delta >= REPO_ACCEL_STARS:
                events.append(
                    {
                        "kind": "accel",
                        "source": source,
                        "category": "github",
                        "text": f"🔥 <b>{safe_name}</b> acelera en GitHub: +{delta:,} estrellas (total {stars:,}).".replace(
                            ",", "."
                        ),
                    },
                )
        elif stars >= NEW_REPO_MIN_STARS:
            events.append(
                {
                    "kind": "new",
                    "source": source,
                    "category": "github",
                    "text": f"⭐ <b>Repositorio nuevo en tendencias:</b> {safe_name} ({stars:,} estrellas).".replace(
                        ",", "."
                    ),
                },
            )
    return events


def filter_events_for_preference(events: list[dict], alert_pref: str) -> list[dict]:
    """Filtra la lista de eventos según la preferencia de alertas del suscriptor."""
    pref = (alert_pref or "all").lower()
    if pref == "none":
        return []
    if pref == "top1":
        return [e for e in events if e.get("kind") == "top1"]
    if pref == "coding":
        return [
            e
            for e in events
            if str(e.get("category") or "").lower() in CODING_CATEGORIES or e.get("source") == "design"
        ]
    return events


def render_alert(events: list[dict]) -> str:
    """Plantilla HTML (Telegram-safe) del boletín de cambios. Fallback sin LLM."""
    if not events:
        return ""
    body = "\n\n".join(e["text"] for e in events)
    return f"🚨 <b>¡ALERTA DE CAMBIO EN EL RANKING!</b>\n\n{body}"


def change_summary(events: list[dict]) -> str:
    """Resumen de una línea para la tabla ``changes`` de SQLite."""
    parts = []
    for e in events:
        plain = " ".join(strip_html(e["text"]).split())
        parts.append(plain[:120])
    return " | ".join(parts)[:800]
