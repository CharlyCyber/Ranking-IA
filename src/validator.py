"""Safety net: valida cada fuente ANTES de persistir para no arruinar el histórico.

Reglas por fuente; una fuente que no cumple se marca como inválida y el updater
conserva los datos anteriores de esa fuente en lugar de sobrescribirlos.
"""

from src.parsers import ParseError, parse_json

# Mínimo de entradas esperadas por fuente para considerarla sana.
MIN_MODELS = {
    "arena": 10,
    "openrouter": 5,
    "design": 5,
}

MODEL_KEYS_REQUIRED = ("rank", "model")


def validate_models(source: str, models) -> list[str]:
    """Devuelve la lista de errores de validación (vacía = fuente sana)."""
    errors: list[str] = []

    if not isinstance(models, list):
        return [f"{source}: los datos no son una lista de modelos ({type(models).__name__})"]

    min_items = MIN_MODELS.get(source, 3)
    if len(models) < min_items:
        errors.append(f"{source}: solo {len(models)} modelos (mínimo {min_items})")

    for entry in models[:200]:
        if not isinstance(entry, dict):
            errors.append(f"{source}: entrada no dict: {entry!r:.80}")
            continue
        for key in MODEL_KEYS_REQUIRED:
            if key not in entry:
                errors.append(f"{source}: falta la clave '{key}' en {str(entry)[:80]}")
        rank = entry.get("rank")
        if not isinstance(rank, int) or rank < 1:
            errors.append(f"{source}: rank inválido {rank!r}")
        model = entry.get("model")
        if not isinstance(model, str) or not model.strip() or len(model) > 120:
            errors.append(f"{source}: nombre de modelo inválido {model!r:.60}")
        score = entry.get("score")
        if score is not None and (not isinstance(score, int) or score < 0):
            errors.append(f"{source}: score inválido {score!r}")

    return errors


def validate_source(source: str, payload) -> list[str]:
    """Valida la estructura completa de una fuente capturada (dict con status/models o items)."""
    if not isinstance(payload, dict):
        return [f"{source}: payload no es un dict ({type(payload).__name__})"]
    if source in ("github", "hackernews"):
        return _validate_items(source, payload)
    if payload.get("status") != "ok":
        return [f"{source}: status={payload.get('status')} ({payload.get('error', 'sin detalle')})"]
    return validate_models(source, payload.get("models"))


def validate_snapshot(snapshot: dict) -> dict[str, list[str]]:
    """Valida todas las fuentes de un snapshot estructurado. Devuelve {fuente: [errores]}."""
    problems: dict[str, list[str]] = {}
    if not isinstance(snapshot, dict):
        return {"snapshot": [f"snapshot no es un dict ({type(snapshot).__name__})"]}
    sources = snapshot.get("sources")
    if not isinstance(sources, dict) or not sources:
        return {"sources": ["snapshot sin fuentes"]}
    for source, payload in sources.items():
        if source in ("github", "hackernews"):
            errors = _validate_items(source, payload)
        else:
            errors = validate_source(source, payload)
        if errors:
            problems[source] = errors
    return problems


def _validate_items(source: str, payload) -> list[str]:
    """Valida fuentes de listas genéricas (github, hackernews)."""
    if not isinstance(payload, dict):
        return [f"{source}: payload no es un dict"]
    if payload.get("status") != "ok":
        return [f"{source}: status={payload.get('status')} ({payload.get('error', 'sin detalle')})"]
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        return [f"{source}: sin elementos"]
    return []


def is_legacy_snapshot(snapshot: dict | None) -> bool:
    """¿El snapshot es el formato antiguo de texto crudo (pre-v3)?

    Los snapshots legados no tienen ``sources`` estructurados; se tratan como
    línea base y NO generan alertas al primer vuelo estructurado.
    """
    if not snapshot:
        return True
    sources = snapshot.get("sources")
    return not isinstance(sources, dict) or not sources


def safe_load_json(text: str):
    """Carga JSON tolerando el formato legado (string crudo) sin lanzar."""
    try:
        return parse_json(text)
    except ParseError:
        return None
