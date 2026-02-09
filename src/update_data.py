import asyncio
import json
import os
import requests
from src.fetcher import DataFetcher
from src.brain import RankingBrain
from src.config import TELEGRAM_TOKEN, ADMIN_CHAT_ID

async def send_notification(text):
    """Envía una notificación de Telegram al administrador."""
    if not TELEGRAM_TOKEN or not ADMIN_CHAT_ID:
        print("⚠️ No se puede enviar notificación: Faltan tokens o Chat ID.")
        return
    
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": ADMIN_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown"
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"❌ Error enviando notificación: {e}")

async def update():
    print("🚀 Iniciando actualización de rankings (Worker)...")
    fetcher = DataFetcher()
    brain = RankingBrain()
    
    # 1. Cargar datos antiguos para comparar
    output_path = os.path.join("data", "rankings.json")
    old_data = {}
    if os.path.exists(output_path):
        with open(output_path, "r", encoding="utf-8") as f:
            old_data = json.load(f)
    
    # 2. Extraer nuevos datos
    new_data = await fetcher.fetch_all()
    
    # 3. Comparar y notificar si hay cambios (solo si ya teníamos datos viejos)
    if old_data:
        print("🔍 Comparando con datos anteriores...")
        news = brain.compare_data(old_data, new_data)
        if news and "SIN_CAMBIOS" not in news:
            print("📣 ¡Cambios detectados! Enviando notificación...")
            await send_notification(news)
        else:
            print("✅ Sin cambios significativos.")

    # 4. Guardar en archivo JSON
    os.makedirs("data", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(new_data, f, ensure_ascii=False, indent=2)
        
    print(f"✅ Datos guardados exitosamente en {output_path}")

if __name__ == "__main__":
    asyncio.run(update())
