"""Calculo puro de TP/rebalanceo a partir de un fill real."""
from dataclasses import dataclass

from core.models import TradeFill


@dataclass(frozen=True)
class TakeProfitOrder:
    side: str
    price: float
    qty: float
    base_level: int
    cycle_id: str
    order_role: str = "TP"


def calcular_distancia_rentable(maker: float = 0.0002, taker: float = 0.0005, profit_min: float = 0.0005) -> float:
    return max(float(maker) + float(taker) + float(profit_min), 0.0)


def crear_tp_contrario(fill: TradeFill, cycle_id: str, base_level: int,
                       spacing: float, maker: float = 0.0002,
                       taker: float = 0.0005, profit_min: float = 0.0005) -> TakeProfitOrder:
    distance = max(float(spacing), calcular_distancia_rentable(maker, taker, profit_min))
    side = "SELL" if fill.side == "BUY" else "BUY"
    price = fill.price * (1 + distance) if fill.side == "BUY" else fill.price * (1 - distance)
    return TakeProfitOrder(side, price, fill.qty, base_level * 100, cycle_id)
