"""Tendencias diarias de GitHub y Hacker News con APIs oficiales (sin scraping).

- GitHub Search API: repos nuevos con más estrellas (el equivalente determinista
  de la página github.com/trending, que no tiene API oficial).
- Algolia HN API: historias recientes con más puntos.

Los deltas de estrellas reales se calculan en ``detector`` con el historial que
guarda ``db.save_repo_stars``.
"""

import logging
from datetime import datetime, timedelta, timezone

import httpx

from src.config import GITHUB_TOKEN
from src.parsers import ParseError, parse_github_search, parse_hn_stories

logger = logging.getLogger(__name__)

GITHUB_SEARCH_URL = "https://api.github.com/search/repositories"
HN_SEARCH_URL = "https://hn.algolia.com/api/v1/search"
HN_FB_TOP_URL = "https://hacker-news.firebaseio.com/v0/topstories.json"
HN_FB_ITEM_URL = "https://hacker-news.firebaseio.com/v0/item/{}.json"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# Palabras que marcatildes un repo como relevante para el público de este bot (IA/dev).
AI_KEYWORDS = (
    "ai", "llm", "gpt", "claude", "gemini", "agent", "ml", "machine-learning",
    "deep-learning", "neural", "rag", "inference", "model", "transformer", "diffusion",
)


class TrendingError(Exception):
    """No se pudieron obtener las tendencias."""


async def fetch_github_trending(days: int = 1, per_query: int = 15) -> dict:
    """Repos creados recientemente con más estrellas, separados en AI y generales.

    Se hacen dos consultas (``topic:ai`` y general) para poder presentar las
    tendencias de IA por separado de las generales.
    """
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    headers = {"Accept": "application/vnd.github+json", "User-Agent": UA}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"

    async with httpx.AsyncClient(headers=headers, timeout=20, follow_redirects=True) as client:
        try:
            ai_resp = await client.get(
                GITHUB_SEARCH_URL,
                params={"q": f"topic:ai created:>{since} stars:>10", "sort": "stars", "order": "desc", "per_page": per_query},
            )
            general_resp = await client.get(
                GITHUB_SEARCH_URL,
                params={"q": f"created:>{since} stars:>30", "sort": "stars", "order": "desc", "per_page": per_query},
            )
        except httpx.HTTPError as exc:
            raise TrendingError(f"GitHub API no disponible: {exc}") from exc

    if ai_resp.status_code == 403:
        logger.warning("GitHub Search rate limit (403) en la consulta AI; se continúa solo con general.")
    try:
        ai_repos = parse_github_search(ai_resp.json()) if ai_resp.status_code == 200 else []
    except (ParseError, ValueError):
        ai_repos = []
    try:
        general_repos = parse_github_search(general_resp.json()) if general_resp.status_code == 200 else []
    except (ParseError, ValueError) as exc:
        if not ai_repos:
            raise TrendingError(f"GitHub Search falló: {exc}") from exc
        general_repos = []

    # La sección general excluye los que ya están en AI y prioriza keywords de IA.
    ai_names = {r["full_name"] for r in ai_repos}
    general_repos = [r for r in general_repos if r["full_name"] not in ai_names]

    if not ai_repos and not general_repos:
        raise TrendingError(
            f"GitHub Search: AI={ai_resp.status_code}, general={general_resp.status_code} sin resultados"
        )

    # Un solo conjunto para el historial de estrellas (sin duplicados).
    merged: dict[str, dict] = {}
    for r in ai_repos[:10] + general_repos[:15]:
        merged.setdefault(r["full_name"], r)

    return {
        "status": "ok",
        "since": since,
        "ai": ai_repos[:10],
        "general": general_repos[:10],
        "items": list(merged.values()),
    }


async def fetch_hackernews(top_n: int = 8, min_points: int = 80) -> dict:
    """Historias de HN de las últimas 24 h ordenadas por puntos.

    Primero la API de Algolia (1 petición); si está bloqueada, la API oficial
    de HN en Firebase (topstories + detalle de cada item).
    """
    try:
        return await _fetch_hn_algolia(top_n, min_points)
    except TrendingError as exc:
        logger.warning("Algolia HN no disponible (%s); usando API oficial de HN.", exc)
        return await _fetch_hn_firebase(top_n, min_points)


