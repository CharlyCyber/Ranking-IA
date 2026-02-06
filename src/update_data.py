import asyncio
import json
import os
from src.fetcher import DataFetcher

async def update():
    print("🚀 Iniciando actualización de rankings (Worker)...")
    fetcher = DataFetcher()
    
    # Extraer todos los datos
    # Nota: No necesitamos claves de API reales aquí si solo guardamos los placeholders
    # o si las claves están en los Secretos de GitHub.
    data = await fetcher.fetch_all()
    
    # Guardar en archivo JSON
    output_path = os.path.join("data", "rankings.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        
    print(f"✅ Datos guardados exitosamente en {output_path}")

if __name__ == "__main__":
    asyncio.run(update())
