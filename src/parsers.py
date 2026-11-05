"""Parsers que convierten texto/JSON crudo de las fuentes en entradas estructuradas.

Esquema normalizado por fuente:

    {"models": [{"rank": 1, "model": "claude-opus-4-6", "score": 1496, "votes": 2829, ...}, ...]}

Todos los parsers son deterministas (regex/JSON puro, sin LLM) y lanzan ``ParseError``
cuando no encuentran filas utilizables, para que la capa de validación decida.
"""

import json
import re


class ParseError(ValueError):
    """El contenido no contiene datos estructurados utilizables."""


def _int(value: str) -> int | None:
    try:
        return int(str(value).replace(",", "").replace(" ", ""))
    except (TypeError, ValueError):
        return None


def _norm_model_key(name: str) -> str:
    """Clave alfanumérica normalizada para cruzar nombres de modelos entre fuentes."""
    base = name.lower().strip()
    if "/" in base:
        base = base.split("/", 1)[1]
    base = re.sub(r":free$", "", base)
    base = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", base)
    return re.sub(r"[^a-z0-9]+", "", base)


def parse_arena(text: str) -> list[dict]:
    """Parsea las tablas del leaderboard de LMSYS/Arena (formatos v3 clásico y tarjetas 2026+)."""
    if not text:
        raise ParseError("texto vacío de Arena")

    legacy = _parse_arena_legacy_tables(text)
    if len(legacy) >= 3:
        return legacy

    modern = _parse_arena_cards(text)
    if len(modern) >= 3:
        return modern

    raise ParseError("Arena: no se encontró ninguna tabla de rankings")


def _parse_arena_legacy_tables(text: str) -> list[dict]:
    """Formato clásico con cabecera explícita Rank / Model / Score / Votes."""
    header = re.compile(r"\nRank\n\t\nModel\n\t\nScore\n\t\nVotes\n")
    category = re.compile(r"\n([A-Za-z][\w\- ]{0,30}?)\n\d+ (?:days?|hours?) ago\nView\nRank\n")
    row = re.compile(
        r"(\d{1,3})[ \t]*\n[ \t]*([^\n]+?)[ \t]*\n[ \t\n]*(\d{3,4})[\t \n]+([\d,]+)",
    )

    headers = [(m.start(), m.end()) for m in header.finditer(text)]
    if not headers:
        return []

    cats = [(m.start(), m.group(1).strip()) for m in category.finditer(text)]
    models: list[dict] = []

    for i, (h_start, h_end) in enumerate(headers):
        seg_end = headers[i + 1][0] if i + 1 < len(headers) else len(text)
        label = None
        for c_pos, c_name in cats:
            if c_pos < h_start:
                label = c_name
            else:
                break

        prev_rank = 0
        for m in row.finditer(text, h_end, seg_end):
            rank = int(m.group(1))
            if rank <= prev_rank:
                break
            score = _int(m.group(3))
            votes = _int(m.group(4))
            if score is None or votes is None or not 500 <= score <= 2500:
                continue
            entry = {"rank": rank, "model": m.group(2).strip(), "score": score, "votes": votes}
            if label:
                entry["category"] = label
            models.append(entry)
            prev_rank = rank

    return models


