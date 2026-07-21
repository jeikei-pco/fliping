"""Adaptador de persistencia del estado de dominio."""
from typing import Any


class GridPersistence:
    def __init__(self, database):
        self.database = database

    def save(self, symbol: str, engine):
        self.database.save_grid_cycles(
            symbol=symbol,
            levels=engine.niveles,
            cycles=engine.cycle_manager.snapshot(),
            blocked_levels=engine.cycle_manager.blocked_levels,
            center_price=engine.centro_grid,
            modo_drenaje=engine.modo_drenaje,
        )

    def load(self, symbol: str):
        return self.database.load_grid_state(symbol)
