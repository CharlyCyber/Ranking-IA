"""Búsqueda web bajo demanda con resultados trazables de Tavily.

Los resultados son evidencia no confiable (no una garantía de veracidad). Solo se
aceptan URLs HTTP(S), se limita el número/tamaño de fragmentos y se preserva la
referencia del proveedor para que el asistente pueda citarla de forma determinista.
"""

import hashlib
import html
import ipaddress
import logging
import re
import threading
import time
import unicodedata
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx

from src.config import LIVE_SEARCH_DAILY_LIMIT, TAVILY_API_KEY
from src.db import Database
from src.html_utils import safe_url

logger = logging.getLogger(__name__)
TAVILY_SEARCH_URL = "https://api.tavily.com/search"
MAX_RESULTS = 3
MAX_EXCERPT_CHARS = 2200
_CACHE_TTL_SECONDS = 300
_CACHE_MAX_ENTRIES = 128
_SEARCH_CACHE: dict[str, tuple[float, dict]] = {}
_SEARCH_CACHE_LOCK = threading.Lock()


def is_public_http_url(url: object) -> bool:
    """Rechaza esquemas no web, hosts locales y direcciones IP no globales."""
    if not safe_url(url):
        return False
    try:
        host = (urlparse(str(url)).hostname or "").lower().rstrip(".")
    except ValueError:
        return False
    if not host or host in {"localhost", "localhost.localdomain"} or host.endswith((".local", ".internal")):
        return False
    try:
        return ipaddress.ip_address(host).is_global
    except ValueError:
        return True


def _clean_text(value: object, limit: int) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]*>", " ", text)
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _search_query(query: str) -> str:
    """Prioriza la pregunta factual y omite instrucciones de formato/citación para el buscador."""
    text = _clean_text(query, 800)
    match = re.search(r"¿([^?]{3,500})\?", text)
    if match:
        return match.group(1).strip()
    return re.split(r"\s+(?:cita|cítalo|contrasta|contrástalo|incluye|indica)\b", text, maxsplit=1, flags=re.I)[0].strip()


_STOPWORDS = {
    "acerca", "ahora", "alguna", "algunas", "alguno", "algunos", "ante", "como", "con", "contra",
    "cual", "cuales", "cuando", "cuanta", "cuantas", "cuanto", "cuantos", "de", "del", "desde",
    "donde", "el", "ella", "ellas", "ellos", "en", "entre", "era", "es", "esa", "esas", "ese",
    "eso", "esos", "esta", "estaba", "estan", "este", "esto", "estos", "fue", "ha", "hace", "hay",
    "la", "las", "le", "les", "lo", "los", "mas", "me", "mi", "mis", "muy", "no", "o", "para",
    "pero", "por", "porque", "que", "quien", "quienes", "se", "segun", "sin", "sobre", "son", "su",
    "sus", "te", "tiene", "tienen", "todo", "todos", "tu", "un", "una", "unas", "uno", "unos", "y",
    "ultimo", "ultima", "ultimos", "ultimas", "snapshot", "actual", "actuales", "actualmente", "pagina",
    "fuente", "fuentes", "independiente", "independientes", "cita", "citas", "cita", "contrasta", "contrastar",
    "indica", "incluye", "dime", "debes", "deberia", "deberia", "deberias", "quiero", "saber", "segun",
}


def _tokens(value: object) -> set[str]:
    normalized = unicodedata.normalize("NFKD", _clean_text(value, 4000).lower())
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    words = re.findall(r"[a-z0-9]{3,}", ascii_text)
    return {word[:-1] if word.endswith("s") and len(word) > 4 else word for word in words}


def _is_relevant_result(query: str, title: str, url: str, excerpt: str) -> bool:
    """Descarta resultados sin coincidencias léxicas mínimas con la pregunta factual."""
    terms = _tokens(query) - _STOPWORDS
    if not terms:
        return True
    title_terms = _tokens(f"{title} {url}")
    all_terms = title_terms | _tokens(excerpt)
    required = min(2, len(terms))
    return len(terms & all_terms) >= required and bool(terms & title_terms)


def _query_cache_key(query: str) -> str:
    normalized = " ".join(_search_query(query).lower().split())[:800]
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _cached_search(key: str) -> dict | None:
    now = time.monotonic()
    with _SEARCH_CACHE_LOCK:
        value = _SEARCH_CACHE.get(key)
        if not value:
            return None
        expires, result = value
        if now >= expires:
            _SEARCH_CACHE.pop(key, None)
            return None
        return {
            "status": result["status"],
            "sources": [dict(item) for item in result["sources"]],
            "searched_at": result.get("searched_at"),
        }


