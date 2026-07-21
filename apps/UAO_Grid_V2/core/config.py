"""
config.py — Única fuente de verdad para toda la configuración del sistema.

REGLA: Ningún otro módulo llama os.getenv() directamente.
Todos reciben una instancia AppConfig inyectada.
"""
from __future__ import annotations

import os
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv

logger = logging.getLogger("UAO_Grid.Config")


# ─────────────────────────────────────────────────────────────────────────────
# Secciones de configuración (separadas por dominio)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ExchangeConfig:
    """Configuración del exchange y credenciales."""
    execution_mode: str        # "DEMO" | "REAL"
    okx_api_key: str
    okx_api_secret: str
    okx_passphrase: str
    is_demo: bool              # Derivado de execution_mode

    @property
    def mode(self) -> str:
        """Alias de execution_mode para compatibilidad."""
        return self.execution_mode


@dataclass(frozen=True)
class CapitalConfig:
    """Configuración de capital y gestión de riesgo."""
    capital_por_operacion: float   # USDT base por operación
    leverage: float                 # Apalancamiento base
    max_open_positions: int         # Máximo de posiciones simultáneas
    proximity_orders: int           # Órdenes activas cercanas al precio
    min_margin_per_line: float      # Margen mínimo por línea (USDT)
    kill_switch_pct: float          # % pérdida sobre capital para cerrar todo

    @property
    def capital_per_operation(self) -> float:
        """Alias en inglés de capital_por_operacion."""
        return self.capital_por_operacion


@dataclass(frozen=True)
class ScannerConfig:
    """Configuración del escáner de mercado."""
    timeframe: str              # "5m"
    limit: int                  # Velas a descargar
    top_n: int                  # Número de top símbolos a analizar
    min_volume_usdt: float      # Volumen 24h mínimo en USDT
    cycle_seconds: int          # Intervalo de re-escaneo
    initial_cycle_seconds: int  # Espera del primer ciclo
    workers: int                # Hilos para análisis paralelo
    symbols_to_trade: int       # Máximo de símbolos simultáneos activos
    core_symbols: List[str]     # Activos siempre activos (no dependen del ranking)
    max_symbols_to_analyze: int # Máximo de símbolos a analizar por ciclo
    backtest_top_n: int         # Top N del análisis que pasan al backtest
    analysis_workers: int       # Hilos para análisis paralelo (alias de workers)


@dataclass(frozen=True)
class GridConfig:
    """Parámetros del grid dinámico."""
    atr_period: int             # Período ATR
    atr_multiplier: float       # Multiplicador ATR para espaciado
    num_lineas_lado: int        # Líneas pre-calculadas a cada lado
    drain_timeout_hours: float  # Timeout del modo drenaje antes de cerrar


@dataclass(frozen=True)
class HisteresisConfig:
    """Configuración de histéresis anti-whipsaw."""
    slide_confirmations: int    # Velas consecutivas para confirmar deslizamiento
    slide_soft_threshold: float # Umbral suave (% del espaciado)
    slide_emergency_threshold: float  # Umbral emergencia (inmediato)


@dataclass(frozen=True)
class RotationConfig:
    """Configuración de rotación de símbolos."""
    timeout_hours: float        # Time-stop: max horas en modo drenaje
    force_market: bool          # Si time-stop expira, cerrar residuo a mercado


@dataclass(frozen=True)
class WatchdogConfig:
    """Configuración del watchdog de estado."""
    interval_seconds: int       # Verificación REST cada N segundos
    ws_timeout_seconds: int     # Segundos sin tick WS = congelado
    price_drift_pct: float      # % diferencia REST vs RAM para forzar refresco


@dataclass(frozen=True)
class IAConfig:
    """Configuración del optimizador IA."""
    enabled: bool
    interval_hours: float
    api_url: str                # URL del proxy local Claude (fallback)
    gemini_api_keys: List[str]
    openrouter_api_keys: List[str]
    openai_api_keys: List[str]
    groq_api_key: Optional[str]
    openai_api_key: Optional[str]  # Para compatibilidad directa


@dataclass(frozen=True)
class DatabaseConfig:
    """Configuración de la base de datos."""
    db_path: str
    state_flush_interval: int   # Volcado RAM → SQLite cada N segundos


@dataclass(frozen=True)
class WebhookConfig:
    """Configuración del webhook de notificaciones."""
    url: Optional[str]
    enabled: bool
    timeout_seconds: float      # Timeout de cada POST al webhook


