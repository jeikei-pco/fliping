import logging
from typing import Optional
from core.ports.ai_provider_port import AIProviderPort

logger = logging.getLogger("UAO_Sclaping.OpenRouterAdapter")

class OpenRouterAdapter(AIProviderPort):
    def __init__(self, api_key: str, url: str = "https://openrouter.ai/api/v1", model: str = "google/gemini-2.0-flash-001"):
        self.api_key = api_key
        self.url = url
        self.model = model

    @property
    def provider_name(self) -> str:
        return f"OpenRouter ({self.model})"

    def generate_json(self, prompt: str) -> Optional[str]:
        try:
            from openrouter import OpenRouter
            import os
            
            # El api_key puede provenir del Factory o directamente de os.environ
            api_key = self.api_key or os.environ.get("OPENROUTER_API_KEY")
            client = OpenRouter(api_key=api_key)
            
            kwargs = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": "Eres un experto cuantitativo de IA. Respondes únicamente en JSON crudo sin comillas invertidas ni bloques de markdown ni explicaciones previas."},
                    {"role": "user", "content": prompt}
                ],
                "max_tokens": 1000,
                "response_format": {"type": "json_object"}
            }
                        
            response = client.chat.completions.create(**kwargs)
            return response.choices[0].message.content
        except Exception as e:
            logger.warning(f"Fallo proveedor {self.provider_name}: {e}")
            return None