def _cache_search(key: str, result: dict) -> None:
    now = time.monotonic()
    with _SEARCH_CACHE_LOCK:
        for item in [cache_key for cache_key, (expires, _result) in _SEARCH_CACHE.items() if expires <= now]:
            _SEARCH_CACHE.pop(item, None)
        if len(_SEARCH_CACHE) >= _CACHE_MAX_ENTRIES:
            _SEARCH_CACHE.pop(next(iter(_SEARCH_CACHE)))
        _SEARCH_CACHE[key] = (
            now + _CACHE_TTL_SECONDS,
            {
                "status": result["status"],
                "sources": [dict(item) for item in result["sources"]],
                "searched_at": result.get("searched_at"),
            },
        )


def search_live_web(query: str, database: Database) -> dict:
    """Busca fuentes públicas recientes o devuelve un estado explícito si no es posible."""
    if not TAVILY_API_KEY:
        return {"status": "not_configured", "sources": []}
    search_query = _search_query(query)
    cache_key = _query_cache_key(search_query)
    cached = _cached_search(cache_key)
    if cached is not None:
        return cached

    daily_key = f"live_search:{datetime.now(timezone.utc).date().isoformat()}"
    if not database.consume_limit(daily_key, LIVE_SEARCH_DAILY_LIMIT, 86400):
        return {"status": "daily_limit", "sources": []}

    payload = {
        "api_key": TAVILY_API_KEY,
        "query": _clean_text(search_query, 500),
        "search_depth": "basic",
        "topic": "general",
        "max_results": MAX_RESULTS,
        "include_answer": False,
        "include_raw_content": True,
    }
    try:
        with httpx.Client(timeout=httpx.Timeout(12.0, connect=5.0), follow_redirects=True) as client:
            response = client.post(TAVILY_SEARCH_URL, json=payload)
            response.raise_for_status()
            body = response.json()
    except Exception as exc:  # noqa: BLE001 - no hace fallar la conversación si el buscador cae
        logger.warning("Búsqueda web no disponible (%s)", type(exc).__name__)
        return {"status": "unavailable", "sources": []}

    results = body.get("results") if isinstance(body, dict) else None
    sources = []
    rejected_irrelevant = 0
    for item in results if isinstance(results, list) else []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not is_public_http_url(url):
            continue
        try:
            domain = (urlparse(url).hostname or "").lower().removeprefix("www.")
        except ValueError:
            continue
        if not domain:
            continue
        page_text = item.get("raw_content")
        has_page_text = isinstance(page_text, str) and bool(page_text.strip())
        evidence = page_text if has_page_text else item.get("content")
        if not _is_relevant_result(search_query, item.get("title", ""), url, str(evidence or "")):
            rejected_irrelevant += 1
            continue
        sources.append(
            {
                "id": f"S{len(sources) + 1}",
                "title": _clean_text(item.get("title"), 240) or domain,
                "url": url,
                "domain": domain,
                "published_date": _clean_text(item.get("published_date"), 40),
                "evidence_type": "page_text" if has_page_text else "search_snippet",
                "excerpt": _clean_text(evidence, MAX_EXCERPT_CHARS),
            }
        )
        if len(sources) >= MAX_RESULTS:
            break

    if rejected_irrelevant:
        logger.info("Tavily devolvió %d resultado(s) irrelevante(s), descartados.", rejected_irrelevant)
    result = {
        "status": "ok" if sources else "no_results",
        "sources": sources,
        "searched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    _cache_search(cache_key, result)
    return result


def format_search_evidence(sources: list[dict] | None) -> str:
    """Serializa los resultados con IDs estables y delimitación de datos no confiables."""
    lines = []
    for source in (sources or [])[:MAX_RESULTS]:
        source_id = str(source.get("id") or "")
        if not re.fullmatch(r"S[1-5]", source_id):
            continue
        title = _clean_text(source.get("title"), 240)
        domain = _clean_text(source.get("domain"), 120)
        published = _clean_text(source.get("published_date"), 40)
        excerpt = _clean_text(source.get("excerpt"), MAX_EXCERPT_CHARS)
        url = str(source.get("url") or "")
        if not is_public_http_url(url):
            continue
        date_note = f" · fecha publicada: {published}" if published else ""
        evidence_kind = "texto extraído de la página" if source.get("evidence_type") == "page_text" else "fragmento de búsqueda"
        lines.append(
            f"[{source_id}] {html.escape(title, quote=True)} · {html.escape(domain, quote=True)}{date_note}\n"
            f"URL: {html.escape(url, quote=True)}\nTipo de evidencia: {evidence_kind}\n"
            f"Texto: {html.escape(excerpt, quote=True)}"
        )
    return "\n\n".join(lines)