# ─────────────────────────────────────────────────────────────────────────────
# Configuración principal (compone todas las secciones)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class AppConfig:
    """
    Configuración completa de la aplicación.

    Instancia única creada al inicio. Inmutable (frozen=True) para evitar
    mutaciones accidentales. Todos los módulos la reciben por inyección.
    """
    exchange: ExchangeConfig
    capital: CapitalConfig
    scanner: ScannerConfig
    grid: GridConfig
    histeresis: HisteresisConfig
    rotation: RotationConfig
    watchdog: WatchdogConfig
    ia: IAConfig
    database: DatabaseConfig
    webhook: WebhookConfig


# ─────────────────────────────────────────────────────────────────────────────
# Helpers de carga
# ─────────────────────────────────────────────────────────────────────────────

def _get_str(key: str, default: str = "") -> str:
    val = os.getenv(key, default)
    return (val or "").strip()


def _get_float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, str(default)) or default)
    except (ValueError, TypeError):
        logger.warning("Config: valor inválido para %s, usando default %s", key, default)
        return default


def _get_int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)) or default)
    except (ValueError, TypeError):
        logger.warning("Config: valor inválido para %s, usando default %s", key, default)
        return default


def _get_bool(key: str, default: bool = False) -> bool:
    val = os.getenv(key, str(default)).strip().lower()
    return val in {"true", "1", "yes", "on"}


def _get_list(keys: List[str]) -> List[str]:
    """Extrae los valores no vacíos de una lista de claves .env."""
    return [v for k in keys if (v := _get_str(k))]


def _load_okx_credentials(mode: str) -> tuple[str, str, str]:
    """
    Carga credenciales OKX según el modo (DEMO | REAL).
    Lanza ValueError si las credenciales están incompletas.
    """
    prefix = "DEMO" if mode in {"DEMO", "SANDBOX", "PAPER", "SIMULATED"} else "REAL"
    api_key = _get_str(f"OKX_API_KEY_{prefix}")
    api_secret = _get_str(f"OKX_API_SECRET_{prefix}")
    passphrase = _get_str(f"OKX_PASSPHRASE_{prefix}")

    if not all([api_key, api_secret, passphrase]):
        raise ValueError(
            f"❌ Credenciales OKX_{prefix} incompletas en .env. "
            f"Se requieren OKX_API_KEY_{prefix}, OKX_API_SECRET_{prefix}, OKX_PASSPHRASE_{prefix}."
        )
    return api_key, api_secret, passphrase


# ─────────────────────────────────────────────────────────────────────────────
# Función pública
# ─────────────────────────────────────────────────────────────────────────────

