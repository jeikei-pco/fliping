import os
import json
import logging
from typing import Optional
from openai import OpenAI
from core.ports.ai_provider_port import AIProviderPort

logger = logging.getLogger("UAO_Sclaping.OpenRouterAdapter")

class OpenRouterAdapter(AIProviderPort):
    def __init__(self, api_key: str = None, model: str = "google/gemini-2.0-flash-001:free", url: str = None, **kwargs):
        self.api_key = api_key or os.getenv("OPENROUTER_API_KEY", "")
        self.model = model
        self.client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=self.api_key,
        )

    @property
    def provider_name(self) -> str:
        return f"OpenRouter ({self.model})"

    def generate_json(self, prompt: str) -> Optional[str]:
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are a JSON-only response engine."},
                    {"role": "user", "content": prompt}
                ],
                response_format={"type": "json_object"}
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.warning(f"Fallo proveedor {self.provider_name}: {e}")
            return None