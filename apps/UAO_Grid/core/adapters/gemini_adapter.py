import logging
from typing import Optional
from core.ports.ai_provider_port import AIProviderPort

logger = logging.getLogger("UAO_Sclaping.GeminiAdapter")

class GeminiAdapter(AIProviderPort):
    def __init__(self, api_key: str, model: str = "gemini-2.0-flash"):
        self.api_key = api_key
        self.model = model

    @property
    def provider_name(self) -> str:
        return f"Gemini ({self.model})"

    def generate_json(self, prompt: str) -> Optional[str]:
        try:
            from google import genai
            import os
            
            # Inicializar cliente de Google GenAI
            client = genai.Client(api_key=self.api_key or os.environ.get("GEMINI_API_KEY"))
            
            # Configuración de generación estándar para la SDK v0.2+
            config = {
                "temperature": 1.0,
                "top_p": 0.95,
                "response_mime_type": "application/json",
                "system_instruction": "Eres un experto cuantitativo de IA. Respondes únicamente en JSON crudo sin comillas invertidas ni bloques de markdown ni explicaciones previas."
            }
            
            # Llamada correcta al modelo de contenido
            response = client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=config,
            )
            
            if response and hasattr(response, "text") and response.text:
                return response.text
                
            return None
        except Exception as e:
            logger.warning(f"Fallo proveedor {self.provider_name}: {e}")
            return None