def load_config(env_path: Optional[str] = None) -> AppConfig:
    """
    Carga la configuración completa desde el archivo .env.

    Args:
        env_path: Ruta al archivo .env. Si es None, busca en el directorio
                  del archivo que llama y sus padres.

    Returns:
        AppConfig inmutable con toda la configuración cargada y validada.

    Raises:
        ValueError: Si las credenciales OKX están incompletas.
        FileNotFoundError: Si no se encuentra el archivo .env.
    """
    if env_path:
        load_dotenv(dotenv_path=env_path, override=False)
    else:
        # Buscar .env subiendo desde el directorio actual
        _env_candidate = Path(__file__).parent.parent / ".env"
        if _env_candidate.exists():
            load_dotenv(dotenv_path=_env_candidate, override=False)
        else:
            load_dotenv(override=False)

    execution_mode = _get_str("EXECUTION_MODE", "DEMO").upper()
    is_demo = execution_mode in {"DEMO", "SANDBOX", "PAPER", "SIMULATED"}

    api_key, api_secret, passphrase = _load_okx_credentials(execution_mode)

    config = AppConfig(
        exchange=ExchangeConfig(
            execution_mode=execution_mode,
            okx_api_key=api_key,
            okx_api_secret=api_secret,
            okx_passphrase=passphrase,
            is_demo=is_demo,
        ),
        capital=CapitalConfig(
            capital_por_operacion=_get_float("GRID_CAPITAL_POR_OPERACION", 50.0),
            leverage=_get_float("GRID_LEVERAGE", 10.0),
            max_open_positions=_get_int("GRID_MAX_OPEN_POSITIONS", 3),
            proximity_orders=_get_int("GRID_PROXIMITY_ORDERS", 10),
            min_margin_per_line=_get_float("GRID_MIN_MARGIN_PER_LINE", 5.0),
            kill_switch_pct=_get_float("GRID_KILL_SWITCH_PCT", 50.0),
        ),
        scanner=ScannerConfig(
            timeframe=_get_str("SCAN_TIMEFRAME", "5m"),
            limit=_get_int("SCAN_LIMIT", 200),
            top_n=_get_int("SCAN_TOP_N", 10),
            min_volume_usdt=_get_float("SCAN_MIN_VOLUME_USDT", 1_000_000.0),
            cycle_seconds=_get_int("SCAN_CYCLE_SECONDS", 1800),
            initial_cycle_seconds=_get_int("SCAN_INITIAL_CYCLE", 300),
            workers=_get_int("SCAN_WORKERS", 10),
            symbols_to_trade=_get_int("GRID_SYMBOLS_TO_TRADE", 3),
            core_symbols=[
                s.strip() for s in
                _get_str("GRID_CORE_SYMBOLS", "BTC/USDT:USDT,ETH/USDT:USDT").split(",")
                if s.strip()
            ],
            max_symbols_to_analyze=_get_int("SCAN_MAX_SYMBOLS", 80),
            backtest_top_n=_get_int("SCAN_BACKTEST_TOP_N", 15),
            analysis_workers=_get_int("SCAN_WORKERS", 10),
        ),
        grid=GridConfig(
            atr_period=_get_int("GRID_ATR_PERIOD", 14),
            atr_multiplier=_get_float("GRID_ATR_MULTIPLIER", 1.5),
            num_lineas_lado=_get_int("GRID_NUM_LINEAS_LADO", 5),
            drain_timeout_hours=_get_float("GRID_DRAIN_TIMEOUT_HOURS", 2.0),
        ),
        histeresis=HisteresisConfig(
            slide_confirmations=_get_int("GRID_SLIDE_CONFIRMATIONS", 2),
            slide_soft_threshold=_get_float("GRID_SLIDE_SOFT_THRESHOLD", 0.50),
            slide_emergency_threshold=_get_float("GRID_SLIDE_EMERGENCY_THRESHOLD", 3.0),
        ),
        rotation=RotationConfig(
            timeout_hours=_get_float("GRID_ROTATION_TIMEOUT_HOURS", 2.0),
            force_market=_get_bool("GRID_ROTATION_FORCE_MARKET", True),
        ),
        watchdog=WatchdogConfig(
            interval_seconds=_get_int("WATCHDOG_INTERVAL_SECONDS", 3600),
            ws_timeout_seconds=_get_int("WATCHDOG_WS_TIMEOUT_SECONDS", 60),
            price_drift_pct=_get_float("WATCHDOG_PRICE_DRIFT_PCT", 1.0),
        ),
        ia=IAConfig(
            enabled=_get_bool("AI_OPTIMIZER_ENABLED", False),
            interval_hours=_get_float("AI_OPTIMIZER_INTERVAL_HOURS", 1.0),
            api_url=_get_str("AI_OPTIMIZER_API_URL", "http://127.0.0.1:8082/v1/messages"),
            gemini_api_keys=_get_list(["GEMINI_API_KEY", "GEMINI_API_KEY2", "GEMINI_API_KEY3"]),
            openrouter_api_keys=_get_list(["OPENROUTER_API_KEY", "OPENROUTER_API_KEY2"]),
            openai_api_keys=_get_list(["OPENAI_API_KEY", "OPENAI_API_KEY2"]),
            groq_api_key=_get_str("GROQ_API_KEY") or None,
            openai_api_key=_get_str("OPENAI_API_KEY") or None,
        ),
        database=DatabaseConfig(
            db_path=_get_str("DB_PATH", "./data/uao_grid.db"),
            state_flush_interval=_get_int("STATE_FLUSH_INTERVAL_SECONDS", 5),
        ),
        webhook=WebhookConfig(
            url=_get_str("WEBHOOK_URL") or None,
            enabled=bool(_get_str("WEBHOOK_URL")),
            timeout_seconds=_get_float("WEBHOOK_TIMEOUT_SECONDS", 3.0),
        ),
    )

    logger.info(
        "✅ Config cargada: modo=%s | capital=%.0f USDT | leverage=%.0fx | max_pos=%d",
        config.exchange.execution_mode,
        config.capital.capital_por_operacion,
        config.capital.leverage,
        config.capital.max_open_positions,
    )
    return config
