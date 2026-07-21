"""
ia_optimizer.py — Optimizador IA. V2.

RESPONSABILIDAD ÚNICA: Recibir TradingMetrics ya calculadas, consultar la IA
y retornar IAOverrides. No calcula métricas. No lee la base de datos.

Cambios respecto a V1:
  - Recibe TradingMetrics (precalculado por DB.get_trading_metrics)
  - Retorna IAOverrides tipado en lugar de guardar en DB directamente
  - Multi-provider fallback: Gemini → OpenRouter → Groq → OpenAI → Claude proxy
  - Aprende de backtests históricos (recent_backtests en TradingMetrics)
  - Historial de overrides persistido por el orquestador (no la IA)
  - Lógica de blacklist consultada externamente (no duplicada aquí)
  - import datetime movido al top del módulo
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from core.config import IAConfig
from core.models import IAOverrides, TradingMetrics

logger = logging.getLogger("UAO_Grid.IAOptimizer")

# Límites de seguridad — la IA NUNCA puede exceder estos valores
_CLAMP_LIMITS: Dict[str, tuple] = {
    "grid_step_pct":       (0.15,  5.0),   # % absoluto entre líneas
    "grid_density_factor": (0.75,  1.25),
    "leverage_factor":     (0.80,  1.15),
    "capital_factor":      (0.70,  1.30),
    "max_leverage":        (2,     75),
    "min_score":           (10.0,  95.0),
    "min_consistency":     (0.0,   0.99),
    "min_oscillation":     (0.0,   5.0),
}

_CLAVES_VALIDAS = {
    "GRID_STEP_PCT", "GRID_DENSITY_FACTOR", "LEVERAGE_FACTOR", "CAPITAL_FACTOR",
    "MAX_LEVERAGE", "MIN_SCORE", "MIN_CONSISTENCY", "MIN_OSCILLATION",
}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _clamp(val: float, min_val: float, max_val: float) -> float:
    return max(min_val, min(float(val), max_val))


def _extraer_json(texto: str) -> Optional[Dict[str, Any]]:
    """Extrae el primer JSON válido del texto usando conteo de llaves."""
    depth = 0
    inicio = -1
    for i, c in enumerate(texto):
        if c == "{":
            if depth == 0:
                inicio = i
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0 and inicio != -1:
                fragmento = texto[inicio : i + 1]
                try:
                    parsed = json.loads(fragmento)
                    if any(k in parsed for k in _CLAVES_VALIDAS):
                        return {k: v for k, v in parsed.items() if k in _CLAVES_VALIDAS}
                except json.JSONDecodeError:
                    inicio = -1
    return None


def _aplicar_clamps(params: Dict[str, Any]) -> IAOverrides:
    """Convierte el dict de la IA a IAOverrides con clamps de seguridad."""
    def f(key: str, default: float) -> float:
        return _clamp(params[key], *_CLAMP_LIMITS[key]) if key in params else default

    def i(key: str, default: int) -> int:
        return int(_clamp(params[key], *_CLAMP_LIMITS[key])) if key in params else default

    return IAOverrides(
        grid_step_pct        = _clamp(float(params["GRID_STEP_PCT"]), *_CLAMP_LIMITS["grid_step_pct"])
                                if "GRID_STEP_PCT" in params else None,
        grid_density_factor  = f("GRID_DENSITY_FACTOR", 1.0),
        leverage_factor      = f("LEVERAGE_FACTOR", 1.0),
        capital_factor       = f("CAPITAL_FACTOR", 1.0),
        max_leverage         = i("MAX_LEVERAGE", 20),
        min_score            = f("MIN_SCORE", 30.0),
        min_consistency      = f("MIN_CONSISTENCY", 0.0),
        min_oscillation      = f("MIN_OSCILLATION", 0.0),
        timestamp            = datetime.now(timezone.utc).isoformat(),
    )


def _construir_prompt(metrics: TradingMetrics) -> str:
    """Construye el prompt para la IA con contexto cuantitativo completo."""
    ctx = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "bot_metrics_last_trades": {
            "total_trades":            metrics.total_trades,
            "win_rate_pct":            metrics.win_rate_pct,
            "profit_factor":           metrics.profit_factor,
            "net_pnl":                 metrics.net_pnl,
            "avg_profit":              metrics.avg_profit,
            "avg_loss":                metrics.avg_loss,
            "long_win_rate":           metrics.long_win_rate,
            "short_win_rate":          metrics.short_win_rate,
            "max_consecutive_wins":    metrics.max_consecutive_wins,
            "max_consecutive_losses":  metrics.max_consecutive_losses,
            "pnl_by_utc_hour":         metrics.pnl_by_utc_hour,
        },
        "per_symbol_performance":   metrics.per_symbol,
        "recent_backtest_history":  metrics.recent_backtests[:20],  # Top 20 más recientes
    }

    return f"""
Analiza el rendimiento cuantitativo de un bot de grid trading en futuros perpetuos.
El bot opera en modo NEUTRAL, LONG y SHORT dependiendo de la tendencia detectada.

