import pytest
import asyncio
from engine.ai_optimizer import get_ai_grid_params_batch, _ai_cache
from engine.fast_backtester import run_vectorized_backtest
from unittest.mock import patch, MagicMock

@pytest.mark.asyncio
async def test_ai_optimizer_cache():
    # Limpiar cache
    _ai_cache.clear()
    
    batch = [
        {"symbol": "BTC-USDT-SWAP", "avg_body_pct": 0.3},
        {"symbol": "ETH-USDT-SWAP", "avg_body_pct": 1.2}
    ]
    user_config = {"maxLeverage": 15.0}
    
    # 1era llamada: debe calcular e insertar en caché
    results1 = await get_ai_grid_params_batch(batch, user_config)
    assert "BTC-USDT-SWAP" in results1
    assert "ETH-USDT-SWAP" in results1
    
    # Verificar heurística (Mock)
    assert results1["BTC-USDT-SWAP"]["grid_lines"] == 10
    assert results1["ETH-USDT-SWAP"]["grid_lines"] == 15
    
    assert "BTC-USDT-SWAP" in _ai_cache
    
    # 2da llamada: debe salir del caché
    # Modificamos manualmente la cache para ver si retorna el valor cacheado
    _ai_cache["BTC-USDT-SWAP"]["params"]["grid_lines"] = 999
    
    results2 = await get_ai_grid_params_batch(batch, user_config)
    
    assert results2["BTC-USDT-SWAP"]["grid_lines"] == 999

@pytest.mark.asyncio
async def test_fast_backtester_mocked():
    # Test usando fetch_ohlcv mockeado para evitar peticiones reales en CI
    import pandas as pd
    
    # Mock data: sube y baja
    mock_ohlcv = [
        [1600000000000, 100, 105, 95, 100, 10],
        [1600000060000, 100, 106, 99, 105, 10],
        [1600000120000, 105, 110, 100, 102, 10], # Sube, tocaría upper levels
        [1600000180000, 102, 102, 90, 95, 10],   # Baja, tocaría lower levels
    ] * 250 # Repetir para tener > 1000 velas
    
    # Símbolo con params AI
    symbols = [
        {
            "symbol": "BTC-USDT-SWAP",
            "cv": 0.5,
            "avg_body_pct": 0.3,
            "ai_params": {
                "grid_spacing_factor": 2.0, # 2%
                "grid_lines": 5,
                "leverage": 10.0
            }
        }
    ]
    
    # Mockear la clase okx de ccxt.async_support
    from unittest.mock import AsyncMock
    
    mock_exchange = MagicMock()
    mock_exchange.fetch_ohlcv = AsyncMock(return_value=mock_ohlcv)
    mock_exchange.close = AsyncMock()
    
    with patch('ccxt.async_support.okx', return_value=mock_exchange):
        results = await run_vectorized_backtest(
            "fake_api", "fake_secret", "fake_pass", symbols, sandbox=True, investment=1000
        )
        
        assert len(results) == 1
        assert results[0]['symbol'] == "BTC-USDT-SWAP"
        assert 'pnl' in results[0]
        assert 'leverage_used' in results[0]
        assert results[0]['leverage_used'] == 10.0
