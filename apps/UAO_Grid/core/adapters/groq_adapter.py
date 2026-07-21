import logging
from typing import Optional
from core.ports.ai_provider_port import AIProviderPort

logger = logging.getLogger("UAO_Sclaping.GroqAdapter")

class GroqAdapter(AIProviderPort):
    def __init__(self, api_key: str, url: str = "https://api.groq.com/openai/v1", model: str = "llama3-70b-8192"):
        self.api_key = api_key
        self.url = url
        self.model = model

    @property
    def provider_name(self) -> str:
        return f"Groq ({self.model})"

    def generate_json(self, prompt: str) -> Optional[str]:
        try:
            from groq import Groq
            import os
            
            # El api_key puede provenir del Factory o directamente de os.environ
            api_key = self.api_key or os.environ.get("GROQ_API_KEY")
            client = Groq(api_key=api_key)
            
            kwargs = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": "Eres un experto cuantitativo de IA. Respondes únicamente en JSON crudo sin comillas invertidas ni bloques de markdown ni explicaciones previas."},
                    {"role": "user", "content": prompt}
                ],
                "max_tokens": 1000
            }
            
            response = client.chat.completions.create(**kwargs)
            return response.choices[0].message.content
        except Exception as e:
            logger.warning(f"Fallo proveedor {self.provider_name}: {e}")
            return None
