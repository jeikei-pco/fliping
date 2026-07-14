import sys
import os
import asyncio
import logging

# Agregar el directorio padre al PYTHONPATH para poder importar 'engine'
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from engine.okx_ws import OkxWsClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("TestOkxWs")

# Cargar el .env local de tests
env_path = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(env_path):
    with open(env_path, "r") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ[key.strip()] = val.strip().strip("\"'")

async def main():
    api_key = os.getenv("OKX_API_KEY_DEMO")
    secret = os.getenv("OKX_API_SECRET_DEMO")
    passphrase = os.getenv("OKX_PASSPHRASE_DEMO")

    if not api_key or not secret:
        logger.error("Credenciales de Demo no encontradas. Verifica apps/python-workers/grid_worker/tests/.env")
        return

    logger.info("Inicializando OkxWsClient en modo DEMO...")
    
    # Parámetros de prueba
    symbol = "ETH/USDT:USDT" # Usamos ETH/USDT:USDT como prueba
    ai_recommendation = {
        "grid_spacing_factor": 0.5, # Espaciado de 0.5%
        "grid_lines": 4,            # 4 líneas: 2 compras, 2 ventas (es simétrico)
        "leverage": 10              # Apalancamiento x10
    }
    
    client = OkxWsClient(
        api_key=api_key,
        secret=secret,
        passphrase=passphrase,
        sandbox=True, # MUY IMPORTANTE: Forzar modo pruebas
        symbol=symbol,
        timeframe="5m",
        base_capital=50.0, # Invertiremos 50 USDT base
        ai_recommendation=ai_recommendation,
        resume_existing_grid=False # Forzar la recreación del grid desde cero
    )
    
    task = None
    try:
        # Iniciar el bot (esto descarga históricas, configura el grid y abre websockets)
        task = asyncio.create_task(client.start())
        
        # Dejamos que el bot corra y reciba datos durante 20 segundos
        logger.info("⏳ Dejando que el bot opere por 20 segundos para verificar su comportamiento...")
        
        for i in range(20):
            await asyncio.sleep(1)
            # Imprimir algunas métricas en vivo cada 5 segundos
            if (i+1) % 5 == 0:
                logger.info(f"Métricas en vivo: {client.metrics}")
                
    except Exception as e:
        logger.error(f"Error durante el test: {e}")
        
    finally:
        logger.info("🛑 Deteniendo el Motor WS y limpiando sockets...")
        await client.stop()
        if task and not task.done():
            task.cancel()
        logger.info("Test finalizado con éxito.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Cancelado por el usuario.")
