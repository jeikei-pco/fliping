"""
main.py — Entry point de UAO Grid V2.

Carga la configuración una sola vez y lanza el orquestador.
Con reinicio automático ante crash del orquestador.
"""
from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────────────
# Logging (configurar ANTES de importar módulos internos)
# ─────────────────────────────────────────────────────────────────────────────

def _configurar_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("uao_grid.log", encoding="utf-8"),
        ],
    )
    # Silenciar libs ruidosas
    logging.getLogger("websocket").setLevel(logging.WARNING)
    logging.getLogger("ccxt").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


logger = logging.getLogger("UAO_Grid")

# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    _configurar_logging()

    # Importar después de configurar logging
    from core.config import load_config
    from core.orquestador import GridOrquestador

    # Buscar el .env en la misma carpeta que main.py
    env_path = Path(__file__).parent / ".env"
    env_path_str = str(env_path) if env_path.exists() else None

    config = load_config(env_path=env_path_str)

    logger.info(
        "🚀 UAO Grid V2 iniciando | modo=%s | capital=%.0f USDT | max_pos=%d",
        config.exchange.execution_mode,
        config.capital.capital_por_operacion,
        config.capital.max_open_positions,
    )

    # Reinicio automático ante crash
    max_reintentos = 5
    reintento = 0

    while reintento < max_reintentos:
        try:
            bot = GridOrquestador(config)
            bot.run()
            logger.info("🔴 Orquestador terminó normalmente — fin.")
            break

        except KeyboardInterrupt:
            logger.info("⏹️ Detenido por el usuario (Ctrl+C)")
            break

        except Exception as exc:
            reintento += 1
            espera = 30 * reintento  # Back-off: 30s, 60s, 90s...
            logger.critical(
                "💥 Crash del orquestador (#%d/%d): %s | Reiniciando en %ds...",
                reintento, max_reintentos, exc, espera,
                exc_info=True,
            )
            time.sleep(espera)

    if reintento >= max_reintentos:
        logger.critical("❌ Máximo de reinicios alcanzado (%d). Bot detenido.", max_reintentos)
        sys.exit(1)


if __name__ == "__main__":
    main()
