"""
main.py — Entry point de la UAO_Grid
Inicia el orquestador leyendo el modo de ejecución desde .env
"""
import logging
import os
from dotenv import load_dotenv
from core.orquestador import GridOrquestador

# Cargar variables de entorno
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("UAO_Sclaping.Main")

def main():
    mode = os.getenv("MODE", os.getenv("EXECUTION_MODE", "DEMO"))
    logger.info(f"🚀 Iniciando UAO_Grid en modo: {mode}")

    try:
        orquestador = GridOrquestador(mode=mode, exchange=None)
        orquestador.run()
    except KeyboardInterrupt:
        logger.info("👋 Simulación detenida por el usuario.")
    except Exception as e:
        logger.error(f"❌ Error crítico en ejecución: {e}", exc_info=True)

if __name__ == "__main__":
    main()
