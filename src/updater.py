import asyncio
import json
import os
import logging
from datetime import datetime
from src.fetcher import DataFetcher
from src.brain import RankingBrain
from src.config import TELEGRAM_TOKEN, ADMIN_CHAT_ID

logger = logging.getLogger(__name__)


class DataUpdater:
    """Maneja la actualización periódica de datos de rankings."""

    def __init__(self, bot=None):
        self.bot = bot
        self.fetcher = DataFetcher()
        self.brain = RankingBrain()
        self.output_path = os.path.join("data", "rankings.json")
        self.last_check = None
        self.last_change = None

    async def check_and_update(self, force=False):
        """
        Verifica y actualiza los datos si hay cambios.
        Retorna: dict con información del resultado.
        """
        result = {
            "success": False,
            "updated": False,
            "changes_detected": False,
            "message": "",
            "timestamp": datetime.now().isoformat(),
        }

        try:
            logger.info("🔄 Iniciando verificación de actualizaciones...")

            # 1. Cargar datos antiguos
            old_data = {}
            if os.path.exists(self.output_path):
                with open(self.output_path, "r", encoding="utf-8") as f:
                    old_data = json.load(f)
                    result["last_update"] = datetime.fromtimestamp(
                        os.path.getmtime(self.output_path)
                    ).isoformat()

            # 2. Extraer nuevos datos
            logger.info("📡 Scraping fuentes de datos...")
            new_data = await self.fetcher.fetch_all()
            result["sources_scraped"] = list(new_data.keys())

            # 3. Comparar datos
            changes_detected = False
            notification_text = None

            if old_data and not force:
                logger.info("🔍 Comparando con datos anteriores...")
                comparison = self.brain.compare_data(old_data, new_data)

                if comparison and "SIN_CAMBIOS" not in comparison:
                    changes_detected = True
                    notification_text = comparison
                    result["changes_detected"] = True
                    result["comparison_result"] = comparison
                    logger.info("📣 ¡Cambios detectados!")
                else:
                    logger.info("✅ Sin cambios significativos.")
                    result["message"] = "Sin cambios detectados"
            else:
                # Primera ejecución o forzada
                result["message"] = "Primera actualización o forzada"
                if force:
                    changes_detected = True
                    notification_text = "🔄 **Actualización Forzada**\n\nLos datos han sido actualizados manualmente."

            # 4. Guardar datos
            os.makedirs("data", exist_ok=True)
            with open(self.output_path, "w", encoding="utf-8") as f:
                json.dump(new_data, f, ensure_ascii=False, indent=2)

            result["success"] = True
            result["updated"] = True
            self.last_check = datetime.now()

            if changes_detected and notification_text:
                self.last_change = datetime.now()
                # 5. Enviar notificación
                await self._send_notification(notification_text)
                result["message"] = (
                    f"Actualización exitosa con cambios: {notification_text[:100]}..."
                )

            logger.info(f"✅ Datos guardados en {self.output_path}")
            return result

        except Exception as e:
            error_msg = f"Error en actualización: {str(e)}"
            logger.error(error_msg, exc_info=True)
            result["message"] = error_msg
            result["error"] = str(e)
            await self._send_notification(
                f"⚠️ **Error en actualización automática**\n\n{error_msg}", is_error=True
            )
            return result

    async def _send_notification(self, text, is_error=False):
        """Envía notificación por Telegram al admin."""
        if not TELEGRAM_TOKEN or not ADMIN_CHAT_ID:
            logger.warning("No se puede enviar notificación: Faltan tokens")
            return

        if not self.bot:
            # Usar requests si no tenemos bot (ej: GitHub Actions)
            import requests

            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            payload = {"chat_id": ADMIN_CHAT_ID, "text": text, "parse_mode": "Markdown"}
            try:
                requests.post(url, json=payload, timeout=10)
            except Exception as e:
                logger.error(f"Error enviando notificación: {e}")
        else:
            # Usar el bot de Telegram
            try:
                await self.bot.send_message(
                    chat_id=int(ADMIN_CHAT_ID), text=text, parse_mode="Markdown"
                )
            except Exception as e:
                logger.error(f"Error enviando notificación: {e}")

    def get_status(self):
        """Retorna el estado actual del updater."""
        return {
            "last_check": self.last_check.isoformat() if self.last_check else None,
            "last_change": self.last_change.isoformat() if self.last_change else None,
            "data_exists": os.path.exists(self.output_path),
            "output_path": self.output_path,
        }


# Función de conveniencia para compatibilidad hacia atrás
async def update_data(bot=None, force=False):
    """Función standalone para ejecutar actualización (usada por GitHub Actions)."""
    updater = DataUpdater(bot=bot)
    return await updater.check_and_update(force=force)