def _parse_arena_cards(text: str) -> list[dict]:
    """Formato moderno de tarjetas de Arena Overview (con 'View all', intervalos ±CI o +x/-y y %)."""
    card_start = re.compile(
        r"(?:^|\n)([A-Za-z][A-Za-z0-9 \-]{1,34})\n(?:🏆\n[^\n]{1,40}\n)?(?=1\n)",
    )
    row = re.compile(
        r"(?:^|\n)(\d{1,3})[ \t]*\n[ \t]*([^\n]+?)[ \t]*\n[ \t]*(\d{3,4}|\d{1,3}(?:\.\d+)?%)"
        r"(?:[ \t]*\n[ \t]*(?:±[\d.]+%?|\+\d+/-\d+))?",
    )

    starts = list(card_start.finditer(text))
    if not starts:
        return []

    models: list[dict] = []
    for idx, match in enumerate(starts):
        label = match.group(1).strip()
        body_start = match.end()
        next_start = starts[idx + 1].start() if idx + 1 < len(starts) else len(text)
        view_all_pos = text.find("\nView all", body_start, next_start)
        seg_end = view_all_pos if view_all_pos != -1 else next_start
        segment = "\n" + text[body_start:seg_end]

        prev_rank = 0
        for r in row.finditer(segment):
            rank = int(r.group(1))
            if rank <= prev_rank:
                break
            model_name = r.group(2).strip()
            raw_metric = r.group(3).strip()
            entry: dict = {"rank": rank, "model": model_name, "category": label}
            if raw_metric.endswith("%"):
                try:
                    entry["win_rate_pct"] = float(raw_metric[:-1])
                except ValueError:
                    continue
            else:
                score = _int(raw_metric)
                if score is None or not 500 <= score <= 2500:
                    continue
                entry["score"] = score
            models.append(entry)
            prev_rank = rank

    return models


def parse_openrouter_page(text: str) -> list[dict]:
    """Parsea la sección 'LLM Leaderboard → This Week' de openrouter.ai/rankings.

    Soporta crecimientos tipo ``222%``, ``>999%``, ``<1%``, ``-12%`` y decimales.
    """
    if not text:
        raise ParseError("texto vacío de OpenRouter")

    row = re.compile(
        r"(\d{1,3})\.\s*\n\s*(.+?)\s*\nby\n\s*(.+?)\s*\n"
        r"([\d.,]+)\s*([KMBT])?\s*tokens?\s*\n\s*([<>+-]?\s*-?\d+(?:\.\d+)?)%",
        re.IGNORECASE,
    )
    multiplier = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000, "T": 1_000_000_000_000}
    models: list[dict] = []
    seen_ranks: set[int] = set()
    for m in row.finditer(text):
        rank = int(m.group(1))
        if rank in seen_ranks:  # segunda tabla (Market Share, Categories...)
            break
        base = float(m.group(4).replace(",", ""))
        tokens = int(base * multiplier.get((m.group(5) or "").upper(), 1))
        raw_growth = re.sub(r"[<>+\s]", "", m.group(6))
        try:
            growth_pct = int(round(float(raw_growth)))
        except ValueError:
            growth_pct = 0
        models.append(
            {
                "rank": rank,
                "model": m.group(2).strip(),
                "author": m.group(3).strip(),
                "tokens_week": tokens,
                "growth_pct": growth_pct,
            },
        )
        seen_ranks.add(rank)

    if len(models) < 3:
        raise ParseError(f"OpenRouter: solo {len(models)} filas parseadas")
    return models


def parse_openrouter_api(payload) -> list[dict]:
    """Parsea la respuesta JSON de la Data API de OpenRouter (ranking por uso de tokens)."""
    if isinstance(payload, dict):
        rows = next((payload[k] for k in ("data", "rankings", "models") if isinstance(payload.get(k), list)), None)
    elif isinstance(payload, list):
        rows = payload
    else:
        rows = None

    if not rows:
        raise ParseError("respuesta de la Data API de OpenRouter sin lista de modelos")

    models: list[dict] = []
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        model = row.get("model") or row.get("name") or row.get("id")
        if not model:
            continue
        tokens = row.get("tokens") or row.get("token_count") or row.get("total_tokens") or 0
        try:
            tokens = int(tokens)
        except (TypeError, ValueError):
            tokens = 0
        models.append(
            {
                "rank": int(row.get("rank") or row.get("position") or i + 1),
                "model": str(model),
                "author": str(row.get("author") or row.get("provider") or ""),
                "tokens_week": tokens,
                "growth_pct": int(row.get("growth_pct") or row.get("growth") or 0),
            },
        )

    if len(models) < 3:
        raise ParseError(f"OpenRouter API: solo {len(models)} filas parseadas")
    return models


