"""Captura de datos: API pública cuando existe, Playwright con navegador único como fallback.

Devuelve por fuente: ``{"status": "ok", "models": [...]}`` o ``{"status": "error", "error": msg}``.
La decisión de conservar datos antiguos ante un error corresponde a ``updater`` + ``validator``.
"""

import logging

import httpx
from playwright.async_api import async_playwright

from src.config import URLS
from src.parsers import (
    ParseError,
    parse_arena,
    parse_design_arena,
    parse_openrouter_api,
    parse_openrouter_page,
)

logger = logging.getLogger(__name__)

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/datasets/model-rankings?range=last-day"
NAV_TIMEOUT_MS = 60_000
PAGE_SETTLE_MS = 5_000

# Marcas de página de bloqueo/挑战 de Cloudflare: diagnóstico claro en vez de un
# error de parseo confuso ("no se encontró la tabla").
BLOCK_MARKERS = ("Sorry, you have been blocked", "Attention Required!", "Just a moment", "cf-browser-verification")


class FetchError(Exception):
    """Una fuente no pudo ser capturada ni parseada."""


class DataFetcher:
    """Extrae y estructura los rankings de todas las fuentes."""

    async def fetch_all(self) -> dict[str, dict]:
        """Captura todas las fuentes en paralelo cuando sea posible.

        Un solo navegador Chromium se usa para todas las páginas (antes se
        lanzaba una instancia por fuente, ~3x memoria).
        """
        # 1. OpenRouter: intento API (barato y sin navegador). Fallback a página.
        openrouter_api = await self._fetch_openrouter_api()

        # 2. Páginas que necesitan navegador: Arena, Design y OpenRouter solo si falló la API.
        pages = {"arena": URLS["arena"], "design": URLS["design_arena"]}
        if openrouter_api is None:
            pages["openrouter"] = URLS["openrouter"]

        texts, page_errors = await self._load_pages(pages)

        results: dict[str, dict] = {}

        results["arena"] = self._parse_source("arena", texts.get("arena"), page_errors.get("arena"), parse_arena)
        results["design"] = self._parse_source("design", texts.get("design"), page_errors.get("design"), parse_design_arena)

        if openrouter_api is not None:
            try:
                results["openrouter"] = {"status": "ok", "models": parse_openrouter_api(openrouter_api), "method": "api"}
            except ParseError as exc:
                logger.warning("OpenRouter API: %s — probando página como fallback", exc)
                results["openrouter"] = self._parse_source(
                    "openrouter", texts.get("openrouter"), page_errors.get("openrouter"), parse_openrouter_page
                )
        else:
            results["openrouter"] = self._parse_source(
                "openrouter", texts.get("openrouter"), page_errors.get("openrouter"), parse_openrouter_page
            )

        return results

    async def _fetch_openrouter_api(self):
        """Intenta la Data API de OpenRouter (ranking semanal por uso de tokens)."""
        try:
            async with httpx.AsyncClient(headers={"User-Agent": BROWSER_UA}, timeout=15, follow_redirects=True) as client:
                resp = await client.get(OPENROUTER_API_URL)
                if resp.status_code == 200:
                    return resp.json()
                logger.info("OpenRouter API devolvió %s (WAF o endpoint cambiado); se usará la página.", resp.status_code)
        except Exception as exc:  # noqa: BLE001 - cualquier fallo de red cae al fallback
            logger.info("OpenRouter API no disponible (%s); se usará la página.", exc)
        return None

    async def _load_pages(self, urls: dict[str, str]) -> tuple[dict[str, str | None], dict[str, str]]:
        """Carga varias páginas con una única instancia de Chromium."""
        texts: dict[str, str | None] = {}
        errors: dict[str, str] = {}
        if not urls:
            return texts, errors

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                try:
                    for key, url in urls.items():
                        page = await browser.new_page(
                            user_agent=BROWSER_UA,
                            locale="es-ES",
                            extra_http_headers={"Accept-Language": "es-ES,es;q=0.9,en;q=0.8"},
                        )
                        try:
                            # `domcontentloaded` en vez de `load`: evita timeouts cuando
                            # el SPA deja recursos colgando (causa real de fallos en Render).
                            await page.goto(url, timeout=NAV_TIMEOUT_MS, wait_until="domcontentloaded")
                            await page.wait_for_timeout(PAGE_SETTLE_MS)
                            body = await page.inner_text("body")
                            if any(marker in body[:2000] for marker in BLOCK_MARKERS):
                                raise FetchError(f"Cloudflare bloqueó el acceso a {url} desde esta IP")
                            texts[key] = body
                        except Exception as exc:  # noqa: BLE001 - un fallo no debe abortar el resto
                            logger.error("Error capturando %s: %s", key, exc)
                            errors[key] = str(exc)[:300]
                        finally:
                            await page.close()
                finally:
                    await browser.close()
        except Exception as exc:  # noqa: BLE001 - fallo global de Playwright (navegador no instalado, etc.)
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

