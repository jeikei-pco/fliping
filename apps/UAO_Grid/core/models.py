"""Objetos de dominio independientes del exchange."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def utc_now() -> float:
    return datetime.now(timezone.utc).timestamp()


@dataclass
class Order:
    order_id: str
    symbol: str
    side: str
    price: float
    qty: float
    status: str = "OPEN"
    reduce_only: bool = False
    grid_level: int = 0
    client_order_id: str = ""
    exchange_order_id: str = ""
    filled_qty: float = 0.0
    remaining_qty: float = 0.0
    created_at: float = field(default_factory=utc_now)
    updated_at: float = field(default_factory=utc_now)
    strategy_id: str = "grid"
    cycle_id: str = ""
    order_role: str = "BASE"

    def __post_init__(self):
        self.side = str(self.side).upper()
        self.price = float(self.price)
        self.qty = float(self.qty)
        self.filled_qty = float(self.filled_qty or 0.0)
        self.remaining_qty = float(self.remaining_qty if self.remaining_qty else max(self.qty - self.filled_qty, 0.0))
        self.grid_level = int(self.grid_level or 0)

    @property
    def inst_id(self) -> str:
        return self.symbol.replace("/", "-").replace(":USDT", "-SWAP")

    def __eq__(self, other):
        if not isinstance(other, Order):
            return False
        return (
            self.symbol == other.symbol
            and self.side == other.side
            and abs(self.price - other.price) <= self.price * 0.0005
            and self.reduce_only == other.reduce_only
        )


@dataclass
class Position:
    symbol: str
    side: str
    qty: float
    entry_price: float
    average_price: float = 0.0
    fees: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    mark_price: float = 0.0
    updated_at: float = field(default_factory=utc_now)

    def __post_init__(self):
        self.side = str(self.side).upper()
        self.qty = abs(float(self.qty))
        self.entry_price = float(self.entry_price or 0.0)
        self.average_price = float(self.average_price or self.entry_price)


@dataclass(frozen=True)
class TradeFill:
    fill_id: str
    order_id: str
    symbol: str
    side: str
    price: float
    qty: float
    fee: float = 0.0
    maker: bool = False
    timestamp: float = field(default_factory=utc_now)
    level_id: Optional[int] = None
    cycle_id: str = ""

    def __post_init__(self):
        object.__setattr__(self, "side", str(self.side).upper())
        object.__setattr__(self, "price", float(self.price))
        object.__setattr__(self, "qty", float(self.qty))
        object.__setattr__(self, "fee", float(self.fee or 0.0))


@dataclass(frozen=True)
class OptimizationProfile:
    symbol: str
    params: Dict[str, Any]
    analysis: Dict[str, Any] = field(default_factory=dict)
    source: str = "BACKTEST"

@dataclass(frozen=True)
class ValidatedOptimizationProfile:
    symbol: str
    analysis: Dict[str, Any] = field(default_factory=dict)
    optimization: Dict[str, Any] = field(default_factory=dict)
    backtest: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_legacy_dict(self) -> Dict[str, Any]:
        """Vista plana para compatibilidad con codigo anterior y cache JSON."""
        params = {
            "symbol": self.symbol,
            "source": self.metadata.get("source", "BACKTEST"),
            "ai_overrides": self.metadata.get("ai_overrides", {}),
            "recent_activity": self.metadata.get("recent_activity", {}),
            "pnl_neto": self.backtest.get("PnL", 0.0),
            "roi_pct": self.backtest.get("ROI", 0.0),
            "drawdown": self.backtest.get("Drawdown", 0.0),
            "win_rate": self.backtest.get("WinRate", 0.0),
            "profit_factor": self.backtest.get("ProfitFactor", 0.0),
            "operaciones": self.backtest.get("Trades", 0),
            "modo": self.optimization.get("preferred_mode", "NEUTRAL"),
            "modo_optimo": self.optimization.get("preferred_mode", "NEUTRAL"),
            "apalancamiento": self.optimization.get("leverage", 1),
            "apalancamiento_usado": self.optimization.get("leverage", 1),
            "num_grids": self.optimization.get("grid_lines", 0),
            "espaciado_pct": self.optimization.get("grid_spacing_pct", 0.0),
            "capital": self.optimization.get("capital", 0.0),
            "capital_factor": self.optimization.get("capital_factor", 1.0),
            "capital_por_linea": (
                float(self.optimization.get("capital", 0.0)) / max(int(self.optimization.get("grid_lines", 1) or 1), 1)
            ),
            "min_profit_pct": self.optimization.get("min_profit_pct", 0.0),
            "rebalance_distance": self.optimization.get("rebalance_distance", 0.0),
            "params_optimos": {
                "modo": self.optimization.get("preferred_mode", "NEUTRAL"),
                "apalancamiento": self.optimization.get("leverage", 1),
                "num_grids": self.optimization.get("grid_lines", 0),
                "espaciado_pct": self.optimization.get("grid_spacing_pct", 0.0),
                "min_profit_pct": self.optimization.get("min_profit_pct", 0.0),
                "rebalance_distance": self.optimization.get("rebalance_distance", 0.0),
            },
            "analisis_original": self.analysis,
            "analysis": self.analysis,
            "optimization": self.optimization,
            "backtest": self.backtest,
            "metadata": self.metadata,
        }
        return params

    def __getitem__(self, key: str) -> Any:
        return self.to_legacy_dict()[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.to_legacy_dict().get(key, default)

    def __contains__(self, key: str) -> bool:
        return key in self.to_legacy_dict()

    def keys(self):
        return self.to_legacy_dict().keys()

    def items(self):
        return self.to_legacy_dict().items()

@dataclass(frozen=True)
class GridLevel:
    level: int
    price: float
    qty: float
    side: str
    status: str = "OPEN"
    cycle: Optional[str] = None

@dataclass(frozen=True)
class GridDefinition:
    symbol: str
    grid_levels: List[GridLevel]
    buy_levels: List[GridLevel]
    sell_levels: List[GridLevel]
    spacing: float
    capital: float
    leverage: float
    inventory: float
    mode: str
    rebalance_distance: float
    profit_target: float
