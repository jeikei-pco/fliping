import logging
from typing import Optional
from core.ports.ai_provider_port import AIProviderPort

logger = logging.getLogger("UAO_Sclaping.OpenAIAdapter")

class OpenAIAdapter(AIProviderPort):
    def __init__(self, api_key: str, url: str, model: str):
        self.api_key = api_key
        self.url = url
        self.model = model

    @property
    def provider_name(self) -> str:
        return f"OpenAI Compatible ({self.url} - {self.model})"

    def generate_json(self, prompt: str) -> Optional[str]:
        try:
            import openai
            import os
            
            # El api_key puede provenir del Factory o directamente de os.environ
            api_key = self.api_key or os.environ.get("OPENAI_API_KEY")
            base_url = self.url.replace("/chat/completions", "") if self.url else None
            client = openai.OpenAI(api_key=api_key, base_url=base_url)
            
            kwargs = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": "Eres un experto cuantitativo de IA. Respondes únicamente en JSON crudo sin comillas invertidas ni bloques de markdown ni explicaciones previas."},
                    {"role": "user", "content": prompt}
                ],
                "max_tokens": 1000
            }
            
            # Usar response_format solo si no es Groq (Groq aveces falla con response_format según la lógica original)
            if "groq" not in (self.url or ""):
                kwargs["response_format"] = {"type": "json_object"}
                
            response = client.chat.completions.create(**kwargs)
            return response.choices[0].message.content
        except Exception as e:
            logger.warning(f"Fallo proveedor {self.provider_name}: {e}")
            return None
