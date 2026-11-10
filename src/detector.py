"""Diff determinista entre snapshots estructurados. Cero llamadas a LLM.

Detecta: cambio de top-1, modelos nuevos, movimientos de ≥3 posiciones,
saltos de score ≥30 puntos y repos de GitHub en aceleración.

Devuelve eventos con ``text`` ya redactado (HTML seguro de Telegram, escapado);
el updater los une en un boletín. Solo si hay eventos se invoca el LLM (para
pulir el boletín) y siempre con plantilla de fallback.
"""

import re

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


def canonical_model_key(name: str) -> str:
    """Normaliza variantes de formato entre fuentes preservando sufijos como (High)/(Max).

    Ej.: ``anthropic/claude-opus-4-6`` y ``Claude Opus 4.6`` → ``claudeopus46``,
    mientras ``Claude Opus 5 (Max)`` (``claudeopus5max``) ≠ ``Claude Opus 5 (High)``.
    """
    base = (name or "").strip()
    if "/" in base:
        base = base.rsplit("/", 1)[-1]
    elif ": " in base:
        prefix, rest = base.split(": ", 1)
        if len(prefix) <= 24:
            base = rest
    return re.sub(r"[^a-z0-9]+", "", base.lower())


def _groups(payload: dict) -> dict[str, dict[str, dict]]:
    """Agrupa los modelos de una fuente por categoría y clave canónica."""
    groups: dict[str, dict[str, dict]] = {}
    for m in payload.get("models") or []:
        if not isinstance(m, dict) or not m.get("model"):
            continue
        cat = m.get("category") or "default"
        canon = canonical_model_key(str(m["model"])) or str(m["model"])
        bucket = groups.setdefault(cat, {})
        existing = bucket.get(canon)
        if existing is None or m.get("rank", 10**6) < existing.get("rank", 10**6):
            bucket[canon] = m
    return groups


def _top1(group: dict[str, dict]) -> str | None:
    if not group:
        return None
    return min(group.values(), key=lambda m: m.get("rank", 10**6))["model"]


def compact_snapshot_for_state(snapshot: dict | None) -> dict:
    """Reduce un snapshot a campos mínimos de ranking/score para persistir baseline."""
    if not isinstance(snapshot, dict):
        return {"generated_at": "", "sources": {}}
    compact_sources: dict[str, dict] = {}
    for source, payload in (snapshot.get("sources") or {}).items():
        if not isinstance(payload, dict):
            continue
        models = []
        for m in (payload.get("models") or [])[:120]:
            if not isinstance(m, dict) or not m.get("model"):
                continue
            item = {
                "model": m["model"],
                "rank": m.get("rank", 999),
            }
            if m.get("category"):
                item["category"] = m["category"]
            if m.get("score") is not None:
                item["score"] = m["score"]
            models.append(item)
        compact_sources[source] = {
            "status": payload.get("status", "ok"),
            "models": models,
        }
    return {
        "generated_at": snapshot.get("generated_at", ""),
        "sources": compact_sources,
    }


def merge_notified_baseline(
    baseline: dict | None,
    new_snapshot: dict,
    alerted_sources: set[str],
) -> dict:
    """Actualiza en el baseline solo las fuentes que acaban de emitir alerta."""
    compact_new = compact_snapshot_for_state(new_snapshot)
    merged_sources: dict[str, dict] = {}
    old_sources = (baseline or {}).get("sources") or {}
    for source, payload in compact_new.get("sources", {}).items():
        if source in alerted_sources or source not in old_sources:
            merged_sources[source] = payload
        else:
            merged_sources[source] = old_sources[source]
    return {
        "generated_at": compact_new.get("generated_at", ""),
        "sources": merged_sources,
    }


