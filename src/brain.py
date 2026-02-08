import google.generativeai as genai
from src.config import GEMINI_API_KEY

class RankingBrain:
    def __init__(self):
        if not GEMINI_API_KEY:
            print("⚠️ ADVERTENCIA: GEMINI_API_KEY no encontrada. El bot no podrá generar respuestas.")
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
        print(f"🧠 Configurando cerebro con prioridad: {self.preferred_models[0]}")
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
        Eres el 'Ranking AI Agent', un experto en modelos de lenguaje (LLMs) y agentes de IA.
        Tu objetivo es informar a los usuarios sobre los mejores modelos hoy basándote en datos reales y "sucios" (texto crudo de webs).
        
        CONTEXTO DE RANKINGS ACTUALES (Extraído directamente de la web):
        {context_data}
        
        INSTRUCCIONES:
        1. Analiza el texto crudo para identificar las listas de ranking.
        2. Responde en español de forma natural y conversacional.
        3. Usa negritas para destacar nombres de modelos.
        4. Si la información no está clara, usa tu conocimiento general pero aclara la fuente.
        5. Sé directo pero amable.
        """
        
        prompt = f"{system_prompt}\n\nUsuario pregunta: {user_query}"
        
        # Lógica de reintento con diferentes modelos
        for model_name in self.preferred_models:
            try:
                current_model = genai.GenerativeModel(model_name)
                response = current_model.generate_content(prompt)
                
                if response and response.candidates:
                    # Verificar si el primer candidato tiene texto
                    if response.candidates[0].content.parts:
                        return response.text
                    else:
                        print(f"⚠️ El modelo {model_name} devolvió una respuesta vacía (posiblemente bloqueada).")
                else:
                    print(f"⚠️ El modelo {model_name} no devolvió candidatos.")
                
            except Exception as e:
                print(f"⚠️ Error con {model_name}: {e}. Intentando siguiente modelo...")
                continue
        
        return "Lo siento, todos los modelos de IA están ocupados o no disponibles en este momento."