Contexto:
{json.dumps(ctx, indent=2)}

Tu tarea: Decide ajustes FINOS sobre los parámetros matemáticos.
- No cambies parámetros más del ±20% del valor neutral (1.0).
- Si el bot va bien (PF > 1.5, WR > 60%), mantén parámetros conservadores.
- Si hay pérdidas consecutivas, reduce leverage_factor y capital_factor.
- Si los backtests históricos muestran alta consistencia, puedes aumentar densidad.

Responde ÚNICAMENTE con un JSON válido con estas claves exactas (sin markdown, sin texto extra):
- GRID_STEP_PCT (float, % de distancia entre líneas, ej: 0.25 para 0.25%)
- GRID_DENSITY_FACTOR (float, multiplicador de líneas, ej: 1.08)
- LEVERAGE_FACTOR (float, multiplicador de apalancamiento, ej: 0.95)
- CAPITAL_FACTOR (float, multiplicador de capital, ej: 1.05)
- MAX_LEVERAGE (int, apalancamiento máximo absoluto, ej: 15)
- MIN_SCORE (float, score mínimo del analizador, ej: 35.0)
- MIN_CONSISTENCY (float, consistencia mínima [0-1], ej: 0.55)
- MIN_OSCILLATION (float, oscilación mínima, ej: 1.5)

