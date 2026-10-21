"""Parsers que convierten texto/JSON crudo de las fuentes en entradas estructuradas.

Esquema normalizado por fuente:

    {"models": [{"rank": 1, "model": "claude-opus-4-6", "score": 1496, "votes": 2829, ...}, ...]}

Todos los parsers son deterministas (regex puro, sin LLM) y lanzan ``ParseError``
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


def parse_arena(text: str) -> list[dict]:
    """Parsea las tablas del leaderboard de LMSYS/Arena (Text, Code, Vision...).

    Cada tabla viene precedida por su categoría con fecha, p. ej.::

        'Text\\n2 days ago\\nView\\nRank\\n\\t\\nModel\\n\\t\\nScore\\n\\t\\nVotes\\n\\n1\\t\\nclaude-opus-4-6\\n\\t1496\\t2,829\\n...'

    Devuelve una lista plana de entradas con ``category``; se detecta el final
    de cada tabla porque el rank deja de crecer.
    """
    if not text:
        raise ParseError("texto vacío de Arena")

    header = re.compile(r"\nRank\n\t\nModel\n\t\nScore\n\t\nVotes\n")
    category = re.compile(r"\n([A-Za-z][\w\- ]{0,30}?)\n\d+ (?:days?|hours?) ago\nView\nRank\n")
    row = re.compile(
        r"(\d{1,3})[ \t]*\n[ \t]*([^\n]+?)[ \t]*\n[ \t\n]*(\d{3,4})[\t \n]+([\d,]+)",
    )

    headers = [(m.start(), m.end()) for m in header.finditer(text)]
    if not headers:
        raise ParseError("Arena: no se encontró ninguna tabla de rankings")

    # Etiqueta de categoría de cada cabecera: la última ocurrencia anterior a ella.
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

    if len(models) < 3:
        raise ParseError(f"Arena: solo {len(models)} filas parseadas")
    return models


def parse_openrouter_page(text: str) -> list[dict]:
    """Parsea la sección 'LLM Leaderboard → This Week' de openrouter.ai/rankings.

    Formato observado::

        '1.\\nKimi K2.5\\nby\\nmoonshotai\\n1.16T tokens\\n222%\\n2.\\n...'
    """
    if not text:
        raise ParseError("texto vacío de OpenRouter")

    row = re.compile(
        r"(\d{1,3})\.\s*\n\s*(.+?)\s*\nby\n\s*(.+?)\s*\n([\d.,]+)\s*([KMBT])?\s*tokens?\s*\n\s*(\d+)%",
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
        models.append(
            {
                "rank": rank,
                "model": m.group(2),
                "author": m.group(3),
                "tokens_week": tokens,
                "growth_pct": int(m.group(6)),
            },
        )
        seen_ranks.add(rank)

    if len(models) < 3:
        raise ParseError(f"OpenRouter: solo {len(models)} filas parseadas")
    return models


def parse_openrouter_api(payload) -> list[dict]:
    """Parsea la respuesta JSON de la Data API de OpenRouter (ranking por uso de tokens).

    Tolerante a variantes de esquema: acepta una lista directa o un dict con
    ``data``/``rankings``/``models``, y claves ``rank``/``position``, ``tokens``
    /``token_count``/``total_tokens``.
    """
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


def parse_design_arena(text: str) -> list[dict]:
    """Parsea el leaderboard de designarena.ai.

    Prefiere la tabla completa (``#1\\nModelo\\nElo\\n...``); si no está, usa la
    tarjeta "Top 20" (líneas de score Elo con el modelo en el bloque siguiente).
    """
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
        block = re.split(r"Last updated|\nRank\nModel\n", block, maxsplit=1)[0]
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
                "full_name": item["full_name"],
                "stars": int(item.get("stargazers_count") or 0),
                "language": item.get("language") or "",
                "description": (item.get("description") or "")[:280],
                "url": item.get("html_url") or f"https://github.com/{item['full_name']}",
                "created_at": item.get("created_at") or "",
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
                "title": hit["title"],
                "points": int(hit.get("points") or 0),
                "comments": int(hit.get("num_comments") or 0),
                "url": hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}",
            },
        )
    if not stories:
        raise ParseError("HN devolvió 0 historias")
    return stories


def parse_json(text: str):
    """Parser genérico de JSON con mensaje de error claro."""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ParseError(f"JSON inválido: {exc}") from exc
