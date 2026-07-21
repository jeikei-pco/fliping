import logging
from typing import Optional
from core.ports.ai_provider_port import AIProviderPort

logger = logging.getLogger("UAO_Sclaping.GeminiAdapter")

class GeminiAdapter(AIProviderPort):
    def __init__(self, api_key: str, model: str = "models/gemini-3-flash-preview"):
        self.api_key = api_key
        self.model = model

    @property
    def provider_name(self) -> str:
        return f"Gemini ({self.model})"

    def generate_json(self, prompt: str) -> Optional[str]:
        try:
            from google import genai
            import os
            
            # El api_key puede provenir del Factory o directamente de os.environ
            client = genai.Client(api_key=self.api_key or os.environ.get("GEMINI_API_KEY"))
            
            tools = [
                {
                    'type': 'google_search',
                },
            ]
            
            generation_config = {
                'temperature': 1,
                'max_output_tokens': 65536,
                'top_p': 0.95,
                'thinking_level': 'high',
                'response_mime_type': 'application/json' # Sugerencia para forzar JSON
            }
            
            system_instruction = "Eres un experto cuantitativo de IA. Respondes únicamente en JSON crudo sin comillas invertidas ni bloques de markdown ni explicaciones previas."
            full_prompt = f"{system_instruction}\n\n{prompt}"
            
            interaction = client.interactions.create(
                model=self.model,
                input=full_prompt,
                tools=tools,
                generation_config=generation_config,
            )
            
            # Extraer el texto de la respuesta (interacción)
            if hasattr(interaction, "text") and interaction.text:
                return interaction.text
            elif hasattr(interaction, "steps") and interaction.steps:
                last_step = interaction.steps[-1]
                if hasattr(last_step, "text"):
                    return last_step.text
                return str(last_step)
            
            return str(interaction)
        except Exception as e:
            logger.warning(f"Fallo proveedor {self.provider_name}: {e}")
            return None
