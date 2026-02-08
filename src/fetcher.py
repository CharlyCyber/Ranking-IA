import asyncio
from playwright.async_api import async_playwright
import requests
from src.config import URLS, API_KEYS
import json
import logging

class DataFetcher:
    def __init__(self):
        self.data = {}

    async def fetch_arena_rankings(self):
        """Extrae el ranking de LMSYS Arena de forma robusta (texto completo)."""
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page()
                await page.goto(URLS["arena"], timeout=60000)
                await page.wait_for_timeout(5000) 
                
                # En lugar de buscar filas específicas, extraemos el texto visible
                # El LLM se encargará de estructurarlo.
                content = await page.inner_text("body")
                
                await browser.close()
                # Recortamos para no saturar tokens, pero lo suficiente para capturar el top
                return content[:5000] 
        except Exception as e:
            logging.error(f"Error en Arena: {e}")
            return "No se pudo obtener datos de Arena."

    async def fetch_openrouter_rankings(self):
        """Extrae modelos populares de OpenRouter."""
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page()
                await page.goto(URLS["openrouter"], timeout=60000)
                await page.wait_for_timeout(5000)
                content = await page.inner_text("body")
                await browser.close()
                return content[:3000]
        except Exception as e:
            logging.error(f"Error en OpenRouter: {e}")
            return "No se pudo obtener datos de OpenRouter."

    async def fetch_design_arena(self):
        """Extrae ranking de modelos de diseño/imagen."""
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page()
                await page.goto(URLS["design_arena"], timeout=60000)
                await page.wait_for_timeout(5000)
                content = await page.inner_text("body")
                await browser.close()
                return content[:2000]
        except Exception as e:
            logging.error(f"Error en Design Arena: {e}")
            return "No se pudo obtener datos de Design Arena."

    def fetch_artificial_analysis(self):
        """Simulación de datos de Artificial Analysis."""
        key = API_KEYS.get('artificial_analysis')
        display_key = key[:5] if key else "None"
        return f"Datos consolidados de Artificial Analysis (API Activa: {display_key}...)"

    def fetch_context7(self):
        """Simulación de datos de Context7."""
        key = API_KEYS.get('context7')
        display_key = key[:5] if key else "None"
        return f"Datos consolidados de Context7 (API Activa: {display_key}...)"

    async def fetch_all(self):
        """Orquesta todas las extracciones de forma paralela para mayor velocidad."""
        # Ejecutamos las tareas de Playwright en paralelo
        tasks = [
            self.fetch_arena_rankings(),
            self.fetch_openrouter_rankings(),
            self.fetch_design_arena()
        ]
        results = await asyncio.gather(*tasks)
        
        return {
            "arena": results[0],
            "openrouter": results[1],
            "design": results[2],
            "artificial_analysis": self.fetch_artificial_analysis(),
            "context7": self.fetch_context7()
        }