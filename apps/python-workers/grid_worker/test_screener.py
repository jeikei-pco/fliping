import asyncio
import os
import logging
from engine.consistency_screener import scan_all_usdt_futures

# Configuramos el logging para ver los mensajes por consola
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

async def run_test():
    # Toma las credenciales desde las variables de entorno (puedes setearlas antes de ejecutar)
    # Ejemplo:
    # $env:OKX_API_KEY="tu_api_key"
    # $env:OKX_SECRET="tu_secret"
    # $env:OKX_PASSPHRASE="tu_passphrase"
    # python test_screener.py
    
    api_key = os.getenv("OKX_API_KEY", "placeholder_api_key")
    secret = os.getenv("OKX_SECRET", "placeholder_secret")
    passphrase = os.getenv("OKX_PASSPHRASE", "placeholder_passphrase")
    
    print("Iniciando prueba del Screener...")
    
    try:
        # Ejecutamos el screener en modo sandbox por defecto con un timeframe más corto para probar
        top_symbols = await scan_all_usdt_futures(
            api_key=api_key,
            secret=secret,
            passphrase=passphrase,
            sandbox=True,
            timeframe="5m",
            limit=100 # Se requiere mínimo 100 velas según la lógica del screener
        )
        
        print("\n--- RESULTADOS DEL SCREENER ---")
        if top_symbols:
            print(f"Se encontraron {len(top_symbols)} símbolos que pasaron la validación:")
            for item in top_symbols:
                print(f" - {item['symbol']} | Tendencia: {item['trend']} | CV: {item.get('cv')} | Avg Body Pct: {item.get('avg_body_pct', 0):.4f}")
        else:
            print("No se encontraron símbolos o hubo un error en la obtención (revisa las credenciales).")
            
    except Exception as e:
        print(f"Error durante la ejecución del test: {e}")

if __name__ == "__main__":
    asyncio.run(run_test())
