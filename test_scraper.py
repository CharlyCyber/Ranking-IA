import asyncio
from src.fetcher import DataFetcher
from src.brain import RankingBrain
import os
from dotenv import load_dotenv

async def test():
    print("🚀 Iniciando prueba de extracción...")
    fetcher = DataFetcher()
    brain = RankingBrain()
    
    # Probar solo Arena para rapidez
    print("📡 Extrayendo datos de LMSYS Arena...")
    arena_data = await fetcher.fetch_arena_rankings()
    print(f"✅ Datos obtenidos (primeros 100 caracteres): {arena_data[:100]}...")
    
    print("\n🧠 Probando cerebro con Gemini...")
    pregunta = "¿Cuál es el mejor modelo para código según Arena?"
    respuesta = brain.generate_response(pregunta, arena_data)
    print(f"🤖 Respuesta de Gemini:\n{respuesta}")

if __name__ == "__main__":
    load_dotenv()
    asyncio.run(test())
