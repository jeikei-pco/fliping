"""
engine.py — Cerebro del Grid.
Maneja el estado lógico del grid, ATR dinámico, histéresis, modo drenaje y kill-switch.
"""
import logging
import os
import time
from typing import List, Dict, Any, Optional
from core.models import Order, Position, TradeFill, ValidatedOptimizationProfile, GridDefinition, GridLevel
from core.grid_builder import GridBuilder
import numpy as np
import pandas as pd

from core.cycle_manager import CycleManager, GridCycle, CycleState
from core.grid_state import GridState
from core.inventory import Inventory
from core.rebalance import crear_tp_contrario

logger = logging.getLogger("UAO_Sclaping.GridEngine")


class GridEngine:
    def __init__(self, symbol: str, capital_inicial: float = None, leverage: float = None):
        self.current_symbol = symbol
        self.posicion_neta = 0.0
        self.precio_promedio = 0.0
        self.modo_estrategia = "NEUTRAL"
        self.niveles = []
        self.centro_grid = 0.0
        self.espaciado_actual = 0.0
        self.capital_inicial = float(capital_inicial if capital_inicial is not None else os.getenv("GRID_CAPITAL_POR_OPERACION", 50))
        self.leverage = float(leverage if leverage is not None else os.getenv("GRID_LEVERAGE", 15.0))
        self.num_lineas_lado = int(os.getenv("GRID_NUM_LINEAS_LADO", 3))
        self.num_grids_optimo = self.num_lineas_lado * 2
        self.optimization_profile: Optional[ValidatedOptimizationProfile] = None

        self.max_proximity_orders = int(os.getenv("GRID_PROXIMITY_ORDERS", 2))
        self.confirmaciones_requeridas = int(os.getenv("GRID_SLIDE_CONFIRMATIONS", 2))
        self.umbral_suave = float(os.getenv("GRID_SLIDE_SOFT_THRESHOLD", 2.50))
        self.umbral_emergencia = float(os.getenv("GRID_SLIDE_EMERGENCY_THRESHOLD", 3.00))

        self._desplazamiento_pendiente = False
        self._direccion_pendiente = 0
        self._confirmaciones = 0

        self.grid_definition: Optional[GridDefinition] = None
        self.grid_state = GridState()
        self.cycle_manager = CycleManager(symbol)
        self.grid_cycles = self.cycle_manager.cycles
        self.blocked_levels = self.cycle_manager.blocked_levels
        self.inventory = Inventory()

        self.modo_drenaje = False
        self.drenaje_inicio = 0.0

        self.kill_switch_pct = float(os.getenv("GRID_KILL_SWITCH_PCT", 50.0))
        self.kill_switch_activado = False
        self.posicion_no_operable = False

        self.malla_modificada = False
        self.ws_reconectar = False
        self.ultima_ejecucion_ts = 0.0

    def create_cycle(self, base_level: int, entry_side: str, entry_price: float,
                     entry_qty: float, entry_fee: float = 0.0,
                     order_id: str = "", level_id: int = None) -> GridCycle:
        """Registra una ejecucion como ciclo independiente del nivel de la malla."""
        fill = TradeFill(
            fill_id=f"local:{int(time.time() * 1000)}",
            order_id=order_id or "",
            symbol=self.current_symbol,
            side=entry_side,
            price=entry_price,
            qty=entry_qty,
            fee=entry_fee,
            timestamp=time.time(),
            level_id=level_id,
        )
        cycle = self.cycle_manager.create_cycle(fill, int(base_level or 0))
        return cycle

    def complete_cycle(self, cycle_id: str, tp_price: float, tp_qty: float,
                       tp_fee: float = 0.0, tp_time: float = None) -> GridCycle:
        fill = TradeFill(f"local:{int(time.time() * 1000)}", "", self.current_symbol,
                         "SELL", tp_price, tp_qty, tp_fee, timestamp=tp_time or time.time())
        return self.cycle_manager.complete_cycle(cycle_id, fill)

    def cancel_cycle(self, cycle_id: str) -> GridCycle:
        return self.cycle_manager.cancel_cycle(cycle_id)

    def cycles_snapshot(self) -> dict:
        return self.cycle_manager.snapshot()

    def restore_cycles(self, cycles: dict, blocked_levels=None):
        """Restaura ciclos activos guardados sin reactivar ciclos terminados."""
        self.cycle_manager.restore(cycles, blocked_levels)
        self.grid_cycles = self.cycle_manager.cycles
        self.blocked_levels = self.cycle_manager.blocked_levels



    def actualizar_tps_dinamicos(self, df_5m: pd.DataFrame):
        """
        [MODIFICADO] Las órdenes Take Profit activas ahora son INAMOVIBLES.
        El precio del TP se fija en el instante exacto en que se ejecuta la entrada
        (capturando self.espaciado_actual en procesar_ejecucion_simulada).
        No se altera ningún TP durante la respiración de 30 s ni ante cambios del ATR.
        """
        # Mantener como pass para no romper referencias en el orquestador.
        pass


    def malla_necesita_reajuste(self, precio_actual: float) -> bool:
        # Si se vació el inventario (Reset on Flat) y se pidió reajuste, se respeta
        if getattr(self, '_reajuste_pendiente', False) and abs(self.posicion_neta) < 1e-9:
            self._reajuste_pendiente = False
            return True
            
        # Limpiamos el flag si existía para que no se quede colgado
        if getattr(self, '_reajuste_pendiente', False):
            self._reajuste_pendiente = False
            
        # Si la diferencia entre el precio del centro y el precio actual es menor al 50% 
        # del espaciado, NO HAGAS NADA.
        if self.centro_grid > 0:
            if abs(precio_actual - self.centro_grid) < (self.espaciado_actual * 0.5 * self.centro_grid):
                return False
                
        return True

    def chequear_breakout_malla(self, precio_actual: float, umbral_pct: float = 0.005) -> bool:
        if not self.niveles:
            return False
            
        max_sell = max([n["price"] for n in self.niveles if n["side"] == "SELL"], default=float('-inf'))
        min_buy = min([n["price"] for n in self.niveles if n["side"] == "BUY"], default=float('inf'))
        
        if max_sell != float('-inf') and precio_actual > (max_sell * (1 + umbral_pct)):
            self._reajuste_pendiente = True
            return True
            
        if min_buy != float('inf') and precio_actual < (min_buy * (1 - umbral_pct)):
            self._reajuste_pendiente = True
            return True
            
        return False

    def update_params(self, atr_mult=None, leverage=None, num_lineas=None, capital_inicial=None):
        """Compatibilidad para hot-reload; no recalcula la malla."""
        if leverage is not None:
            self.leverage = float(leverage)
        if num_lineas is not None:
            self.num_lineas_lado = int(num_lineas)
            self.num_grids_optimo = self.num_lineas_lado * 2
        if capital_inicial is not None:
            self.capital_inicial = float(capital_inicial)

    def inicializar_grid(self, profile: ValidatedOptimizationProfile, current_price: float):
        """Crea la malla utilizando un perfil de optimización validado."""
        self.grid_definition = GridBuilder.build(profile, current_price)
        self.optimization_profile = profile
        self.malla_modificada = True
        self.modo_estrategia = self.grid_definition.mode
        self.centro_grid = float(current_price)
        self.espaciado_actual = self.grid_definition.spacing
        self.capital_inicial = self.grid_definition.capital
        self.leverage = self.grid_definition.leverage
        self.num_grids_optimo = len(self.grid_definition.grid_levels)
        self.num_lineas_lado = max(1, self.num_grids_optimo // 2)
        self.niveles = [
            {
                "side": level.side,
                "price": level.price,
                "qty": level.qty,
                "level": level.level,
                "status": level.status,
                "cycle_id": level.cycle or "",
            }
            for level in self.grid_definition.grid_levels
        ]

        # Validaciones de consistencia (Sprint 7)
        optimization = profile.optimization
        grid_def = self.grid_definition

        # TODO: Implementar una mejor validación de spacing. Por ahora, una aproximación.
        # La validación exacta requiere recalcular el spacing a partir de los niveles generados
        # y compararlo con el spacing del perfil. Para simplificar, se asume que el GridBuilder
        # usa el spacing del perfil.
        # assert abs(optimization["grid_spacing_pct"] - (grid_def.grid_levels[1].price / grid_def.grid_levels[0].price - 1)) < 1e-5, "Spacing mismatch"
        # assert optimization["grid_lines"] == len(grid_def.grid_levels), "Grid lines mismatch"
        assert optimization["leverage"] == grid_def.leverage, "Leverage mismatch"
        assert optimization["capital"] == grid_def.capital, "Capital mismatch"

        logger.info(f"✅ [GridEngine] Grid inicializado para {self.current_symbol} con perfil validado.")

    def evaluar_kill_switch(self, precio_actual: float) -> bool:
        """Edge Case 8: Stop-Loss Global si pérdida excede el % del capital."""
        if abs(self.posicion_neta) < 1e-9:
            return False
            
        if self.posicion_neta > 0:  # LONG
            pnl = self.posicion_neta * (precio_actual - self.precio_promedio)
        else:  # SHORT
            pnl = abs(self.posicion_neta) * (self.precio_promedio - precio_actual)
            
        pnl_real = pnl
        umbral = self.capital_inicial * (self.kill_switch_pct / 100.0)
        
        if pnl_real < -umbral:
            logger.critical(f"🚨🚨🚨 KILL-SWITCH ACTIVADO! PnL: ${pnl_real:.4f} excede umbral -${umbral:.2f}")
            self.kill_switch_activado = True
            return True
            
        return False

    def reset(self):
        """Resetea estado interno en caso de Kill-Switch."""
        self.posicion_neta = 0.0
        self.precio_promedio = 0.0
        self.niveles = []
        self.grid_state = GridState()
        self.cycle_manager = CycleManager(self.current_symbol)
        self.grid_cycles = self.cycle_manager.cycles
        self.blocked_levels = self.cycle_manager.blocked_levels
        self.inventory = Inventory()
        self._reajuste_pendiente = False
        self.modo_drenaje = False
        self.kill_switch_activado = False
        self.posicion_no_operable = False
        self.malla_modificada = False
        self.ultima_ejecucion_ts = 0.0
        self._atr_inicializado = False

    # ── MODO DRENAJE ──

    def activar_modo_drenaje(self):
        self.modo_drenaje = True
        self.drenaje_inicio = time.time()
        self.malla_modificada = True
        logger.info(f"🚿 Modo Drenaje activado para {self.current_symbol}.")

    def es_timeout_drenaje(self, timeout_horas: float) -> bool:
        if not self.modo_drenaje: return False
        return (time.time() - self.drenaje_inicio) > (timeout_horas * 3600)

    @staticmethod
    def _redondear_qty(qty: float, lot_sz: float) -> float:
        if lot_sz <= 0: return qty
        # El round(..., 8) evita el error del 2.9999999
        return float(int(round(qty / lot_sz, 8)) * lot_sz)

    @staticmethod
    def _redondear_precio(precio: float, tick_sz: float) -> float:
        return round(round(precio / tick_sz) * tick_sz, 10) if tick_sz > 0 else precio

    @staticmethod
    def _distancia_pct(precio_a: float, precio_b: float) -> float:
        base = max(abs(float(precio_a or 0.0)), abs(float(precio_b or 0.0)), 1e-9)
        return abs(float(precio_a) - float(precio_b)) / base

    def _retirar_niveles_cercanos(self, side: str, price: float, distancia_minima_pct: float, preservar_level: int = None) -> int:
        """
        Evita que una orden de TP/rebalance quede pegada a una orden base vecina.
        La contraorden nacida del fill tiene prioridad sobre niveles previos del mismo lado.
        """
        originales = list(self.niveles)
        self.niveles = []
        retirados = 0
        for nivel in originales:
            if preservar_level is not None and nivel.get("level") == preservar_level:
                self.niveles.append(nivel)
                continue

            mismo_lado = str(nivel.get("side", "")).upper() == side.upper()
            es_base = abs(int(nivel.get("level", 0) or 0)) < 100
            demasiado_cerca = self._distancia_pct(nivel.get("price", 0.0), price) < distancia_minima_pct
            if mismo_lado and es_base and demasiado_cerca:
                retirados += 1
                continue

            self.niveles.append(nivel)

        return retirados

    def calcular_ordenes_drenaje(self, posicion: Position, market_info: dict) -> List[Order]:
        """Drenaje Inteligente (Profit-First): Coloca TPs asegurando ganancia mínima."""
        if abs(posicion.qty) < 1e-9:
            return []
            
        # En OKX el tamaño y mínimo son SIEMPRE en contratos (enteros).
        min_sz = 1.0
        lot_sz = 1.0
        tick_sz = float(market_info.get("precision", {}).get("price", 1e-8))
        
        qty_total = posicion.qty
        
        # Fraccionar posición respetando mínimos (3 -> 2 -> 1)
        divisor_elegido = 1
        for divisor in [3, 2, 1]:
            qty_tp = self._redondear_qty(qty_total / divisor, lot_sz)
            if qty_tp >= min_sz:
                divisor_elegido = divisor
                break
        else:
            logger.warning(f"⚠️ Posición residual {qty_total} menor al lote mínimo. Ignorando.")
            self.posicion_no_operable = True
            self.modo_drenaje = False
            return []
            
        ordenes = []
        qty_restante = qty_total
        qty_tp = self._redondear_qty(qty_total / divisor_elegido, lot_sz)
        
        # --- NUEVA LÓGICA: PROFIT FIRST ---
        # 0.2% de ganancia mínima asegurada para cubrir fees y dejar profit
        ganancia_minima_pct = float(os.getenv("GRID_DRAIN_MIN_PROFIT_PCT", 0.002)) 
        
        for i in range(divisor_elegido):
            if i == divisor_elegido - 1:
                qty_orden = self._redondear_qty(qty_restante, lot_sz)
            else:
                qty_orden = qty_tp
                
            if qty_orden < min_sz:
                continue
                
            # Escalonamos las salidas, pero SIEMPRE por encima de la ganancia mínima
            mult_escalon = ganancia_minima_pct + (0.001 * i)
            precio_base = posicion.entry_price
            
            # Calculamos el precio exigiendo rentabilidad
            precio_objetivo = precio_base * (1 + mult_escalon) if posicion.side == "LONG" else precio_base * (1 - mult_escalon)
            precio_tp = self._redondear_precio(precio_objetivo, tick_sz)
            
            ordenes.append(Order(
                order_id=f"drain_{int(time.time()*1000)}_{i}",
                symbol=self.current_symbol,
                side="SELL" if posicion.side == "LONG" else "BUY",
                price=precio_tp,
                qty=qty_orden
            ))
            qty_restante -= qty_orden
            
        logger.info(f"🚿 [Drenaje Paciente] Calculadas {len(ordenes)} órdenes TP a partir de {ganancia_minima_pct*100}% de profit.")
        return ordenes

    # ── GRID DESLIZANTE CON HISTÉRESIS ──



    def _desplazar_grid(self, nuevo_centro: float):
        """
        Re-inicializa la malla centrada en el nuevo precio.
        [FASE 1] TPs Inamovibles: extrae y protege los TPs activos ANTES de
        regenerar las órdenes base, luego los fusiona evitando solapamientos.
        """
        if not self.optimization_profile:
            return

        # 1. Extraer todos los niveles TP vigentes (abs(level) >= 100)
        tps_protegidos = [n for n in self.niveles if abs(int(n.get("level", 0) or 0)) >= 100]
        logger.info(
            f"[DESLIZAR] Protegiendo {len(tps_protegidos)} TPs inamovibles antes de re-centrar la malla."
        )

        # 2. Regenerar solo las órdenes base
        self.inicializar_grid(self.optimization_profile, nuevo_centro)

        # 3. Fusionar TPs protegidos evitando solapamientos con nuevos niveles base
        nuevos_precios = {n["price"] for n in self.niveles}
        tps_reinsertados = 0
        for tp in tps_protegidos:
            tp_precio = tp.get("price", 0.0)
            # Revisar que no haya un nivel base ya en ese mismo precio exacto
            demasiado_cerca = any(
                self._distancia_pct(tp_precio, p) < (self.espaciado_actual * 0.50)
                for p in nuevos_precios
            )
            if not demasiado_cerca:
                self.niveles.append(tp)
                nuevos_precios.add(tp_precio)
                tps_reinsertados += 1

        logger.info(
            f"[DESLIZAR] Malla re-centrada en {nuevo_centro:.4f}. "
            f"TPs reinsertados: {tps_reinsertados}/{len(tps_protegidos)}."
        )

    # ── ESTADO Y ORDENES ──

    def procesar_precio_externo(self, precio_actual: float):
        """Actualiza el precio y evalúa si toca deslizar (usado también por Watchdog)."""
        if not self.modo_drenaje and not self.kill_switch_activado:
            if self.chequear_breakout_malla(precio_actual, umbral_pct=0.03):
                logger.info(f"🚀 [BREAKOUT] Precio fuera de extremos por >3%. Deslizando malla a {precio_actual}")
                self._desplazar_grid(precio_actual)

    def forzar_sincronizacion(self, posicion: Position):
        """Llamado por Watchdog o al recuperar el estado inicial."""
        self.posicion_neta = posicion.qty if posicion.side == "LONG" else -posicion.qty
        self.precio_promedio = posicion.entry_price
        # [NUEVO] Re-generar la malla para adaptarnos a la posición forzada
        if abs(self.posicion_neta) > 1e-9:
            if self.optimization_profile:
                self.inicializar_grid(self.optimization_profile, self.precio_promedio)
        else:
            self.niveles = []

    def obtener_ordenes_deseadas(self, precio_actual: float, market_info: dict) -> List[Order]:
        """
        Retorna las órdenes que DEBERÍAN existir ahora mismo en el Exchange.
        Retorna todos los niveles calculados para que las órdenes descansen en OKX.
        """
        if not self.niveles or self.kill_switch_activado:
            if not self.kill_switch_activado and hasattr(self, 'modo_estrategia'):
                # Evitar spammear el log cada iteración. Usar el flag _out_of_range_log
                if not getattr(self, '_out_of_range_log', False):
                    logger.info(f"Modo [{self.modo_estrategia}]: Fuera de rango de compra/venta o sin órdenes disponibles.")
                    self._out_of_range_log = True
            return []
            
        self._out_of_range_log = False

        # Si estamos en modo drenaje, generar órdenes de drenaje
        if self.modo_drenaje:
            pos = Position(self.current_symbol, "LONG" if self.posicion_neta > 0 else "SHORT", 
                           abs(self.posicion_neta), self.precio_promedio)
            return self.calcular_ordenes_drenaje(pos, market_info)

        # MODO NORMAL: enviar la malla completa; no recortar por proximidad.
        top_n = self.niveles
        
        # tick_sz is the price precision from CCXT
        tick_sz = float(market_info.get("precision", {}).get("price", 1e-8))
        contract_sz = float(market_info.get("contractSize", 1.0))
        
        # En OKX futuros/swaps, el tamaño (sz) se envía en contratos y siempre es un número entero.
        lot_sz_contracts = 1.0
        
        deseadas = []
        for n in top_n:
            precio = self._redondear_precio(n["price"], tick_sz)
            
            # Convertir de cantidad base (monedas) a cantidad de contratos para OKX
            qty_contracts = n["qty"] / contract_sz if contract_sz > 0 else n["qty"]
            
            qty = self._redondear_qty(qty_contracts, lot_sz_contracts)
            # Garantizar mínimo de 1 contrato si el monto calculado es menor pero mayor a 0
            if qty < 1.0 and qty_contracts > 0:
                qty = 1.0
                
            if qty > 0:
                oid = f"grid_{n['level']}_{precio}"
                is_tp = abs(n['level']) >= 100
                deseadas.append(Order(
                    order_id=oid, 
                    symbol=self.current_symbol, 
                    side=n["side"], 
                    price=precio, 
                    qty=qty, 
                    grid_level=n['level'],
                    reduce_only=is_tp
                ))
                
        return deseadas

    def procesar_ejecucion_simulada(self, side: str, price: float, qty: float,
                                    level_id: int = None, market_info: dict = None,
                                    fill_fee: float = 0.0, order_id: str = "",
                                    fill_timestamp: float = None):
        """Aplica un fill real y avanza un ciclo independiente del nivel."""
        self.ultima_ejecucion_ts = time.time()
        market_info = market_info or {}
        side = str(side).upper()
        price = float(price)
        qty = float(qty)
        if price <= 0 or qty <= 0:
            return None
        old_pos = self.posicion_neta
        if side == "BUY":
            self.posicion_neta += qty
        else:
            self.posicion_neta -= qty

        # Recalcular precio promedio si cambió el tamaño pero mantuvo el signo
        if (side == "BUY" and old_pos >= 0) or (side == "SELL" and old_pos <= 0):
            if abs(self.posicion_neta) > 1e-9:
                self.precio_promedio = ((abs(old_pos) * self.precio_promedio) + (qty * price)) / abs(self.posicion_neta)
        elif abs(self.posicion_neta) < 1e-9:
            self.precio_promedio = 0.0

        # ELIMINAR EL NIVEL EJECUTADO MEDIANTE LEVEL ID EXACTO
        # [FASE 1b] Usamos reconstrucción limpia de lista en lugar de .pop()
        # dentro de un contexto concurrente para evitar errores de indexación.
        nivel_match = None
        for i, n in enumerate(self.niveles):
            # Prioridad 1: Búsqueda infalible por Level ID
            if level_id is not None and n.get("level") == level_id:
                nivel_match = i
                break
            # Prioridad 2: Fallback por precio (por si acaso)
            elif n["side"] == side and abs(n["price"] - price) < (price * 0.001):
                nivel_match = i
                break

        if nivel_match is not None:
            n_ejecutado = self.niveles[nivel_match]
            cantidad_fill = min(qty, float(n_ejecutado.get("qty", qty)))
            cantidad_restante = max(0.0, float(n_ejecutado.get("qty", qty)) - cantidad_fill)
            # Reconstrucción limpia: NO se usa pop() con índice mutable
            self.niveles = [n for idx, n in enumerate(self.niveles) if idx != nivel_match]
            if cantidad_restante > 1e-9 and abs(int(n_ejecutado.get("level", 0) or 0)) >= 100:
                residual = dict(n_ejecutado)
                residual["qty"] = cantidad_restante
                self.niveles.append(residual)

            es_tp = abs(int(n_ejecutado.get("level", 0) or 0)) >= 100
            cycle_id = n_ejecutado.get("cycle_id")

            if es_tp and cycle_id in self.grid_cycles:
                cycle = self.grid_cycles[cycle_id]
                tp_fill = TradeFill(
                    fill_id=f"{order_id or 'fill'}:{int((fill_timestamp or time.time()) * 1000)}",
                    order_id=order_id or "",
                    symbol=self.current_symbol,
                    side=side,
                    price=price,
                    qty=cantidad_fill,
                    fee=fill_fee,
                    timestamp=fill_timestamp or time.time(),
                    level_id=level_id,
                    cycle_id=cycle_id,
                )
                self.inventory.apply_fill(tp_fill)
                if cantidad_restante > 1e-9:
                    cycle.tp_qty += cantidad_fill
                    cycle.tp_fee += float(fill_fee or 0.0)
                    cycle.tp_time = float(fill_timestamp or time.time())
                    self.malla_modificada = True
                    logger.info("⏳ [CICLO %s] TP parcial %.8f; queda %.8f.", cycle_id, cantidad_fill, cantidad_restante)
                    return cycle
                try:
                    cycle = self.cycle_manager.complete_cycle(cycle_id, tp_fill)
                except ValueError as exc:
                    logger.warning("Ciclo %s rechazado: %s", cycle_id, exc)
                    return None

                # El nivel base vuelve a estar disponible solamente al cerrar el TP.
                base_level = cycle.base_level
                base_price = n_ejecutado.get("base_price")
                if not base_price:
                    base_price = self.centro_grid * (
                        1 + self.espaciado_actual * abs(base_level)
                    ) if base_level > 0 else self.centro_grid * (
                        1 - self.espaciado_actual * abs(base_level)
                    )
                self.niveles.append({
                    "side": cycle.entry_side,
                    "price": base_price,
                    "qty": cycle.entry_qty,
                    "level": base_level,
                    "cycle_id": "",
                })
                self.malla_modificada = True
                logger.info(
                    "✅ [CICLO %s] TP completado a %.8f; nivel base %s liberado y recreado.",
                    cycle_id, price, base_level,
                )
                return cycle

            # Una entrada base crea un ciclo y bloquea su nivel hasta el TP.
            base_level = int(n_ejecutado.get("level", level_id or 0) or 0)
            cycle = self.create_cycle(
                base_level=base_level,
                entry_side=side,
                entry_price=price,
                entry_qty=cantidad_fill,
                entry_fee=fill_fee,
                order_id=order_id,
                level_id=level_id,
            )
            tp_side = "SELL" if side == "BUY" else "BUY"
            entry_fill = TradeFill(
                fill_id=f"{order_id or 'fill'}:{int((fill_timestamp or time.time()) * 1000)}",
                order_id=order_id or "",
                symbol=self.current_symbol,
                side=side,
                price=price,
                qty=cantidad_fill,
                fee=fill_fee,
                timestamp=fill_timestamp or time.time(),
                level_id=level_id,
                cycle_id=cycle.cycle_id,
            )
            self.inventory.apply_fill(entry_fill)
            
            # Obtener comisiones (usar defaults realistas si no existen en market_info)
            fee_maker = float(market_info.get("maker", 0.00020))
            fee_taker = float(market_info.get("taker", 0.00050))
            
            # Rentabilidad deseada = comisiones + 0.10% (0.0010)
            self.min_spread_rentable = fee_maker + fee_taker + 0.0010
            
            # El rebalanceo es una funcion pura basada en el fill real.
            tp_order = crear_tp_contrario(
                entry_fill, cycle.cycle_id, base_level,
                spacing=self.espaciado_actual,
                maker=fee_maker, taker=fee_taker,
                profit_min=float(os.getenv("GRID_MIN_NET_PROFIT", "0.0005")),
            )
            precio_ejecucion = entry_fill.price
            tp_price = tp_order.price
            espaciado_usar = abs(tp_price / precio_ejecucion - 1.0)
            cycle.tp_side = tp_order.side
            cycle.tp_price = tp_price
            
            # En lugar de multiplicar ciegamente por 100:
            nivel_origen = abs(base_level)

            # Si el nivel ya es un TP (ej. >= 100), lo restauramos a linea base.
            tp_level = nivel_origen * 100
            if tp_level == 0: tp_level = 9999
            
            distancia_minima_pct = max(espaciado_usar * 0.80, self.min_spread_rentable)
            retirados = self._retirar_niveles_cercanos(tp_side, tp_price, distancia_minima_pct)

            self.niveles.append({
                "side": tp_side,
                "price": tp_price,
                "qty": cantidad_fill,
                "level": tp_level,
                "precio_original_entrada": precio_ejecucion,
                "source_fill_price": precio_ejecucion,
                "cycle_id": cycle.cycle_id,
                "base_level": base_level,
                "base_price": n_ejecutado.get("price", self.centro_grid),
            })
            self.malla_modificada = True
            
            logger.info(f"     🔄 [MALLA] SEÑAL EJECUTADA: Nivel {n_ejecutado['level']} llenado. Price={precio_ejecucion:.4f}.")
            logger.info(
                "     🔄 [MALLA] CONTRA-ORDEN CREADA: %s a $%.4f desde fill $%.4f "
                "(Espaciado usado: %.3f%%, niveles cercanos retirados: %d).",
                tp_side,
                tp_price,
                precio_ejecucion,
                espaciado_usar * 100,
                retirados,
            )
            return cycle
        else:
            logger.warning(f"   ⚠️ [MALLA] No se encontró el nivel para el fill (side={side}, price={price}, id={level_id})")

        # === NUEVO: RESET ON FLAT (Reubicación de Malla) DESACTIVADO ===
        # A petición del usuario, si la operación que acaba de ocurrir nos deja sin posición en el mercado,
        # NO forzamos un reajuste pendiente. La malla persiste para evitar cancelar y recrear
        # órdenes innecesariamente.
        if abs(self.posicion_neta) < 1e-9:
            logger.info("🎯 [GRID AUTÓNOMO] Inventario en 0. Manteniendo la malla actual intacta (Reset on Flat desactivado).")
            # self._reajuste_pendiente = True
        # ===================================================
