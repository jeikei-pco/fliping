"""
ai_optimizer.py — Worker Daemon para optimización IA.
Consulta a Claude periódicamente y guarda los overrides en SQLite.
"""
import json
import logging
import os
import urllib.request
import urllib.error
import re
import time
import threading
from typing import Dict, Any

from core.database import Database

logger = logging.getLogger("UAO_Sclaping.AIOptimizer")

class AIOptimizerWorker(threading.Thread):
    def __init__(self, db: Database):
        super().__init__(daemon=True)
        self.db = db
        self.intervalo = float(os.getenv("AI_OPTIMIZER_INTERVAL_HOURS", 24)) * 3600
        self.api_url = os.getenv("AI_OPTIMIZER_API_URL", "http://127.0.0.1:8082/v1/messages")

    def run(self):
        logger.info(f"🤖 AI Optimizer iniciado. Consultará IA cada {self.intervalo/3600:.1f}h")
        # Esperar un poco antes de la primera consulta
        time.sleep(60)
        
        while True:
            try:
                self._optimizar_global()
            except Exception as e:
                logger.error(f"❌ Error en AI Optimizer: {e}")
                
            time.sleep(self.intervalo)

    def _optimizar_global(self):
        logger.info("🧠 Solicitando parámetros óptimos globales a la IA...")
        proxy_key = os.getenv("CLAUDE_CODE_PROXY_API_KEY", "freecc").strip()
        
        prompt = """
Eres un experto institucional en Grid Trading para criptomonedas perpetuas.
Analiza el estado actual del mercado macro. 
Devuelve los mejores parámetros globales sugeridos para la malla.
Las claves deben ser exactas:
- GRID_ATR_MULTIPLIER (float, ej. 1.5)
- GRID_NUM_LINEAS_LADO (int, ej. 5)

IMPORTANTE: Responde ESTRICTAMENTE con un único objeto JSON válido.
Ejemplo:
{"GRID_ATR_MULTIPLIER": 1.5, "GRID_NUM_LINEAS_LADO": 5}
"""
        body = {
            "model": os.getenv("CLAUDE_CODE_MODEL", "claude-3-5-haiku-20241022"),
            "max_tokens": 1000,
            "system": "Eres un experto cuantitativo de IA. Respondes únicamente en JSON crudo sin comillas invertidas ni bloques de markdown ni explicaciones previas.",
            "messages": [{"role": "user", "content": prompt}],
        }
        
        request = urllib.request.Request(
            self.api_url,
            data=json.dumps(body).encode("utf-8"),
            headers={"content-type": "application/json", "x-api-key": proxy_key, "anthropic-version": "2023-06-01"},
            method="POST",
        )
        
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read().decode("utf-8", "replace")
            content = ""
            try:
                # Intento 1: Parsear como JSON estándar (si no es streaming)
                payload = json.loads(raw)
                for item in payload.get("content", []):
                    if isinstance(item, dict) and item.get("type") == "text":
                        content += item.get("text", "")
            except json.JSONDecodeError:
                # Intento 2: Parsear como Server-Sent Events (SSE)
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
            
            # ── Extracción robusta de JSON ────────────────────────────────────
            # Buscar TODOS los bloques {...} en el contenido (balanced brackets)
            def extraer_jsons(texto: str):
                """Extrae todos los candidatos JSON usando conteo de llaves."""
                candidatos = []
                depth = 0
                inicio = -1
                for i, c in enumerate(texto):
                    if c == '{':
                        if depth == 0:
                            inicio = i
                        depth += 1
                    elif c == '}':
                        depth -= 1
                        if depth == 0 and inicio != -1:
                            candidatos.append(texto[inicio:i+1])
                            inicio = -1
                return candidatos

            CLAVES_VALIDAS = {"GRID_ATR_MULTIPLIER", "GRID_NUM_LINEAS_LADO"}

            params = None
            for candidato in extraer_jsons(content):
                try:
                    parsed = json.loads(candidato)
                    # Aceptar solo si tiene al menos una clave conocida
                    if any(k in parsed for k in CLAVES_VALIDAS):
                        # Filtrar solo las claves que conocemos
                        params = {k: v for k, v in parsed.items() if k in CLAVES_VALIDAS}
                        break
                except json.JSONDecodeError:
                    continue

            if params:
                logger.info(f"🤖 IA sugiere overrides: {params}")
                self.db.update_config_overrides(params)
            else:
                logger.warning(f"⚠️ IA no devolvió JSON válido con claves conocidas. Contenido recibido: {content[:200]}")