Ejemplo de respuesta válida:
{{"GRID_STEP_PCT": 0.25, "GRID_DENSITY_FACTOR": 1.05, "LEVERAGE_FACTOR": 0.95, "CAPITAL_FACTOR": 1.0, "MAX_LEVERAGE": 15, "MIN_SCORE": 35.0, "MIN_CONSISTENCY": 0.55, "MIN_OSCILLATION": 1.5}}
""".strip()


# ─────────────────────────────────────────────────────────────────────────────
# Llamadas a proveedores de IA
# ─────────────────────────────────────────────────────────────────────────────

def _llamar_gemini(api_key: str, prompt: str, model: str = "gemini-2.0-flash") -> Optional[str]:
    try:
        from google import genai
        from google.genai import types

        client   = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=(
                    "Eres un experto cuantitativo de trading algorítmico. "
                    "Respondes únicamente en JSON crudo sin markdown ni explicaciones."
                ),
                temperature=0.2,
            ),
        )
        return response.text
    except Exception as exc:
        logger.warning("Gemini %s falló: %s", model, exc)
        return None


def _llamar_openai_compatible(
    api_key: str,
    base_url: str,
    model: str,
    prompt: str,
    json_mode: bool = True,
) -> Optional[str]:
    try:
        import openai

        client = openai.OpenAI(api_key=api_key, base_url=base_url)
        kwargs: Dict[str, Any] = {
            "model":    model,
            "messages": [
                {
                    "role":    "system",
                    "content": (
                        "Eres un experto cuantitativo de trading algorítmico. "
                        "Respondes únicamente en JSON crudo sin markdown ni explicaciones."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "max_tokens": 1000,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        response = client.chat.completions.create(**kwargs)
        return response.choices[0].message.content
    except Exception as exc:
        logger.warning("OpenAI-compat %s falló: %s", base_url, exc)
        return None


def _llamar_anthropic_proxy(
    api_key: str,
    api_url: str,
    model: str,
    prompt: str,
) -> Optional[str]:
    import urllib.request

    body = {
        "model":      model,
        "max_tokens": 1000,
        "system":     (
            "Eres un experto cuantitativo de trading algorítmico. "
            "Respondes únicamente en JSON crudo sin markdown ni explicaciones."
        ),
        "messages": [{"role": "user", "content": prompt}],
    }
    headers = {
        "content-type":      "application/json",
        "x-api-key":         api_key,
        "anthropic-version": "2023-06-01",
    }
    req = urllib.request.Request(
        api_url,
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            raw = resp.read().decode("utf-8", "replace")
            payload = json.loads(raw)
            content = ""
            for item in payload.get("content", []):
                if isinstance(item, dict) and item.get("type") == "text":
                    content += item.get("text", "")
            return content or None
    except Exception as exc:
        logger.warning("Anthropic proxy falló: %s", exc)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Función principal (sin estado)
# ─────────────────────────────────────────────────────────────────────────────

def optimizar(
    metrics: TradingMetrics,
    ia_config: IAConfig,
) -> Optional[IAOverrides]:
    """
    Consulta la IA con las métricas de rendimiento y retorna los overrides sugeridos.

    Args:
        metrics: TradingMetrics calculadas por Database.get_trading_metrics().
        ia_config: Configuración IA desde AppConfig (API keys, URLs).

    Returns:
        IAOverrides con los ajustes sugeridos, o None si todos los proveedores fallaron.
    """
    if metrics.total_trades < 10:
        logger.info("⏭️ IA Optimizer: pocos trades (%d) — saltando optimización", metrics.total_trades)
        return None

    prompt    = _construir_prompt(metrics)
    contenido = None
    modelo_usado = "desconocido"

    # ── Orden de intentos: Gemini → OpenRouter → Groq → OpenAI → Claude proxy ─

    # 1. Gemini (múltiples keys)
    for key in ia_config.gemini_api_keys:
        contenido = _llamar_gemini(key, prompt, model="gemini-2.0-flash")
        if contenido:
            modelo_usado = "gemini-2.0-flash"
            break

    # 2. OpenRouter
    if not contenido:
        for key in ia_config.openrouter_api_keys:
            contenido = _llamar_openai_compatible(
                api_key=key,
                base_url="https://openrouter.ai/api/v1",
                model="google/gemini-2.0-flash-001",
                prompt=prompt,
            )
            if contenido:
                modelo_usado = "openrouter/gemini-2.0-flash"
                break

    # 3. Groq
    if not contenido and ia_config.groq_api_key:
        contenido = _llamar_openai_compatible(
            api_key=ia_config.groq_api_key,
            base_url="https://api.groq.com/openai/v1",
            model="llama3-70b-8192",
            prompt=prompt,
            json_mode=False,  # Groq no soporta json_object en todos los modelos
        )
        if contenido:
            modelo_usado = "groq/llama3-70b"

    # 4. OpenAI directo
    if not contenido:
        for key in ia_config.openai_api_keys:
            contenido = _llamar_openai_compatible(
                api_key=key,
                base_url="https://api.openai.com/v1",
                model="gpt-4o-mini",
                prompt=prompt,
            )
            if contenido:
                modelo_usado = "openai/gpt-4o-mini"
                break

    # 5. Anthropic/Claude proxy local
    if not contenido:
        contenido = _llamar_anthropic_proxy(
            api_key="freecc",
            api_url=ia_config.api_url,
            model="claude-3-5-haiku-20241022",
            prompt=prompt,
        )
        if contenido:
            modelo_usado = "claude-haiku-local"

    if not contenido:
        logger.warning("⚠️ Todos los proveedores de IA fallaron — sin overrides aplicados")
        return None

    # ── Parsear y validar ─────────────────────────────────────────────────────
    params = _extraer_json(contenido)
    if not params:
        logger.warning("⚠️ IA no devolvió JSON válido. Contenido: %s", contenido[:300])
        return None

    overrides = _aplicar_clamps(params)
    overrides = IAOverrides(
        grid_step_pct       = overrides.grid_step_pct,
        grid_density_factor = overrides.grid_density_factor,
        leverage_factor     = overrides.leverage_factor,
        capital_factor      = overrides.capital_factor,
        max_leverage        = overrides.max_leverage,
        min_score           = overrides.min_score,
        min_consistency     = overrides.min_consistency,
        min_oscillation     = overrides.min_oscillation,
        timestamp           = datetime.now(timezone.utc).isoformat(),
        model_used          = modelo_usado,
        confidence          = 1.0,
    )

    logger.info(
        "🤖 IA [%s] → step=%s%% | density=%.2f | lev=%.2f | cap=%.2f | max_lev=%d | min_score=%.1f",
        modelo_usado,
        f"{overrides.grid_step_pct:.3f}" if overrides.grid_step_pct else "auto",
        overrides.grid_density_factor,
        overrides.leverage_factor,
        overrides.capital_factor,
        overrides.max_leverage,
        overrides.min_score,
    )
    return overrides


# ─────────────────────────────────────────────────────────────────────────────
# Worker Daemon (thread)
# ─────────────────────────────────────────────────────────────────────────────

class IAOptimizerWorker(threading.Thread):
    """
    Hilo daemon que ejecuta optimizaciones periódicas.
    Llama a optimizar() y publica el resultado via callback.
    El orquestador provee el callback y persiste los overrides.
    """

    def __init__(
        self,
        ia_config: IAConfig,
        get_metrics_fn,       # callable() → TradingMetrics
        on_overrides_fn,      # callable(IAOverrides) → None
    ) -> None:
        super().__init__(daemon=True, name="IAOptimizerWorker")
        self.ia_config       = ia_config
        self.get_metrics_fn  = get_metrics_fn
        self.on_overrides_fn = on_overrides_fn
        self.intervalo_s     = ia_config.interval_hours * 3600
        self._stop_event     = threading.Event()

    def run(self) -> None:
        logger.info(
            "🤖 IAOptimizerWorker iniciado — ciclo cada %.1fh",
            self.ia_config.interval_hours,
        )
        time.sleep(60)  # Espera inicial antes de primera consulta

        while not self._stop_event.is_set():
            try:
                metrics  = self.get_metrics_fn()
                overrides = optimizar(metrics, self.ia_config)
                if overrides:
                    self.on_overrides_fn(overrides)
            except Exception as exc:
                logger.error("❌ IAOptimizerWorker error: %s", exc)

            self._stop_event.wait(timeout=self.intervalo_s)

    def stop(self) -> None:
        self._stop_event.set()
