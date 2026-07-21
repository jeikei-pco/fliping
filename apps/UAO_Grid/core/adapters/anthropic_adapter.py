import json
import logging
import urllib.request
from typing import Optional
from core.ports.ai_provider_port import AIProviderPort

logger = logging.getLogger("UAO_Sclaping.AnthropicAdapter")

class AnthropicAdapter(AIProviderPort):
    def __init__(self, api_key: str, url: str, model: str):
        self.api_key = api_key
        self.url = url
        self.model = model

    @property
    def provider_name(self) -> str:
        return f"Anthropic Proxy ({self.url} - {self.model})"

    def generate_json(self, prompt: str) -> Optional[str]:
        try:
            import os
            api_key = self.api_key or os.environ.get("CLAUDE_CODE_PROXY_API_KEY", "freecc").strip()
            
            body = {
                "model": self.model,
                "max_tokens": 1000,
                "system": "Eres un experto cuantitativo de IA. Respondes únicamente en JSON crudo sin comillas invertidas ni bloques de markdown ni explicaciones previas.",
                "messages": [{"role": "user", "content": prompt}],
            }
            headers = {
                "content-type": "application/json", 
                "x-api-key": api_key, 
                "anthropic-version": "2023-06-01"
            }
        
            request = urllib.request.Request(
                self.url,
                data=json.dumps(body).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            
            with urllib.request.urlopen(request, timeout=20) as response:
                raw = response.read().decode("utf-8", "replace")
                
                # Parseo Anthropic (SSE o JSON standard)
                content = ""
                try:
                    payload = json.loads(raw)
                    for item in payload.get("content", []):
                        if isinstance(item, dict) and item.get("type") == "text":
                            content += item.get("text", "")
                    return content if content else None
                except json.JSONDecodeError:
                    for line in raw.splitlines():
                        if line.startswith("data: "):
                            try:
                                data = json.loads(line[6:])
                                if data.get("type") == "content_block_delta":
                                    delta = data.get("delta", {})
                                    if delta.get("type") == "text_delta":
                                        content += delta.get("text", "")
                                elif data.get("type") == "message" and "content" in data:
                                    for item in data.get("content", []):
                                        if isinstance(item, dict) and item.get("type") == "text":
                                            content += item.get("text", "")
                            except Exception:
                                pass
                    return content if content else None
        except Exception as e:
            logger.warning(f"Fallo proveedor {self.provider_name}: {e}")
            return None
