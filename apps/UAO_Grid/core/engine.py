"""
engine.py — Cerebro del Grid.
Maneja el estado lógico del grid, ATR dinámico, histéresis, modo drenaje y kill-switch.
"""
import logging
import os
import time
from typing import List, Dict, Any, Optional
import numpy as np
import pandas as pd

from core.providers import Order, Position

logger = logging.getLogger("UAO_Sclaping.GridEngine")


class GridEngine:
    def __init__(self, symbol: str, capital_inicial: float, leverage: float):
        self.current_symbol = symbol
        self.capital_inicial = capital_inicial
        self.leverage = leverage
        
        self.posicion_neta = 0.0
        self.precio_promedio = 0.0
        
        # Parámetros Base
        # La cantidad total de líneas se divide a la mitad (para buy y sell)
        total_configurado = int(os.getenv("GRID_NUM_LINEAS_LADO", 10))
        self.num_lineas_lado = max(1, total_configurado)
        self.max_proximity_orders = int(os.getenv("GRID_PROXIMITY_ORDERS", 2))
        self.atr_multiplicador = float(os.getenv("GRID_ATR_MULTIPLIER", 1.5))
        
        # Histéresis Anti-Whipsaw
        self.confirmaciones_requeridas = int(os.getenv("GRID_SLIDE_CONFIRMATIONS", 2))
        self.umbral_suave = float(os.getenv("GRID_SLIDE_SOFT_THRESHOLD", 2.50)) # Aumentado de 1.20 a 2.50
        self.umbral_emergencia = float(os.getenv("GRID_SLIDE_EMERGENCY_THRESHOLD", 3.00)) # Aumentado proporcionalmente
        
        self._desplazamiento_pendiente = False
        self._direccion_pendiente = 0
        self._confirmaciones = 0
        
        # Grid Interno
        self.espaciado_actual = 0.003
        self.centro_grid = 0.0
        self.niveles: List[Dict[str, Any]] = []
        self.qty_por_nivel = 0.0
        
        # Modo Drenaje
        self.modo_drenaje = False
        self.drenaje_inicio = 0.0
        
        # Kill-Switch
        self.kill_switch_pct = float(os.getenv("GRID_KILL_SWITCH_PCT", 50.0))
        self.kill_switch_activado = False
        self.posicion_no_operable = False

        # Control de envío REST: solo reconciliar OKX cuando la malla cambió.
        self.malla_modificada = False
        
        # Señal reconexión
        self.ws_reconectar = False
        
        # Tracking de ejecuciones
        self.ultima_ejecucion_ts = 0.0
        self._atr_inicializado = False

    def update_params(self, proximity=None, atr_mult=None, leverage=None, num_lineas=None, capital_inicial=None):
        """Hot-reload de parámetros sin detener WebSocket."""
        if proximity is not None: self.max_proximity_orders = int(proximity)
        if atr_mult is not None: self.atr_multiplicador = float(atr_mult)
        if leverage is not None: 
            self.leverage = float(leverage)
            if self.centro_grid > 0: self._recalcular_inversion_por_nivel()
        if num_lineas is not None: 
            self.num_lineas_lado = max(1, int(num_lineas) // 2)
        if capital_inicial is not None:
            self.capital_inicial = float(capital_inicial)
            if self.centro_grid > 0: self._recalcular_inversion_por_nivel()

    def _recalcular_inversion_por_nivel(self):
        if self.num_lineas_lado <= 0: return
        
        # El capital_inicial se divide en partes iguales entre TODAS las líneas de la malla (compras + ventas)
        total_lineas = self.num_lineas_lado * 2
        
        # Inversión real por línea * apalancamiento = Tamaño total de la orden a enviar al exchange
        self.inversion_por_nivel = (self.capital_inicial / total_lineas) * self.leverage

    def calcular_espaciado_atr(self, df_5m: pd.DataFrame, market_info: dict = {}):
        """Calcula el spread dinámico permitiendo expansión y contracción seguras."""
        if df_5m is None or len(df_5m) < 15:
            return
            
        # 1. Liberamos el candado: permitimos que recalcule incluso con posición abierta
        self._atr_inicializado = True
            
        high = df_5m['high']
        low = df_5m['low']
        close = df_5m['close']
        
        tr1 = high - low
        tr2 = (high - close.shift()).abs()
        tr3 = (low - close.shift()).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(14).mean().iloc[-1]
        
        precio_actual = close.iloc[-1]
        if precio_actual > 0 and atr > 0:
            espaciado_calculado = (atr * self.atr_multiplicador) / precio_actual
            
            # ── A. DISTANCIA DINÁMICA CON PISO DE COMISIONES ──
            fee_maker = float(market_info.get("maker", 0.00020))
            fee_taker = float(market_info.get("taker", 0.00050))
            
            # Piso dinámico: Cubrir doble comisión (entrada + salida) + un margen neto de ganancia (ej. 0.15%)
            self.min_spread_rentable = (fee_maker + fee_taker) + 0.0015
            max_spread = 0.0080  # Techo: 0.80%
            
            # El espaciado nunca podrá ser menor a lo que garantiza ganancia real
            nuevo_espaciado = max(self.min_spread_rentable, min(espaciado_calculado, max_spread))
            
            if abs(self.espaciado_actual - nuevo_espaciado) > 1e-5:
                self.espaciado_actual = nuevo_espaciado
                
                cobertura_deseada = 0.10 
                lineas_calculadas_totales = int(cobertura_deseada / self.espaciado_actual)
                # Dividir a la mitad (sell y buy)
                # Priorizar el valor de la IA (self.num_lineas_lado) si es mayor que la cobertura mínima de seguridad
                self.num_lineas_lado = max(self.num_lineas_lado, max(2, lineas_calculadas_totales // 2))
                self._recalcular_inversion_por_nivel()
                self._hubo_cambio_atr = True
                
                logger.info(
                    f"🤖 [Auto-Grid Inteligente] Distancia: {self.espaciado_actual * 100:.2f}% | "
                    f"Líneas/lado: {self.num_lineas_lado} | "
                    f"Cobertura total: {self.num_lineas_lado * 2 * self.espaciado_actual * 100:.2f}%"
                )
            else:
                self._hubo_cambio_atr = False

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

    def inicializar_grid(self, precio_base: float, num_grids_sugerido: int = 10):
        """Crea la malla alrededor del precio base respetando los Take Profits activos."""
        self.centro_grid = precio_base
        self.num_lineas_lado = max(1, num_grids_sugerido // 2)
        self._recalcular_inversion_por_nivel()

        # 1. Salvar los Take Profits activos (niveles >= 100)
        tps_activos = [n for n in getattr(self, "niveles", []) if abs(n.get("level", 0)) >= 100]

        # 2. Mapear que lineas originales ya fueron ejecutadas para no recrearlas.
        # Si un TP es SELL, la original fue una BUY (nivel negativo).
        # Si un TP es BUY, la original fue una SELL (nivel positivo).
        niveles_cubiertos = set()
        for tp in tps_activos:
            nivel_base = abs(tp.get("level", 0)) // 100
            if nivel_base <= 0:
                continue
            if tp.get("side") == "SELL":
                niveles_cubiertos.add(-nivel_base)
            else:
                niveles_cubiertos.add(nivel_base)

        # 3. Limpiar y restaurar los TPs en memoria.
        self.niveles = []
        self.niveles.extend(tps_activos)

        # 4. Dibujar nuevas lineas base, omitiendo las que ya tienen TP en curso.
        margen_seguro = getattr(self, 'min_spread_rentable', 0.0025)

        for i in range(1, self.num_lineas_lado + 1):
            if i not in niveles_cubiertos:
                # LÓGICA ANTI-PÉRDIDAS PARA LONG: El precio base para vender (TP) 
                # NUNCA debe estar por debajo del precio promedio + margen seguro.
                if self.posicion_neta > 1e-9 and self.precio_promedio > 0:
                    piso_rentable = self.precio_promedio * (1 + margen_seguro)
                    base_sell = max(self.centro_grid, piso_rentable)
                else:
                    base_sell = self.centro_grid
                    
                precio_sell = base_sell * (1 + (self.espaciado_actual * i))
                qty_sell = self.inversion_por_nivel / precio_sell
                self.niveles.append({"side": "SELL", "price": precio_sell, "qty": qty_sell, "level": i})

            if -i not in niveles_cubiertos:
                # LÓGICA ANTI-PÉRDIDAS PARA SHORT: El precio base para comprar (TP)
                # NUNCA debe estar por encima del precio promedio - margen seguro.
                if self.posicion_neta < -1e-9 and self.precio_promedio > 0:
                    techo_rentable = self.precio_promedio * (1 - margen_seguro)
                    base_buy = min(self.centro_grid, techo_rentable)
                else:
                    base_buy = self.centro_grid
                    
                precio_buy = base_buy * (1 - (self.espaciado_actual * i))
                qty_buy = self.inversion_por_nivel / precio_buy
                self.niveles.append({"side": "BUY", "price": precio_buy, "qty": qty_buy, "level": -i})

        self.malla_modificada = True

    # ── KILL SWITCH ──

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
        """Re-inicializa la malla centrada en el nuevo precio."""
        self.inicializar_grid(nuevo_centro, num_grids_sugerido=self.num_lineas_lado * 2)

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
            self.inicializar_grid(self.precio_promedio, num_grids_sugerido=self.num_lineas_lado * 2)
        else:
            self.niveles = []

    def obtener_ordenes_deseadas(self, precio_actual: float, market_info: dict) -> List[Order]:
        """
        Retorna las órdenes que DEBERÍAN existir ahora mismo en el Exchange.
        Retorna todos los niveles calculados para que las órdenes descansen en OKX.
        """
        if not self.niveles or self.kill_switch_activado:
            return []

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

    def procesar_ejecucion_simulada(self, side: str, price: float, qty: float, level_id: int = None):
        """Simula el PnL y posición, y RECALCULA LA MALLA DINÁMICAMENTE."""
        self.ultima_ejecucion_ts = time.time()
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
            n_ejecutado = self.niveles.pop(nivel_match)
            
            # GENERAR EL TAKE PROFIT DINÁMICO EN CONTRA
            tp_side = "SELL" if side == "BUY" else "BUY"
            mult = (1 + self.espaciado_actual) if side == "BUY" else (1 - self.espaciado_actual)
            tp_price = price * mult
            
            # En lugar de multiplicar ciegamente por 100:
            nivel_origen = abs(n_ejecutado["level"])

            # Si el nivel ya es un TP (ej. >= 100), lo restauramos a linea base.
            tp_level = (nivel_origen // 100) if nivel_origen >= 100 else (nivel_origen * 100)
            if tp_level == 0: tp_level = 9999
            
            self.niveles.append({
                "side": tp_side,
                "price": tp_price,
                "qty": n_ejecutado["qty"],
                "level": tp_level,
                "precio_original_entrada": price
            })
            self.malla_modificada = True
            logger.info(f"   🔄 [MALLA] Nivel {n_ejecutado['level']} llenado. TP {tp_side} creado a ${tp_price:.4f}")
        else:
            logger.warning(f"   ⚠️ [MALLA] No se encontró el nivel para el fill (side={side}, price={price}, id={level_id})")

        # === NUEVO: RESET ON FLAT (Reubicación de Malla) ===
        # Si la operación que acaba de ocurrir nos deja sin posición en el mercado,
        # forzamos un reajuste pendiente. El orquestador verá esto en el próximo tick
        # y re-dibujará la malla usando el precio actual y el ATR más fresco.
        if abs(self.posicion_neta) < 1e-9:
            logger.info("🎯 [GRID AUTÓNOMO] Inventario en 0. Solicitando re-ubicación completa de las líneas de compra y venta (Reset on Flat).")
            self._reajuste_pendiente = True
        # ===================================================