def parse_openrouter_catalog(payload: dict) -> dict:
    """Parsea el catálogo público gratuito ``GET /api/v1/models`` de OpenRouter.

    Devuelve precios por 1M de tokens, longitud de contexto, modelos ``:free``
    y lanzamientos recientes.
    """
    items = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(items, list) or not items:
        raise ParseError("Catálogo de OpenRouter vacío o inválido")

    by_key: dict[str, dict] = {}
    free_models: list[dict] = []
    all_parsed: list[dict] = []

    for item in items:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        model_id = str(item["id"])
        name = str(item.get("name") or model_id)
        pricing = item.get("pricing") or {}
        try:
            prompt_per_m = round(float(pricing.get("prompt", 0) or 0) * 1_000_000, 4)
            completion_per_m = round(float(pricing.get("completion", 0) or 0) * 1_000_000, 4)
        except (TypeError, ValueError):
            continue
        if prompt_per_m < 0 or completion_per_m < 0:
            continue

        ctx = int(item.get("context_length") or 0)
        created = int(item.get("created") or 0)
        is_free = model_id.endswith(":free") or (prompt_per_m == 0.0 and completion_per_m == 0.0)

        info = {
            "id": model_id,
            "name": name,
            "prompt_per_m": prompt_per_m,
            "completion_per_m": completion_per_m,
            "context_length": ctx,
            "is_free": is_free,
            "created": created,
        }
        all_parsed.append(info)
        for candidate in (model_id, name):
            k = _norm_model_key(candidate)
            if k and (k not in by_key or (not is_free and by_key[k]["is_free"])):
                by_key[k] = info

        if is_free and ctx >= 32_000:
            free_models.append(info)

    # Ordenar modelos gratuitos por ventana de contexto y recencia
    free_models.sort(key=lambda m: (m["context_length"], m["created"]), reverse=True)
    recent_models = sorted(all_parsed, key=lambda m: m["created"], reverse=True)[:10]

    return {
        "by_key": by_key,
        "free_models": free_models[:15],
        "recent_models": recent_models,
    }


def enrich_models_with_catalog(models: list[dict], catalog: dict | None) -> list[dict]:
    """Cruza entradas de ranking con precios y contexto del catálogo de OpenRouter."""
    if not catalog or not isinstance(catalog.get("by_key"), dict):
        return models
    by_key = catalog["by_key"]
    for entry in models:
        if not isinstance(entry, dict) or not entry.get("model"):
            continue
        key = _norm_model_key(entry["model"])
        hit = by_key.get(key)
        if not hit:
            continue
        entry.setdefault("prompt_per_m", hit["prompt_per_m"])
        entry.setdefault("completion_per_m", hit["completion_per_m"])
        entry.setdefault("context_length", hit["context_length"])
        entry.setdefault("is_free", hit["is_free"])
    return models


def parse_design_arena(text: str) -> list[dict]:
    """Parsea el leaderboard de designarena.ai."""
    if not text:
        raise ParseError("texto vacío de Design Arena")

    # Formato 1: tabla completa con win rate y battles.
    models: list[dict] = []
    full = re.compile(r"#(\d{1,3})\n([^\n]+?)\n(1[0-5]\d{2})\n[^\n]+\n(\d{1,3}(?:\.\d+)?)%")
    for m in full.finditer(text):
        models.append(
            {
                "rank": int(m.group(1)),
                "model": m.group(2).strip(),
                "score": int(m.group(3)),
                "win_rate_pct": float(m.group(4)),
            },
        )
    if len(models) >= 3:
        return _dedupe_ranks(models)

    # Formato 2 (fallback): tarjeta Top-20 — bloques entre líneas de score.
    models = []
    scores = list(re.finditer(r"\n(1[0-5]\d{2})\n", text))
    for i, m in enumerate(scores):
        start = m.end()
        end = scores[i + 1].start() if i + 1 < len(scores) else len(text)
        block = text[start:end]
        block = re.split(r"\n#\d+\b|\nFAQ\b|Last updated|\nRank\nModel\n", block, maxsplit=1)[0]
        block = re.sub(r"\s+", " ", block).strip()
        if not block or len(block) > 70 or block.isdigit():
            continue
        models.append({"rank": len(models) + 1, "model": block, "score": int(m.group(1))})

    if len(models) < 3:
        raise ParseError(f"Design Arena: solo {len(models)} filas parseadas")
    return models


