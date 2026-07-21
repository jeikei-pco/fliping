"""
models.py — Tipos de dominio compartidos entre todos los módulos de UAO Grid V2.

REGLA: Solo definiciones de datos (dataclasses, enums).
Sin lógica de negocio. Sin imports de otros módulos internos.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


# ─────────────────────────────────────────────────────────────────────────────
# Enums
# ─────────────────────────────────────────────────────────────────────────────

class GridMode(str, Enum):
    """Modo estratégico del grid."""
    NEUTRAL = "NEUTRAL"   # Órdenes simétricas arriba y abajo
    LONG    = "LONG"      # Acumula longs abajo, toma ganancias arriba
    SHORT   = "SHORT"     # Acumula shorts arriba, toma ganancias abajo


class TrendDirection(str, Enum):
    """Dirección de la tendencia detectada por el analizador."""
    BULLISH   = "BULLISH"    # EMA rápida > EMA lenta
    BEARISH   = "BEARISH"    # EMA rápida < EMA lenta
    SIDEWAYS  = "SIDEWAYS"   # Sin tendencia clara


class OrderSide(str, Enum):
    """Lado de la orden."""
    BUY  = "BUY"
    SELL = "SELL"


class PositionSide(str, Enum):
    """Lado de la posición."""
    LONG  = "LONG"
    SHORT = "SHORT"


class OrderStatus(str, Enum):
    """Estado de la orden."""
    OPEN      = "OPEN"
    FILLED    = "FILLED"
    CANCELLED = "CANCELLED"
    PARTIAL   = "PARTIAL"


class GridEvent(str, Enum):
    """
    Eventos emitidos por el GridEngine.
    El orquestador reacciona a estos eventos sin acoplarse al engine.
    """
    GRID_MODIFIED        = "GRID_MODIFIED"         # La malla cambió → reconciliar órdenes
    KILL_SWITCH          = "KILL_SWITCH"           # Pérdida crítica → cerrar todo
    DRAIN_START          = "DRAIN_START"           # Iniciar modo drenaje
    DRAIN_COMPLETE       = "DRAIN_COMPLETE"        # Drenaje terminado
    SLIDE_UP             = "SLIDE_UP"              # Grid se deslizó hacia arriba
    SLIDE_DOWN           = "SLIDE_DOWN"            # Grid se deslizó hacia abajo
    WS_RECONECT_NEEDED   = "WS_RECONECT_NEEDED"    # WebSocket congelado


# ─────────────────────────────────────────────────────────────────────────────
# Salida del Analizador
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class MarketMetrics:
    """
    Métricas de mercado calculadas por el Analizador.

    Solo describe el estado del mercado. No contiene decisiones de trading.
    Es la entrada del Optimizador.
    """
    symbol:               str
    precio:               float     # Último precio observado

    # Volatilidad
    atr_pct:              float     # ATR como % del precio (ej. 0.003 = 0.3%)
    atr_abs:              float     # ATR en precio absoluto (ej. 0.45 USDT)
    rango_vela_mediano:   float     # Mediana de rango H-L en % (ej. 0.004)
    recorrido_real:       float     # Recorrido interno mediano (zig-zag de 1m)

    # Calidad de oscilación
    ops_promedio:         float     # Operaciones reales promedio por vela
    velas_utiles_pct:     float     # % de velas con rango > grid_step mínimo
    consistencia:         float     # Estabilidad de la volatilidad [0, 1]
    simetria:             float     # Balance alcista/bajista [0, 1]
    oscilacion:           float     # recorrido_real / range_pct (>1 = zig-zag real)
    zigzag_score:         float     # tanh(osc * sim) → [0, 1]
    amplitude_ratio:      float     # rango_vela / MIN_GRID, cap=5
    deriva_pct:           float     # % de drift total del precio en el período

    # Score compuesto
    score:                float     # Score global [0, ~100]

    # Parámetro óptimo para el grid
    grid_step_optimo:     float     # Espaciado sugerido en % (ej. 0.003)

    # Liquidez
    volumen_24h_usdt:     float     # Volumen de 24h en USDT

    # Tendencia (para sugerir modo)
    tendencia:            TrendDirection = TrendDirection.SIDEWAYS
    ema_fast:             float = 0.0   # EMA rápida (ej. EMA 21)
    ema_slow:             float = 0.0   # EMA lenta (ej. EMA 50)

    # Comisiones del mercado (para el backtester, sin que lo recalcule)
    fee_maker:            float = 0.0002
    fee_taker:            float = 0.0005


# ─────────────────────────────────────────────────────────────────────────────
# Salida del Optimizador
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class GridParameters:
    """
    Parámetros calculados por el Optimizador para una configuración de grid.

    Es la entrada del Backtester y del Engine.
    """
    symbol:             str
    valido:             bool

    # Si no es válido, explica por qué
    razon_invalido:     Optional[str] = None

    # Parámetros de la malla
    modo:               GridMode = GridMode.NEUTRAL
    precio_actual:      float = 0.0
    apalancamiento:     int = 10
    limite_superior:    float = 0.0
    limite_inferior:    float = 0.0
    num_grids:          int = 10
    espaciado_pct:      float = 0.003
    capital_por_linea:  float = 0.0
    tamaño_orden_usdt:  float = 0.0

    # Riesgo calculado (informativo)
    riesgo_liquidacion_pct: float = 0.0  # % estimado de distancia a liquidación


# ─────────────────────────────────────────────────────────────────────────────
# Salida del Backtester
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class BacktestResult:
    """
    Resultado completo de una simulación de grid.

    Contiene todas las métricas financieras estándar para evaluar la estrategia.
    La IA aprende de estos resultados históricos.
    """
    symbol:           str
    modo_optimo:      GridMode
    apalancamiento:   int
    num_grids:        int
    espaciado_pct:    float

    # Métricas financieras
    pnl_neto:         float    # PnL neto total en USDT
    operaciones:      int      # Número de trades cerrados
    win_rate:         float    # Porcentaje de trades ganadores [0, 1]
    profit_factor:    float    # gross_profit / gross_loss (>1 = rentable)
    max_drawdown:     float    # Máxima caída desde máximo (USDT, absoluto)
    max_drawdown_pct: float    # Máxima caída como % del capital
    sharpe_ratio:     float    # retorno / std_dev retornos (>1 = bueno)
    calmar_ratio:     float    # retorno_anual / max_drawdown_pct
    expectancy:       float    # USDT esperados por trade: wr*avg_win - (1-wr)*avg_loss
    recovery_factor:  float    # pnl_neto / max_drawdown

    # Score compuesto (para ranking)
    score_backtest:   float    # PnL*0.4 + Sharpe*0.3 + PF*0.2 + WR*0.1 (normalizado)

    # Parámetros usados (para que la IA aprenda)
    params_usados:    Dict[str, Any] = field(default_factory=dict)

    # Análisis original de mercado (para contexto)
    metrics_originales: Optional[Dict[str, Any]] = None


# ─────────────────────────────────────────────────────────────────────────────
# IA Optimizer
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TradingMetrics:
    """
    Métricas de rendimiento real del bot. Calculadas por la DB.
    Es la entrada del IA Optimizer. No la IA calcula estas métricas.
    """
    # Globales
    total_trades:       int     = 0
    win_rate_pct:       float   = 0.0
    profit_factor:      float   = 0.0
    net_pnl:            float   = 0.0
    avg_profit:         float   = 0.0
    avg_loss:           float   = 0.0
    long_win_rate:      float   = 0.0
    short_win_rate:     float   = 0.0
    max_consecutive_wins:   int = 0
    max_consecutive_losses: int = 0
    pnl_by_utc_hour:    Dict[str, float] = field(default_factory=dict)

    # Por símbolo (top 15 más activos)
    per_symbol:         Dict[str, Dict[str, Any]] = field(default_factory=dict)

    # Contexto de backtests recientes
    recent_backtests:   List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class IAOverrides:
    """
    Parámetros de ajuste sugeridos por la IA.
    Todos son multiplicadores o valores absolutos con límites seguros.
    """
    # Parámetros de malla
    grid_step_pct:        Optional[float] = None   # % exacto de espaciado (None = automático)
    grid_density_factor:  float = 1.0              # Multiplicador de cantidad de líneas
    leverage_factor:      float = 1.0              # Multiplicador de apalancamiento
    capital_factor:       float = 1.0              # Multiplicador de capital

    # Filtros duros
    max_leverage:         int   = 20               # Apalancamiento máximo absoluto
    min_score:            float = 30.0             # Score mínimo del analizador
    min_consistency:      float = 0.0              # Consistencia mínima [0, 1]
    min_oscillation:      float = 0.0              # Oscilación mínima

    # Metadatos
    timestamp:            str   = ""
    model_used:           str   = ""
    confidence:           float = 1.0              # Confianza de la IA en sus ajustes [0, 1]

    @classmethod
    def defaults(cls) -> "IAOverrides":
        """Retorna overrides neutros (sin cambios)."""
        return cls()


# ─────────────────────────────────────────────────────────────────────────────
# Exchange Domain Objects
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Order:
    """Orden de exchange. Representación de dominio independiente del exchange."""
    order_id:    str
    symbol:      str
    side:        OrderSide
    price:       float
    qty:         float
    status:      OrderStatus = OrderStatus.OPEN
    reduce_only: bool = False
    grid_level:  int  = 0

    @property
    def inst_id(self) -> str:
        """Convierte símbolo CCXT al formato instId de OKX."""
        return self.symbol.replace("/", "-").replace(":USDT", "-SWAP")

    def is_price_match(self, other: "Order", tolerance_pct: float = 0.0005) -> bool:
        """Compara precios con tolerancia de ±0.05% por defecto."""
        return abs(self.price - other.price) <= (self.price * tolerance_pct)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Order):
            return False
        return (
            self.symbol      == other.symbol
            and self.side    == other.side
            and self.is_price_match(other)
            and self.reduce_only == other.reduce_only
        )


@dataclass
class Position:
    """Posición abierta en el exchange."""
    symbol:      str
    side:        PositionSide
    qty:         float          # Cantidad en contratos (siempre positiva)
    entry_price: float

    @property
    def is_long(self) -> bool:
        return self.side == PositionSide.LONG

    @property
    def is_short(self) -> bool:
        return self.side == PositionSide.SHORT


# ─────────────────────────────────────────────────────────────────────────────
# Historial / Persistencia
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TradeRecord:
    """Registro de un trade ejecutado. Se persiste en DB."""
    symbol:       str
    side:         str           # "BUY" | "SELL"
    price:        float
    qty:          float
    pnl:          float         # PnL bruto del trade
    fee:          float         # Comisión pagada
    executed_at:  str           # ISO timestamp


@dataclass
class GridEngineEvent:
    """Evento emitido por el GridEngine para que el Orquestador reaccione."""
    event_type:   GridEvent
    symbol:       str
    data:         Dict[str, Any] = field(default_factory=dict)
    timestamp:    float = 0.0   # time.time()
