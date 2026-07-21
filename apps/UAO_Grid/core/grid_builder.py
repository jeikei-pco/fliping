import logging
from typing import List
from core.models import ValidatedOptimizationProfile, GridDefinition, GridLevel

logger = logging.getLogger("UAO_Sclaping.GridBuilder")

class GridBuilder:
    @staticmethod
    def build(profile: ValidatedOptimizationProfile, current_price: float) -> GridDefinition:
        symbol = profile.symbol
        optimization = profile.optimization

        grid_spacing_pct = float(optimization["grid_spacing_pct"])
        grid_lines = int(optimization["grid_lines"])
        capital = float(optimization["capital"])
        leverage = float(optimization["leverage"])
        min_profit_pct = float(optimization["min_profit_pct"])
        preferred_mode = str(optimization["preferred_mode"]).upper()
        rebalance_distance = float(optimization["rebalance_distance"])

        # [FASE 3b] Filtros de seguridad: rechazar variables destructivas de la IA
        MAX_LEVERAGE = 50.0
        MIN_LEVERAGE = 1.0
        MAX_SPACING_PCT = 0.4   # 20%
        MIN_SPACING_PCT = 0.0001 # 0.01%
        MIN_CAPITAL = 1.0
        MIN_GRID_LINES = 4

        if leverage > MAX_LEVERAGE:
            logger.warning(
                f"[GridBuilder] ⚠️ Apalancamiento {leverage}x excede el maximo ({MAX_LEVERAGE}x). Limitando."
            )
            leverage = MAX_LEVERAGE
        if leverage < MIN_LEVERAGE:
            logger.warning(f"[GridBuilder] ⚠️ Apalancamiento {leverage}x es menor al minimo. Ajustando a {MIN_LEVERAGE}x.")
            leverage = MIN_LEVERAGE

        if grid_spacing_pct > MAX_SPACING_PCT:
            logger.warning(
                f"[GridBuilder] ⚠️ Espaciado {grid_spacing_pct*100:.2f}% excede el maximo ({MAX_SPACING_PCT*100:.0f}%). Limitando."
            )
            grid_spacing_pct = MAX_SPACING_PCT
        if grid_spacing_pct < MIN_SPACING_PCT:
            logger.warning(
                f"[GridBuilder] ⚠️ Espaciado {grid_spacing_pct*100:.4f}% es demasiado pequeno. Ajustando a {MIN_SPACING_PCT*100:.2f}%."
            )
            grid_spacing_pct = MIN_SPACING_PCT

        if capital < MIN_CAPITAL:
            logger.warning(f"[GridBuilder] ⚠️ Capital ${capital:.2f} es demasiado bajo. Ajustando a ${MIN_CAPITAL:.2f}.")
            capital = MIN_CAPITAL

        if grid_lines < MIN_GRID_LINES:
            logger.warning(f"[GridBuilder] ⚠️ grid_lines={grid_lines} es demasiado bajo. Ajustando a {MIN_GRID_LINES}.")
            grid_lines = MIN_GRID_LINES

        if current_price <= 0:
            raise ValueError("current_price debe ser mayor a cero")
        if grid_spacing_pct <= 0:
            raise ValueError("grid_spacing_pct debe ser mayor a cero")
        if grid_lines <= 0:
            raise ValueError("grid_lines debe ser mayor a cero")


        # Calcular cantidad por nivel (asumiendo distribución equitativa del capital)
        # Esto es una simplificación, el cálculo real podría ser más complejo
        qty_per_level = (capital / grid_lines) * leverage / current_price

        buy_levels: List[GridLevel] = []
        sell_levels: List[GridLevel] = []
        all_grid_levels: List[GridLevel] = []

        if preferred_mode == "LONG":
            buy_count = max(1, int(round(grid_lines * 0.70)))
            sell_count = max(1, grid_lines - buy_count)
        elif preferred_mode == "SHORT":
            sell_count = max(1, int(round(grid_lines * 0.70)))
            buy_count = max(1, grid_lines - sell_count)
        else:
            buy_count = grid_lines // 2
            sell_count = grid_lines - buy_count

        # Construir niveles de venta (SELL) por encima del precio actual.
        for i in range(1, sell_count + 1):
            price = current_price * (1 + grid_spacing_pct * i)
            sell_levels.append(GridLevel(level=i, price=price, qty=qty_per_level, side="SELL"))

        # Construir niveles de compra (BUY) por debajo del precio actual.
        for i in range(1, buy_count + 1):
            price = current_price * (1 - grid_spacing_pct * i)
            buy_levels.append(GridLevel(level=-i, price=price, qty=qty_per_level, side="BUY"))
        
        all_grid_levels.extend(buy_levels)
        all_grid_levels.extend(sell_levels)
        all_grid_levels.sort(key=lambda x: x.price)

        logger.info(f"GridBuilder: Construido grid para {symbol} con {len(all_grid_levels)} niveles.")

        return GridDefinition(
            symbol=symbol,
            grid_levels=all_grid_levels,
            buy_levels=buy_levels,
            sell_levels=sell_levels,
            spacing=grid_spacing_pct,
            capital=capital,
            leverage=leverage,
            inventory=0.0, # Inventario inicial en 0
            mode=preferred_mode,
            rebalance_distance=rebalance_distance,
            profit_target=min_profit_pct,
        )
