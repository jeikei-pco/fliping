import asyncio
import os
import ccxt.async_support as ccxt
import logging
from engine.okx_ws import _create_okx_exchange

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("TestETH")

async def test_eth_demo():
    # Obtener credenciales del entorno (o de donde prefieras)
    # Si no están en las variables de entorno, puedes pegarlas directamente aquí para probar.
    api_key = os.getenv("OKX_API_KEY_DEMO", "TU_API_KEY")
    secret = os.getenv("OKX_API_SECRET_DEMO", "TU_SECRET")
    passphrase = os.getenv("OKX_PASSPHRASE_DEMO", "TU_PASSPHRASE")

    if api_key == "TU_API_KEY":
        logger.warning("No se encontraron credenciales en el entorno. Reemplaza las variables en el script.")
        return

    # Usar sandbox=True para que siempre sea en DEMO
    exchange = _create_okx_exchange(api_key, secret, passphrase, sandbox=True)
    symbol = "ETH/USDT:USDT" # Formato CCXT para Perpetual Swaps (Futuros)

    try:
        logger.info("Conectando a OKX (Modo DEMO)...")
        await exchange.load_markets()
        
        market = exchange.market(symbol)
        contract_size = market.get('contractSize', 1)

        # 1. Ajustar el apalancamiento a 20x
        leverage = 20
        logger.info(f"Configurando apalancamiento a {leverage}x para {symbol}...")
        await exchange.set_leverage(leverage, symbol)

        # 2. Obtener precio actual para calcular contratos
        ticker = await exchange.fetch_ticker(symbol)
        current_price = ticker['last']
        logger.info(f"Precio actual de ETH: {current_price} USDT")

        # 3. Inversión inicial de 10 USDT
        inversion_usdt = 10.0
        posicion_total_usdt = inversion_usdt * leverage
        
        # Calcular el número de contratos
        # cantidad = (Inversión * Apalancamiento) / Precio / Tamaño del contrato
        raw_amount = (posicion_total_usdt / current_price) / contract_size
        amount = float(exchange.amount_to_precision(symbol, raw_amount))
        
        min_amount = float(market['limits']['amount']['min'] or 1.0)
        if amount < min_amount:
            logger.warning(f"La cantidad calculada ({amount}) es menor que el mínimo permitido ({min_amount}). Ajustando al mínimo.")
            amount = min_amount

        logger.info(f"Se creará una orden de compra (MARKET) por {amount} contratos de {symbol}.")
        logger.info(f"Inversión base: {inversion_usdt} USDT. Valor de posición (20x): ~{amount * current_price * contract_size:.2f} USDT.")

        # 4. Enviar la orden de mercado
        order = await exchange.create_market_buy_order(symbol, amount)
        
        logger.info("✅ Orden ejecutada con éxito!")
        logger.info(f"Detalles de la orden: ID={order['id']}, Status={order['status']}, Executed={order['filled']}")

    except Exception as e:
        logger.error(f"❌ Error al ejecutar el test en OKX Demo: {e}")
    finally:
        await exchange.close()

if __name__ == "__main__":
    asyncio.run(test_eth_demo())