def detect_changes(
    old: dict | None,
    new: dict,
    star_baselines: dict[str, int] | None = None,
    baseline_snapshot: dict | None = None,
) -> list[dict]:
    """Compara dos snapshots estructurados y devuelve eventos ordenados por importancia."""
    if is_legacy_snapshot(old) or is_legacy_snapshot(new):
        return []

    events: list[dict] = []
    old_sources = old.get("sources", {})
    base_sources = (
        baseline_snapshot.get("sources", {})
        if isinstance(baseline_snapshot, dict) and not is_legacy_snapshot(baseline_snapshot)
        else {}
    )
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

        base_payload = base_sources.get(source)
        if not isinstance(base_payload, dict) or base_payload.get("status") not in ("ok", "stale"):
            base_payload = None
        events.extend(_diff_models(source, old_payload, payload, base_payload=base_payload))

    priority = {"top1": 0, "podium": 1, "new": 2, "accel": 3, "move": 4, "score": 5}
    events.sort(key=lambda e: priority.get(e["kind"], 9))
    return events[:MAX_EVENTS]


def _diff_models(
    source: str,
    old_payload: dict,
    new_payload: dict,
    base_payload: dict | None = None,
) -> list[dict]:
    events: list[dict] = []
    old_groups = _groups(old_payload)
    base_groups = _groups(base_payload) if base_payload else {}
    new_groups = _groups(new_payload)

    for category, new_group in new_groups.items():
        old_group = old_groups.get(category) or {}
        if not old_group:
            continue
        base_group = base_groups.get(category) or {}
        raw_label = f"{source}" if category == "default" else f"{source} · {category}"
        label = escape_html(raw_label)

        # 1. Cambio de top-1 (usando clave canónica para ignorar cambios de formato entre fuentes)
        old_t1, new_t1 = _top1(old_group), _top1(new_group)
        if old_t1 and new_t1 and canonical_model_key(old_t1) != canonical_model_key(new_t1):
            events.append(
                {
                    "kind": "top1",
                    "source": source,
                    "category": category,
                    "models": [new_t1, old_t1],
                    "text": (
                        f"🥇 <b>¡Nuevo nº1 en {label}!</b>\n"
                        f"<b>{escape_html(new_t1)}</b> desbancó a {escape_html(old_t1)}."
                    ),
                },
            )

        # 2. Modelos nuevos en el top-10 (o entrando desde #11+)
        handled_moves: set[str] = set()
        for canon, entry in new_group.items():
            model = entry["model"]
            rank = entry.get("rank", 999)
            old_entry = old_group.get(canon)
            base_entry = base_group.get(canon) or old_entry
            if not old_entry and rank <= 10:
                events.append(
                    {
                        "kind": "new",
                        "source": source,
                        "category": category,
                        "models": [model],
                        "text": f"🆕 <b>Modelo nuevo en {label}:</b> {escape_html(model)} entra directo al puesto {rank}.",
                    },
                )
            elif old_entry and old_entry.get("rank", 999) > 10 and rank <= 10:
                base_rank = base_entry.get("rank", old_entry.get("rank", 0))
                if abs(base_rank - rank) < MOVE_THRESHOLD:
                    handled_moves.add(canon)
                    events.append(
                        {
                            "kind": "new",
                            "source": source,
                            "category": category,
                            "models": [model],
                            "text": (
                                f"🆕 <b>{escape_html(model)}</b> entra al Top 10 de {label} "
                                f"(puesto {old_entry.get('rank')} → {rank})."
                            ),
                        },
                    )

        # 3. Subidas al podio Top-3, movimientos grandes (acumulados vs baseline) y saltos de score
        for canon, entry in new_group.items():
            if canon in handled_moves:
                continue
            model = entry["model"]
            old_entry = old_group.get(canon)
            if not old_entry:
                continue
            base_entry = base_group.get(canon) or old_entry
            old_rank = old_entry.get("rank", 0)
            base_rank = base_entry.get("rank", old_rank)
            new_rank = entry.get("rank", 0)
            delta_step = old_rank - new_rank
            delta_base = base_rank - new_rank
            if delta_step != 0 and abs(delta_base) >= MOVE_THRESHOLD:
                arrow = "📈" if delta_base > 0 else "📉"
                events.append(
                    {
                        "kind": "move",
                        "source": source,
                        "category": category,
                        "models": [model],
                        "text": (
                            f"{arrow} <b>{escape_html(model)}</b> en {label}: "
                            f"puesto {base_rank} → {new_rank} "
                            f"({'sube' if delta_base > 0 else 'baja'} {abs(delta_base)})"
                        ),
                    },
                )
            elif new_rank in (2, 3) and old_rank > new_rank and delta_step > 0:
                events.append(
                    {
                        "kind": "podium",
                        "source": source,
                        "category": category,
                        "models": [model],
                        "text": (
                            f"🏅 <b>{escape_html(model)}</b> sube en el podio de {label}: "
                            f"puesto {old_rank} → {new_rank}."
                        ),
                    },
                )
            elif source in SCORE_SOURCES and old_entry.get("score") and entry.get("score"):
                base_score = base_entry.get("score") or old_entry["score"]
                if entry["score"] != old_entry["score"]:
                    jump = entry["score"] - base_score
                    if abs(jump) >= SCORE_JUMP:
                        arrow = "🚀" if jump > 0 else "⛰"
                        events.append(
                            {
                                "kind": "score",
                                "source": source,
                                "category": category,
                                "models": [model],
                                "text": (
                                    f"{arrow} <b>{escape_html(model)}</b> en {label}: "
                                    f"score {base_score} → {entry['score']} ({jump:+d})."
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
                    raw_name = fm.get("name") or fid
                    name = escape_html(raw_name)
                    ctx_k = int(fm["context_length"]) // 1000
                    events.append(
                        {
                            "kind": "new",
                            "source": "openrouter",
                            "category": "free",
                            "models": [str(raw_name), str(fid)],
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
                    "models": [new_top, old_top],
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
                        "models": [name],
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
                    "models": [name],
                    "text": f"⭐ <b>Repositorio nuevo en tendencias:</b> {safe_name} ({stars:,} estrellas).".replace(
                        ",", "."
                    ),
                },
            )
    return events


def event_matches_watch(event: dict, watch_models: list[str]) -> bool:
    """Devuelve True si algún término seguido coincide con los modelos del evento."""
    if not watch_models:
        return False
    candidates = [str(m) for m in (event.get("models") or []) if m]
    if not candidates:
        candidates = [strip_html(event.get("text") or "")]
    cand_lower = [c.lower() for c in candidates]
    cand_canon = [canonical_model_key(c) for c in candidates]
    for raw_term in watch_models:
        term = (raw_term or "").strip().lower()
        if not term:
            continue
        cterm = canonical_model_key(term)
        if any(term in cl for cl in cand_lower):
            return True
        if len(cterm) >= 3 and any(cterm in cc for cc in cand_canon):
            return True
    return False


def filter_events_for_preference(
    events: list[dict],
    alert_pref: str,
    watch_models: list[str] | None = None,
) -> list[dict]:
    """Filtra la lista de eventos según la preferencia de alertas y modelos seguidos."""
    pref = (alert_pref or "all").lower()
    if pref == "none":
        return []
    watched = [w for w in (watch_models or []) if str(w).strip()]
    if pref == "watched":
        return [e for e in events if event_matches_watch(e, watched)]
    if pref == "top1":
        base = [e for e in events if e.get("kind") == "top1"]
    elif pref == "coding":
        base = [
            e
            for e in events
            if str(e.get("category") or "").lower() in CODING_CATEGORIES or e.get("source") == "design"
        ]
    else:
        return events

    if not watched:
        return base
    selected_ids = {id(e) for e in base}
    return [e for e in events if id(e) in selected_ids or event_matches_watch(e, watched)]


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
