"""Captura de datos: APIs públicas primero, Playwright con navegador único como respaldo.

Devuelve por fuente: ``{"status": "ok", "models": [...]}`` o ``{"status": "error", "error": msg}``.
Además enriquece los modelos con precios por millón de tokens, ventana de contexto y
modelos ``:free`` desde el catálogo público gratuito ``/api/v1/models`` de OpenRouter.
"""

import asyncio
import html
import logging
import re

import httpx
from playwright.async_api import async_playwright

from src.config import DISABLE_PLAYWRIGHT, URLS
from src.parsers import (
    ParseError,
    enrich_models_with_catalog,
    parse_arena,
    parse_arena_hf_dataset,
    parse_arena_mirror_json,
    parse_design_arena,
    parse_openrouter_api,
    parse_openrouter_catalog,
    parse_openrouter_page,
)
from src.validator import validate_source

logger = logging.getLogger(__name__)

_TAG_STRIP_BLOCKS_RE = re.compile(r"<(script|style|noscript|svg)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_BLOCK_TAGS_RE = re.compile(
    r"</?(?:tr|td|th|div|p|li|h[1-6]|section|article|header|footer|br|table|tbody|thead|dt|dd|ul|ol|nav)[^>]*>",
    re.IGNORECASE,
)
_ALL_TAGS_RE = re.compile(r"<[^>]+>")


def _html_to_text(raw_html: str) -> str:
    """Convierte HTML SSR en texto limpio por líneas para los parsers deterministas."""
    if not raw_html:
        return ""
    cleaned = _TAG_STRIP_BLOCKS_RE.sub(" ", raw_html)
    cleaned = _BLOCK_TAGS_RE.sub("\n", cleaned)
    cleaned = _ALL_TAGS_RE.sub("", cleaned)
    cleaned = html.unescape(cleaned)
    return "\n".join(line.strip() for line in cleaned.splitlines() if line.strip())


async def _abort_heavy_assets(route) -> None:
    """Bloquea imágenes, fuentes, vídeo y CSS en Playwright para ahorrar RAM y latencia."""
    if route.request.resource_type in ("image", "media", "font", "stylesheet"):
        await route.abort()
    else:
        await route.continue_()

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/datasets/model-rankings?range=last-day"
OPENROUTER_MODELS_API_URL = URLS.get("openrouter_models_api", "https://openrouter.ai/api/v1/models")
ARENA_HF_FILTER_URL = "https://datasets-server.huggingface.co/filter"
ARENA_HF_DATASET = "lmarena-ai/leaderboard-dataset"
ARENA_MIRROR_JSON_URL = URLS.get(
    "arena_mirror_json",
    "https://raw.githubusercontent.com/Tliens/ai-model-rank/main/data.json",
)
NAV_TIMEOUT_MS = 60_000
HYDRATE_TIMEOUT_MS = 15_000
PAGE_SETTLE_MS = 2_500

BLOCK_MARKERS = ("Sorry, you have been blocked", "Attention Required!", "Just a moment", "cf-browser-verification")


class FetchError(Exception):
    """Una fuente no pudo ser capturada ni parseada."""


