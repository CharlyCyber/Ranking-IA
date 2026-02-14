import json
import google.generativeai as genai
from src.config import GEMINI_API_KEY

class RankingBrain:
    def __init__(self):
        if not GEMINI_API_KEY:
            print("ADVERTENCIA: GEMINI_API_KEY no encontrada. El bot no podrá generar respuestas.")
        else:
            genai.configure(api_key=GEMINI_API_KEY)
        
        # Lista de prioridad de modelos: El más avanzado primero
        self.preferred_models = [
            'gemini-flash-latest', 
            'gemini-pro-latest'
        ]
        
        # Intentamos inicializar el modelo solo si hay key, sino dejaremos que falle en generate_response
        if GEMINI_API_KEY:
            self.model = self._get_best_available_model()
        else:
            self.model = None

    def _get_best_available_model(self):
        """Intenta inicializar el mejor modelo disponible."""
        # Por defecto instanciamos el primero, la validación ocurre al llamar generate_content
        # pero aquí dejamos configurado el "intento" principal.
        print(f"Cerebro: Configurando cerebro con prioridad: {self.preferred_models[0]}")
        return genai.GenerativeModel(self.preferred_models[0])

    def check_health(self):
        """Verifica si el cerebro está conectado y respondiendo."""
        try:
            if not self.model:
                return False, "API Key no configurada."
            # Intento de generación muy barata (1 token)
            self.model.generate_content("Ping", generation_config={"max_output_tokens": 1})
            return True, "Cerebro operativo y conectado a Gemini."
        except Exception as e:
            return False, f"Error de conexión con Gemini: {str(e)}"

    def generate_response(self, user_query, context_data):
        """Genera respuesta con reintentos en modelos de respaldo."""
        
        system_prompt = f"""
        Eres el 'Ranking AI Agent', un analista de IA curioso, entusiasta y muy organizado.
        Tu misión es digerir la información cruda de rankings y presentarla de forma visualmente atractiva en Telegram.

        CONTEXTO DE RANKINGS ACTUALES (Extraído de la web):
        {context_data}
        
        ⛔ REGLAS CRÍTICAS DE FORMATO (Telegram):
        1. **NO USES TABLAS MARKDOWN** (como `| Nombre | Score |`). Se rompen en móviles.
        2. Usa **listas verticales** con emojis.
        3. Evita el uso de `_` (guiones bajos) en el texto normal para no romper el formato, o escápalos si son parte de un nombre.

        🎨 ESTILO DE RESPUESTA:
        - Empieza con un "Hook" curioso o un dato interesante.
        - Usa este formato para los tops:
          🏆 **1. [Nombre Modelo]** 
          ├─ 📊 Score: [Puntaje/Elo]
          └─ 🧠 Insight: [Dato breve o por qué destaca]
          
          🥈 **2. [Nombre Modelo]** ...
        
        - Agrupa por categorías si detectas varias (ej: "General", "Coding", "Velocidad").
        - Tono: Techie, informativo y directo.
        """
        
        prompt = f"{system_prompt}\n\nPregunta del Usuario: {user_query}"
        
        # Lógica de reintento con diferentes modelos
        for model_name in self.preferred_models:
            try:
                current_model = genai.GenerativeModel(model_name)
                response = current_model.generate_content(prompt)
                
                # Caso 1: Respuesta exitosa con texto
                if response and hasattr(response, 'text') and response.text:
                    return response.text
                
                # Caso 2: Respuesta bloqueada o vacía (sin .text disponible)
                if response and response.candidates:
                    parts = response.candidates[0].content.parts
                    if parts:
                        return parts[0].text
                
                print(f"Warning: El modelo {model_name} no devolvió texto utilizable (posible bloqueo).")
                
            except Exception as e:
                # Capturar errores específicos de seguridad o cuotas
                error_msg = str(e)
                if "safety" in error_msg.lower():
                    print(f"Bloqueo de seguridad con {model_name}. Intentando siguiente...")
                else:
                    print(f"Warning: Error con {model_name}: {error_msg}. Intentando siguiente...")
                continue
        
        return "Lo siento, todos los modelos de IA están ocupados o no disponibles en este momento."

    def compare_data(self, old_data, new_data):
        """Compara datos antiguos vs nuevos para detectar cambios interesantes."""
        system_prompt = f"""
        Eres un vigilante de rankings de IA. Tu tarea es comparar los datos antiguos con los nuevos y detectar:
        1. Cambios en el TOP 1 de cualquier categoría.
        2. Nuevos modelos que aparezcan en los rankings.
        3. Caídas o subidas drásticas de posición.

        DATOS ANTIGUOS:
        {json.dumps(old_data, ensure_ascii=False)[:3000]}

        DATOS NUEVOS:
        {json.dumps(new_data, ensure_ascii=False)[:3000]}

        INSTRUCCIONES:
        - Si NO hay cambios relevantes, responde ÚNICAMENTE con la palabra: "SIN_CAMBIOS".
        - Si hay cambios, genera un breve reporte (máx 150 palabras) usando emojis.
        - Sé emocionante, como un boletín de noticias de última hora.
        - Usa el formato: 🚨 **¡ALERTA DE CAMBIO EN EL RANKING!** ...
        """
        
        try:
            for model_name in self.preferred_models:
                try:
                    model = genai.GenerativeModel(model_name)
                    response = model.generate_content(system_prompt)
                    text = response.text.strip()
                    if text:
                        return text
                except:
                    continue
            return "SIN_CAMBIOS"
        except Exception as e:
            print(f"Error comparando datos: {e}")
            return "SIN_CAMBIOS"
