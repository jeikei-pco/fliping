"""Inventario lógico calculado exclusivamente desde fills."""
from dataclasses import dataclass

from core.models import TradeFill


@dataclass
class Inventory:
    net_qty: float = 0.0
    average_price: float = 0.0
    capital_used: float = 0.0
    fees: float = 0.0
    realized_pnl: float = 0.0

    def apply_fill(self, fill: TradeFill):
        signed = fill.qty if fill.side == "BUY" else -fill.qty
        old = self.net_qty
        self.fees += fill.fee
        self.net_qty += signed
        if old == 0 or old * self.net_qty > 0:
            self.average_price = ((abs(old) * self.average_price) + (fill.qty * fill.price)) / max(abs(self.net_qty), 1e-12)
        elif abs(self.net_qty) < 1e-12:
            self.average_price = 0.0
        self.capital_used = abs(self.net_qty * self.average_price)
