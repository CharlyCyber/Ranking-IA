"""
Script de actualización de datos (compatibility layer).
Este script ahora utiliza el módulo updater.py centralizado.
"""

import asyncio

from src.updater import update_data

if __name__ == "__main__":
    print("🚀 Ejecutando actualización de rankings (GitHub Actions / Manual)...")
    result = asyncio.run(update_data(bot=None, force=False))

    if result["success"]:
        print("✅ Proceso completado exitosamente")
        if result["changes_detected"]:
            print("📣 Se detectaron cambios y se envió notificación")
        else:
            print("ℹ️ Sin cambios significativos")
    else:
        print(f"❌ Error: {result.get('message', 'Error desconocido')}")
        exit(1)
