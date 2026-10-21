"""Prueba manual: captura de todas las fuentes + respuesta del cerebro con contexto v3.

Uso: python test_scraper.py
"""

import asyncio
from datetime import datetime

from dotenv import load_dotenv

from src.brain import build_context, get_brain
from src.fetcher import DataFetcher


async def test():
    print("🚀 Iniciando prueba de extracción...")
    fetcher = DataFetcher()
    results = await fetcher.fetch_all()

    for source, payload in results.items():
        status = payload.get("status")
        entries = payload.get("models") or payload.get("items") or []
        detail = f"{len(entries)} entradas" if status == "ok" else payload.get("error", "")[:80]
        print(f"  {source}: {status} — {detail}")

    snapshot = {"generated_at": datetime.now().isoformat(), "sources": results}
    context = build_context(snapshot)
    print(f"\n📝 Contexto generado: {len(context)} caracteres")

    print("\n🧠 Probando cerebro con Gemini...")
    brain = get_brain()
    answer = brain.generate_response(
        "¿Cuál es el mejor modelo para código según Arena?", context
    )
    print(f"\n🤖 Respuesta de Gemini:\n{answer}")


if __name__ == "__main__":
    load_dotenv()
    asyncio.run(test())