class DataFetcher:
    """Extrae y estructura los rankings y metadatos de precios de todas las fuentes."""

    async def fetch_all(self) -> dict[str, dict]:
        """Captura todas las fuentes con APIs JSON primero y navegador único como respaldo."""
        # 1. Peticiones ligeras por API en paralelo (0 MB extra de RAM, sin bloqueo Cloudflare)
        arena_api_models, openrouter_api, catalog = await asyncio.gather(
            self._fetch_arena_api(),
            self._fetch_openrouter_api(),
            self._fetch_openrouter_catalog(),
        )

        # 2. Intento HTTP ligero (SSR/HTML) antes de levantar Chromium (ahorra RAM en 512MB)
        candidate_pages: dict[str, tuple[str, object]] = {"design": (URLS["design_arena"], parse_design_arena)}
        if arena_api_models is None:
            candidate_pages["arena"] = (URLS["arena"], parse_arena)
        if openrouter_api is None:
            candidate_pages["openrouter"] = (URLS["openrouter"], parse_openrouter_page)

        results: dict[str, dict] = {}
        pages_for_browser: dict[str, str] = {}
        http_checks = await asyncio.gather(
            *(self._fetch_page_http(url) for url, _parser in candidate_pages.values()),
            return_exceptions=True,
        )
        for (key, (url, parser)), http_res in zip(candidate_pages.items(), http_checks):
            http_text = http_res if isinstance(http_res, str) else None
            if http_text:
                parsed = self._parse_source(key, http_text, None, parser)
                if parsed.get("status") == "ok" and not validate_source(key, parsed):
                    parsed["method"] = "http"
                    results[key] = parsed
                    continue
            pages_for_browser[key] = url

        texts, page_errors = await self._load_pages(pages_for_browser)

        if arena_api_models is not None:
            results["arena"] = {"status": "ok", "models": arena_api_models, "method": "api"}
        elif "arena" not in results:
            results["arena"] = self._parse_source("arena", texts.get("arena"), page_errors.get("arena"), parse_arena)

        if "design" not in results:
            results["design"] = self._parse_source(
                "design", texts.get("design"), page_errors.get("design"), parse_design_arena
            )

        if openrouter_api is not None:
            try:
                results["openrouter"] = {
                    "status": "ok",
                    "models": parse_openrouter_api(openrouter_api),
                    "method": "api",
                }
            except ParseError as exc:
                logger.warning("OpenRouter API: %s — probando página como fallback", exc)
                if "openrouter" not in results:
                    results["openrouter"] = self._parse_source(
                        "openrouter", texts.get("openrouter"), page_errors.get("openrouter"), parse_openrouter_page
                    )
        elif "openrouter" not in results:
            results["openrouter"] = self._parse_source(
                "openrouter", texts.get("openrouter"), page_errors.get("openrouter"), parse_openrouter_page
            )

        # 3. Enriquecer modelos con precios $/1M tokens, contexto y catálogo de modelos gratuitos
        if catalog:
            for src_payload in results.values():
                if src_payload.get("status") == "ok" and isinstance(src_payload.get("models"), list):
                    enrich_models_with_catalog(src_payload["models"], catalog)
            if isinstance(results.get("openrouter"), dict):
                results["openrouter"]["free_models"] = catalog.get("free_models", [])
                results["openrouter"]["recent_models"] = catalog.get("recent_models", [])

        return results

    async def _fetch_arena_api(self) -> list[dict] | None:
        """Obtiene el leaderboard oficial de LMArena vía JSON (HF Datasets Server → mirror GitHub Raw)."""
        hf_models = await self._fetch_arena_hf_dataset()
        if hf_models:
            return hf_models

        try:
            async with httpx.AsyncClient(headers={"User-Agent": BROWSER_UA}, timeout=20, follow_redirects=True) as client:
                resp = await client.get(ARENA_MIRROR_JSON_URL)
                if resp.status_code == 200:
                    models = parse_arena_mirror_json(resp.json(), per_category=15)
                    logger.info("LMArena cargado vía feed JSON verificado (%d entradas).", len(models))
                    return models
        except Exception as exc:  # noqa: BLE001
            logger.info("Feed JSON secundario de Arena no disponible (%s); se usará navegador.", exc)
        return None

    async def _fetch_arena_hf_dataset(self) -> list[dict] | None:
        """Consulta el dataset oficial público ``lmarena-ai/leaderboard-dataset`` en Hugging Face."""
        queries = (
            ("Text", "text", "overall"),
            ("Code", "text", "coding"),
            ("WebDev", "webdev", "overall"),
            ("Vision", "vision", "overall"),
            ("Agent", "agent", "overall"),
        )
        try:
            async with httpx.AsyncClient(headers={"User-Agent": BROWSER_UA}, timeout=18, follow_redirects=True) as client:
                tasks = [
                    client.get(
                        ARENA_HF_FILTER_URL,
                        params={
                            "dataset": ARENA_HF_DATASET,
                            "config": conf,
                            "split": "latest",
                            "where": f"\"category\"='{cat}'",
                            "orderby": "rank",
                            "offset": "0",
                            "length": "15",
                        },
                    )
                    for _label, conf, cat in queries
                ]
                responses = await asyncio.gather(*tasks, return_exceptions=True)

            category_rows: dict[str, list[dict]] = {}
            for (label, _conf, _cat), resp in zip(queries, responses):
                if isinstance(resp, Exception) or getattr(resp, "status_code", 0) != 200:
                    continue
                body = resp.json()
                rows = body.get("rows") if isinstance(body, dict) else None
                if isinstance(rows, list) and rows:
                    category_rows[label] = rows

            if "Text" not in category_rows:
                return None
            models = parse_arena_hf_dataset(category_rows, per_category=15)
            logger.info("LMArena cargado desde dataset oficial en Hugging Face (%d entradas).", len(models))
            return models
        except Exception as exc:  # noqa: BLE001
            logger.info("Dataset oficial HF de Arena no disponible (%s); probando respaldo JSON.", exc)
            return None

    async def _fetch_openrouter_api(self):
        """Intenta la Data API de OpenRouter (ranking por uso de tokens)."""
        try:
            async with httpx.AsyncClient(headers={"User-Agent": BROWSER_UA}, timeout=15, follow_redirects=True) as client:
                resp = await client.get(OPENROUTER_API_URL)
                if resp.status_code == 200:
                    return resp.json()
                logger.info("OpenRouter rankings API devolvió %s; se usará la página.", resp.status_code)
        except Exception as exc:  # noqa: BLE001
            logger.info("OpenRouter rankings API no disponible (%s); se usará la página.", exc)
        return None

    async def _fetch_openrouter_catalog(self) -> dict | None:
        """Obtiene el catálogo público gratuito /api/v1/models (precios, contexto y modelos :free)."""
        try:
            async with httpx.AsyncClient(headers={"User-Agent": BROWSER_UA}, timeout=20, follow_redirects=True) as client:
                resp = await client.get(OPENROUTER_MODELS_API_URL)
                if resp.status_code == 200:
                    return parse_openrouter_catalog(resp.json())
        except Exception as exc:  # noqa: BLE001
            logger.info("Catálogo público de OpenRouter no disponible (%s).", exc)
        return None

    async def _fetch_page_http(self, url: str) -> str | None:
        """Descarga HTML ligero con httpx y extrae texto SSR sin abrir navegador."""
        try:
            async with httpx.AsyncClient(headers={"User-Agent": BROWSER_UA}, timeout=15, follow_redirects=True) as client:
                resp = await client.get(url)
                if resp.status_code != 200 or not resp.text:
                    return None
                text = _html_to_text(resp.text)
                if any(marker in text[:2000] for marker in BLOCK_MARKERS):
                    return None
                return text
        except Exception as exc:  # noqa: BLE001
            logger.debug("HTTP ligero no suficiente para %s: %s", url, exc)
            return None

    async def _load_pages(self, urls: dict[str, str]) -> tuple[dict[str, str | None], dict[str, str]]:
        """Carga varias páginas con una única instancia de Chromium y espera de hidratación."""
        texts: dict[str, str | None] = {}
        errors: dict[str, str] = {}
        if not urls:
            return texts, errors
        if DISABLE_PLAYWRIGHT:
            logger.info("Playwright desactivado por DISABLE_PLAYWRIGHT=true; omitiendo %s.", list(urls.keys()))
            for key in urls:
                errors[key] = "Playwright desactivado (DISABLE_PLAYWRIGHT=true)"
            return texts, errors

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=True,
                    args=[
                        "--disable-dev-shm-usage",
                        "--no-sandbox",
                        "--disable-gpu",
                        "--disable-extensions",
                        "--disable-background-networking",
                        "--disable-default-apps",
                        "--mute-audio",
                        "--disable-software-rasterizer",
                    ],
                )
                try:
                    for key, url in urls.items():
                        page = await browser.new_page(
                            user_agent=BROWSER_UA,
                            locale="en-US",
                            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
                        )
                        try:
                            await page.route("**/*", _abort_heavy_assets)
                            await page.goto(url, timeout=NAV_TIMEOUT_MS, wait_until="domcontentloaded")
                            try:
                                await page.wait_for_function(
                                    "() => document.body && document.body.innerText.length > 1500",
                                    timeout=HYDRATE_TIMEOUT_MS,
                                )
                            except Exception:  # noqa: BLE001 - si tarda más, leemos lo hidratado tras settle
                                pass
                            await page.wait_for_timeout(PAGE_SETTLE_MS)
                            body = await page.inner_text("body")
                            if any(marker in body[:2000] for marker in BLOCK_MARKERS):
                                raise FetchError(f"Cloudflare bloqueó el acceso a {url} desde esta IP")
                            texts[key] = body
                        except Exception as exc:  # noqa: BLE001
                            logger.error("Error capturando %s: %s", key, exc)
                            errors[key] = str(exc)[:300]
                        finally:
                            await page.close()
                finally:
                    await browser.close()
        except Exception as exc:  # noqa: BLE001
            logger.error("Fallo global de Playwright: %s", exc)
            for key in urls:
                if key not in texts:
                    errors[key] = f"Playwright no disponible: {str(exc)[:200]}"

        return texts, errors

    def _parse_source(self, name: str, text: str | None, page_error: str | None, parser) -> dict:
        """Parsea el texto de una página; cualquier fallo queda como status=error."""
        if text is None:
            return {"status": "error", "error": page_error or "sin texto capturado"}
        try:
            return {"status": "ok", "models": parser(text), "method": "page"}
        except ParseError as exc:
            logger.error("Parseo fallido en %s: %s", name, exc)
            return {"status": "error", "error": str(exc)}


async def fetch_snapshot() -> dict[str, dict]:
    """Función de conveniencia: captura completa de todas las fuentes."""
    return await DataFetcher().fetch_all()