async def _fetch_hn_algolia(top_n: int, min_points: int) -> dict:
    params = {
        "tags": "story",
        "numericFilters": f"points>{min_points}",
        "hitsPerPage": top_n * 2,
    }
    try:
        async with httpx.AsyncClient(headers={"User-Agent": UA}, timeout=15) as client:
            resp = await client.get(HN_SEARCH_URL, params=params)
            resp.raise_for_status()
            payload = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise TrendingError(f"HN API no disponible: {exc}") from exc

    try:
        stories = parse_hn_stories(payload)
    except ParseError as exc:
        raise TrendingError(str(exc)) from exc

    return {"status": "ok", "items": stories[:top_n]}


async def _fetch_hn_firebase(top_n: int, min_points: int) -> dict:
    """Fallback: API oficial HN (Firebase). Trae topstories + detalle de items."""
    try:
        async with httpx.AsyncClient(headers={"User-Agent": UA}, timeout=15) as client:
            top = await client.get(HN_FB_TOP_URL)
            top.raise_for_status()
            ids = top.json()[: max(top_n * 3, 30)]

            stories = []
            for item_id in ids[: top_n * 2]:
                if len(stories) >= top_n:
                    break
                resp = await client.get(HN_FB_ITEM_URL.format(item_id))
                if resp.status_code != 200:
                    continue
                item = resp.json() or {}
                if item.get("type") != "story" or not item.get("title"):
                    continue
                points = int(item.get("score") or 0)
                if points < min_points:
                    continue
                stories.append(
                    {
                        "title": item["title"],
                        "points": points,
                        "comments": int(item.get("descendants") or 0),
                        "url": item.get("url") or f"https://news.ycombinator.com/item?id={item_id}",
                    },
                )
    except (httpx.HTTPError, ValueError) as exc:
        raise TrendingError(f"HN Firebase API no disponible: {exc}") from exc

    if not stories:
        raise TrendingError("HN Firebase API devolvió 0 historias")
    return {"status": "ok", "items": stories[:top_n], "source": "firebase"}


async def fetch_trending() -> dict[str, dict]:
    """GitHub + HN en paralelo. Cada fuente falla de forma independiente."""
    import asyncio

    gh_task = asyncio.create_task(fetch_github_trending())
    hn_task = asyncio.create_task(fetch_hackernews())
    gh_res, hn_res = await asyncio.gather(gh_task, hn_task, return_exceptions=True)

    results: dict[str, dict] = {}
    if isinstance(gh_res, Exception):
        logger.error("Trending GitHub: %s", gh_res)
        results["github"] = {"status": "error", "error": str(gh_res)[:300]}
    else:
        results["github"] = gh_res

    if isinstance(hn_res, Exception):
        logger.error("Trending HN: %s", hn_res)
        results["hackernews"] = {"status": "error", "error": str(hn_res)[:300]}
    else:
        results["hackernews"] = hn_res

    return results


def render_trending_brief(github: dict, hackernews: dict) -> str:
    """Plantilla HTML (Telegram-safe) del bloque de tendencias para /hoy."""
    parts: list[str] = []

    if github.get("status") == "ok":
        lines = []
        for r in (github.get("ai") or [])[:5]:
            desc = (r.get("description") or "").strip()
            line = f'⭐ <b>{r["full_name"]}</b> ({r["stars"]:,}★)'
            if desc:
                line += "\n   " + desc[:120]
            lines.append(line)
        if lines:
            parts.append("🐙 <b>Tendencias en GitHub (repos nuevos)</b>\n" + "\n".join(lines))

    if hackernews.get("status") == "ok":
        lines = [
            f'• <a href="{s["url"]}">{s["title"]}</a> ({s["points"]} pts, {s["comments"]} 💬)'
            for s in (hackernews.get("items") or [])[:5]
        ]
        if lines:
            parts.append("🔥 <b>Hacker News</b>\n" + "\n".join(lines))

    if not parts:
        return "<i>Las tendencias no están disponibles ahora mismo.</i>"
    return "\n\n".join(parts)