def _dedupe_ranks(models: list[dict]) -> list[dict]:
    """Conserva el primer modelo por rank y renumera posiciones correlativamente."""
    seen: set[int] = set()
    out = []
    for m in sorted(models, key=lambda x: x["rank"]):
        if m["rank"] in seen:
            continue
        seen.add(m["rank"])
        m["rank"] = len(out) + 1
        out.append(m)
    return out


def parse_github_search(payload: dict) -> list[dict]:
    """Parsea la respuesta de la GitHub Search API (repos ordenados por estrellas)."""
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise ParseError("respuesta de GitHub Search sin items")
    repos = []
    for item in items:
        if not isinstance(item, dict) or not item.get("full_name"):
            continue
        repos.append(
            {
                "full_name": str(item["full_name"]),
                "stars": int(item.get("stargazers_count") or 0),
                "language": str(item.get("language") or ""),
                "description": str(item.get("description") or "")[:280],
                "url": str(item.get("html_url") or f"https://github.com/{item['full_name']}"),
                "created_at": str(item.get("created_at") or ""),
            },
        )
    if not repos:
        raise ParseError("GitHub Search devolvió 0 repositorios")
    return repos


def parse_hn_stories(payload: dict) -> list[dict]:
    """Parsea la respuesta de la Algolia HN API (hits con title/points/num_comments)."""
    hits = payload.get("hits") if isinstance(payload, dict) else None
    if not isinstance(hits, list):
        raise ParseError("respuesta de HN sin hits")
    stories = []
    for hit in hits:
        if not isinstance(hit, dict) or not hit.get("title"):
            continue
        stories.append(
            {
                "title": str(hit["title"]),
                "points": int(hit.get("points") or 0),
                "comments": int(hit.get("num_comments") or 0),
                "url": str(hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}"),
            },
        )
    if not stories:
        raise ParseError("HN devolvió 0 historias")
    return stories


def parse_hf_trending(payload) -> list[dict]:
    """Parsea modelos open-weights en tendencia de la API pública de Hugging Face."""
    if isinstance(payload, dict):
        raw_items = payload.get("recentlyTrending") or payload.get("models") or []
    elif isinstance(payload, list):
        raw_items = payload
    else:
        raw_items = []

    if not isinstance(raw_items, list):
        raise ParseError("respuesta de Hugging Face sin lista de modelos")

    models: list[dict] = []
    for entry in raw_items:
        if not isinstance(entry, dict):
            continue
        repo = entry.get("repoData") if isinstance(entry.get("repoData"), dict) else entry
        repo_id = repo.get("id") or repo.get("modelId")
        if not repo_id:
            continue
        models.append(
            {
                "id": str(repo_id),
                "likes": int(repo.get("likes") or 0),
                "downloads": int(repo.get("downloads") or 0),
                "pipeline_tag": str(repo.get("pipeline_tag") or ""),
                "url": f"https://huggingface.co/{repo_id}",
            },
        )

    if not models:
        raise ParseError("Hugging Face Trending devolvió 0 modelos")
    return models


def parse_json(text: str):
    """Parser genérico de JSON con mensaje de error claro."""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ParseError(f"JSON inválido: {exc}") from exc
