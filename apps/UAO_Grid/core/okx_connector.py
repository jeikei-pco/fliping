"""
okx_connector.py — Módulo de conexión y filtrado de mercados OKX con CCXT.

Responsabilidades:
  1. Inicializar el cliente ccxt.okx en modo 'swap' (futuros perpetuos)
  2. Filtrar mercados activos con liquidación en USDT
  3. Obtener volúmenes de tickers para filtrado de liquidez

Seguridad:
  - No hardcodea credenciales. La API key de OKX se lee del entorno o
    del endpoint de credenciales de Imperio (CREDENTIALS_API_URL).
  - Todas las excepciones son capturadas y logueadas con mensajes genéricos
    hacia el usuario; el detalle solo va al logger.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Dict, List, Optional

import ccxt
import urllib.request
import json

logger = logging.getLogger("UAO_Sclaping.okx_connector")

# ── Constantes ────────────────────────────────────────────────────────────────
_DEFAULT_TIMEOUTS = {
    "fetchTickers":      10_000,   # ms
    "fetchOHLCV":        15_000,
    "loadMarkets":       20_000,
}


def inicializar_okx(api_key: str = "", api_secret: str = "", passphrase: str = "") -> ccxt.Exchange:
    """
    Crea e inicializa un cliente ccxt.okx en modo swap/perpetuo.

    Los parámetros de credenciales son opcionales; si no se proporcionan se
    intenta leer de variables de entorno (OKX_API_KEY, OKX_API_SECRET, OKX_API_PASSPHRASE).
    Si no existen, consulta la API interna de credenciales de Imperio.
    """
    
    # Intento 1: Argumentos directos o entorno
    ak = api_key or os.getenv("OKX_API_KEY", "")
    sec = api_secret or os.getenv("OKX_API_SECRET", "")
    pw = passphrase or os.getenv("OKX_API_PASSPHRASE", "")
    
    # Intento 2: API interna
    if not ak or not sec:
        url = os.getenv("CREDENTIALS_API_URL", "http://localhost:80/api/internal/credentials/exchanges").strip()
        token = os.getenv("CREDENTIALS_API_TOKEN", "").strip() or os.getenv("IMPERIO_CREDENTIALS_API_TOKEN", "").strip()
        if not token and os.path.exists("/imperio_shared/credentials_api.token"):
            with open("/imperio_shared/credentials_api.token", "r") as f:
                token = f.read().strip()
                
        try:
            logger.info(f"🔑 Consultando credenciales de OKX a la API interna: {url}")
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as response:
                payload = json.loads(response.read().decode("utf-8"))
                creds = payload.get("credentials", {}).get("okx", {})
                if creds:
                    ak = ak or creds.get("api_key", "")
                    sec = sec or (creds.get("api_secret", "") or creds.get("secret", ""))
                    pw = pw or (creds.get("password", "") or creds.get("passphrase", ""))
                    logger.info("✅ Credenciales de OKX obtenidas correctamente de la API.")
        except Exception as e:
            logger.warning(f"⚠️ No se pudo obtener credenciales de OKX por la API: {e}")

    api_key = ak
    api_secret = sec
    passphrase = pw

    config: Dict = {
        "options": {
            "defaultType": "swap",   # Futuros perpetuos
        },
        "timeout":       20_000,
        "enableRateLimit": True,
    }

    if api_key:
        config["apiKey"]     = api_key
        config["secret"]     = api_secret
        config["password"]   = passphrase

    exchange = ccxt.okx(config)

    logger.info("🔌 Cargando mercados de OKX (modo swap)...")
    exchange.load_markets()
    logger.info("✅ Mercados cargados: %d contratos disponibles", len(exchange.markets))

    return exchange


def obtener_futuros_usdt(exchange: ccxt.Exchange) -> List[str]:
    """
    Filtra los contratos swap activos liquidados en USDT.

    Retorna lista de símbolos CCXT con la sintaxis SÍMBOLO/USDT:USDT.
    """
    simbolos = [
        symbol
        for symbol, market in exchange.markets.items()
        if (
            market.get("active")
            and market.get("settle") == "USDT"
            and market.get("type") == "swap"
        )
    ]
    logger.info("📋 Contratos USDT-settle activos: %d", len(simbolos))
    return simbolos


def filtrar_por_volumen(
    exchange: ccxt.Exchange,
    simbolos: List[str],
    min_volume_usdt: float = 1_000_000.0,
) -> List[str]:
    """
    Descarta símbolos con volumen diario inferior al umbral mínimo.

    Hace una sola llamada a fetchTickers para eficiencia (un solo request).
    Los errores individuales se ignoran silenciosamente para mantener la
    robustez del escaneo.

    Args:
        exchange:        Cliente ccxt.Exchange
        simbolos:        Lista de símbolos a filtrar
        min_volume_usdt: Volumen mínimo diario en USDT

    Returns:
        Sublista de simbolos con volumen suficiente, ordenados de mayor a menor.
    """
    logger.info("📊 Obteniendo tickers para filtrado de liquidez (%d símbolos)...", len(simbolos))

    try:
        # Llamada batch a los tickers: más eficiente que fetchTicker individual
        tickers = exchange.fetch_tickers(simbolos)
    except Exception as exc:  # noqa: BLE001
        logger.warning("⚠️ Error al obtener tickers batch: %s. Usando todos los símbolos.", exc)
        return simbolos

    filtrados: List[tuple] = []
    for symbol in simbolos:
        ticker = tickers.get(symbol, {})
        # 'quoteVolume' = volumen en USDT para pares /USDT:USDT
        vol = ticker.get("quoteVolume") or ticker.get("baseVolume") or 0.0
        try:
            vol = float(vol)
        except (TypeError, ValueError):
            vol = 0.0

        if vol >= min_volume_usdt:
            filtrados.append((symbol, vol))

    # Ordenar de mayor a menor volumen
    filtrados.sort(key=lambda x: x[1], reverse=True)
    resultado = [s for s, _ in filtrados]

    logger.info(
        "✅ Símbolos con volumen ≥ $%.0f USDT: %d / %d",
        min_volume_usdt, len(resultado), len(simbolos)
    )
    return resultado
