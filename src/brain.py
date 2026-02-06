import google.generativeai as genai
from src.config import GEMINI_API_KEY

class RankingBrain:
    def __init__(self):
        genai.configure(api_key=GEMINI_API_KEY)
        # Lista de prioridad de modelos: El más avanzado primero
        self.preferred_models = ['gemini-2.5-flash', 'gemini-2.0-flash', 'gemini-1.5-pro', 'gemini-flash-latest']
        self.model = self._get_best_available_model()

    def _get_best_available_model(self):
        """Intenta inicializar el mejor modelo disponible."""
        # Por defecto instanciamos el primero, la validación ocurre al llamar generate_content
        # pero aquí dejamos configurado el "intento" principal.
        print(f"🧠 Configurando cerebro con prioridad: {self.preferred_models[0]}")
        return genai.GenerativeModel(self.preferred_models[0])

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
                return response.text
            except Exception as e:
                print(f"⚠️ Error con {model_name}: {e}. Intentando siguiente modelo...")
                continue
        
        return "Lo siento, todos los modelos de IA están ocupados o no disponibles en este momento."
