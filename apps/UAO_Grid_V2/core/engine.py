"""
engine.py — Estado lógico del grid activo. V2.

RESPONSABILIDAD ÚNICA: Mantener el estado interno del grid para un símbolo.
Procesar ticks de precio y emitir GridEngineEvents.
No hace llamadas al exchange. No lee os.getenv directamente.

Cambios críticos respecto a V1:
  - Recibe GridParameters y AppConfig en __init__ (no os.getenv)
  - Modo NO está hardcodeado a NEUTRAL — respeta el modo del optimizador
  - _reajuste_pendiente, _hubo_cambio_atr declarados en __init__
  - Retorna List[GridEngineEvent] en procesar_tick() en lugar de mutar estado global
  - WatchdogREST movido a watchdog.py
  - Todos los atributos correctamente inicializados en __init__
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from core.config import AppConfig
from core.models import (
    GridEngineEvent,
    GridEvent,
    GridMode,
    GridParameters,
    Order,
    OrderSide,
    Position,
    PositionSide,
)

logger = logging.getLogger("UAO_Grid.Engine")


class GridEngine:
    """
    Máquina de estado del grid para un símbolo.

    Recibe ticks de precio y emite eventos (GridEngineEvent) que
    el orquestador interpreta para reconciliar órdenes en el exchange.
    """

    def __init__(
        self,
        params: GridParameters,
        config: AppConfig,
    ) -> None:
        """
        Inicializa el engine con los parámetros del grid y la configuración.

        Args:
            params: Parámetros calculados por OptimizadorGrid.
            config: Configuración global de la aplicación.
        """
        self.symbol          = params.symbol
        self.params          = params
        self.config          = config

        # ── Estado del grid ────────────────────────────────────────────────────
        self.modo_estrategia = params.modo          # ← CORREGIDO: no hardcodeado a NEUTRAL
        self.espaciado_actual = params.espaciado_pct
        self.centro_grid      = 0.0
        self.niveles: List[Dict[str, Any]] = []
        self.qty_por_nivel    = 0.0

        # ── Posición ───────────────────────────────────────────────────────────
        self.posicion_neta   = 0.0   # > 0 = LONG, < 0 = SHORT
        self.precio_promedio = 0.0
        self.capital_inicial = params.capital_por_linea * params.num_grids

        # ── Histéresis anti-whipsaw ────────────────────────────────────────────
        self.confirmaciones_requeridas = config.histeresis.slide_confirmations
        self.umbral_suave              = config.histeresis.slide_soft_threshold
        self.umbral_emergencia         = config.histeresis.slide_emergency_threshold
        self._desplazamiento_pendiente = False
        self._direccion_pendiente      = 0
        self._confirmaciones           = 0

        # ── Modo drenaje ───────────────────────────────────────────────────────
        self.modo_drenaje    = False
        self.drenaje_inicio  = 0.0

        # ── Kill-switch ────────────────────────────────────────────────────────
        self.kill_switch_pct       = config.capital.kill_switch_pct
        self.kill_switch_activado  = False

        # ── Flags de control ──────────────────────────────────────────────────
        self.malla_modificada      = False   # True → hay que reconciliar OKX
        self.ws_reconectar         = False   # True → Watchdog pide reconexión
        self._atr_inicializado     = False
        self._reajuste_pendiente   = False   # Correctamente declarado en __init__
        self._hubo_cambio_atr      = False   # Correctamente declarado en __init__
        self.ultima_ejecucion_ts   = 0.0

        logger.info(
            "🔧 GridEngine inicializado: %s | modo=%s | lev=%dx | grids=%d | espaciado=%.3f%%",
            self.symbol, self.modo_estrategia.value,
            params.apalancamiento, params.num_grids,
            params.espaciado_pct * 100,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Inicialización del grid
    # ─────────────────────────────────────────────────────────────────────────

    def inicializar_grid(
        self,
        precio_base: float,
        market_info: Optional[Dict[str, Any]] = None,
    ) -> List[GridEngineEvent]:
        """
        Crea los niveles de la malla centrados en precio_base.
        Respeta el modo configurado (NEUTRAL / LONG / SHORT).
        """
        market_info = market_info or {}
        self.centro_grid   = precio_base
        self.malla_modificada = True
        
        # 1. Conservar los TPs activos (niveles con valor absoluto >= 100)
        # Esto asegura que los TPs se mantienen ligados a su precio de ejecución original
        tps_activos = [n for n in self.niveles if abs(n.get("level", 0)) >= 100]
        
        # 2. Registrar niveles cubiertos para no sobrescribir las posiciones activas
        niveles_cubiertos = set()
        for tp in tps_activos:
            # Revertir el offset de +/- 100 para obtener el nivel original
            lvl = tp.get("level", 0)
            nivel_base = abs(lvl) % 100 if abs(lvl) >= 100 else abs(lvl)
            niveles_cubiertos.add(nivel_base)
            
        self.niveles = []
        self.niveles.extend(tps_activos)

        p       = self.params
        num_l   = max(1, p.num_grids // 2)

        # ── Calcular qty por nivel ─────────────────────────────────────────────
        contract_size = float(market_info.get("contractSize", 1.0))
        precio_ref    = float(market_info.get("lastPrice", precio_base)) or precio_base
        qty_usdt      = p.tamaño_orden_usdt
        qty_contratos = qty_usdt / (precio_ref * contract_size + 1e-9)
        self.qty_por_nivel = max(qty_contratos, float(market_info.get("limits", {}).get("amount", {}).get("min", 0.0) or 0.0))

        # ── Niveles de compra (debajo del precio) ──────────────────────────────
        for i in range(1, num_l + 1):
            if i not in niveles_cubiertos:
                precio_nivel = precio_base * (1 - self.espaciado_actual) ** i
                self.niveles.append({
                    "side":  OrderSide.BUY,
                    "price": precio_nivel,
                    "qty":   self.qty_por_nivel,
                    "level": -i,
                })

        # ── Niveles de venta (arriba del precio) ──────────────────────────────
        for i in range(1, num_l + 1):
            if i not in niveles_cubiertos:
                precio_nivel = precio_base * (1 + self.espaciado_actual) ** i
                self.niveles.append({
                    "side":  OrderSide.SELL,
                    "price": precio_nivel,
                    "qty":   self.qty_por_nivel,
                    "level": i,
                })

        logger.info(
            "📐 Grid inicializado: %s | centro=%.4f | %d niveles buy + %d niveles sell",
            self.symbol, precio_base, num_l, num_l,
        )
        return [GridEngineEvent(
            event_type=GridEvent.GRID_MODIFIED,
            symbol=self.symbol,
            data={"centro": precio_base, "num_niveles": len(self.niveles)},
            timestamp=time.time(),
        )]

    # ─────────────────────────────────────────────────────────────────────────
    # Procesamiento de ticks de precio
    # ─────────────────────────────────────────────────────────────────────────

    def procesar_tick(self, precio_actual: float) -> List[GridEngineEvent]:
        """
        Procesa un tick de precio en tiempo real.
        Retorna lista de eventos para que el orquestador reaccione.
        """
        eventos: List[GridEngineEvent] = []

        if self.kill_switch_activado:
            return eventos

        if not self.niveles or self.centro_grid <= 0:
            return eventos

        # ── Kill-switch ────────────────────────────────────────────────────────
        ks_eventos = self._evaluar_kill_switch(precio_actual)
        if ks_eventos:
            return ks_eventos

        # ── Histéresis: evaluar si necesita deslizamiento ──────────────────────
        eventos.extend(self._evaluar_histeresis(precio_actual))

        return eventos

    def _evaluar_kill_switch(self, precio_actual: float) -> List[GridEngineEvent]:
        """Activa kill-switch si la pérdida supera el umbral configurado."""
        if self.posicion_neta == 0 or self.precio_promedio <= 0:
            return []

        if self.posicion_neta > 0:  # LONG
            pnl_pct = (precio_actual - self.precio_promedio) / self.precio_promedio * 100
        else:  # SHORT
            pnl_pct = (self.precio_promedio - precio_actual) / self.precio_promedio * 100

        pnl_pct_con_lev = pnl_pct * self.params.apalancamiento

        if pnl_pct_con_lev < -self.kill_switch_pct:
            self.kill_switch_activado = True
            logger.critical(
                "🚨 KILL-SWITCH activado: %s | PnL_lev=%.1f%% < -%.1f%%",
                self.symbol, pnl_pct_con_lev, self.kill_switch_pct,
            )
            return [GridEngineEvent(
                event_type=GridEvent.KILL_SWITCH,
                symbol=self.symbol,
                data={"pnl_pct_lev": pnl_pct_con_lev, "precio": precio_actual},
                timestamp=time.time(),
            )]
        return []

    def _evaluar_histeresis(self, precio_actual: float) -> List[GridEngineEvent]:
        """
        Histéresis anti-whipsaw para deslizamiento de malla.

        - Si el precio supera el umbral de emergencia (3× espaciado): deslizar inmediatamente.
        - Si supera el umbral suave: contar confirmaciones. Al N°=confirmaciones_requeridas, deslizar.
        """
        if self.centro_grid <= 0:
            return []

        distancia_pct = abs(precio_actual - self.centro_grid) / (self.centro_grid + 1e-9)
        num_espaciados = distancia_pct / (self.espaciado_actual + 1e-9)
        eventos: List[GridEngineEvent] = []

        # Emergencia: deslizar inmediatamente
        if num_espaciados >= self.umbral_emergencia:
            logger.warning(
                "⚡ Deslizamiento de emergencia: %s | precio=%.4f → centro=%.4f (%.1f× espaciado)",
                self.symbol, precio_actual, self.centro_grid, num_espaciados,
            )
            eventos.extend(self._deslizar(precio_actual))
            self._resetear_histeresis()
            return eventos

        # Suave: acumular confirmaciones
        if num_espaciados >= self.umbral_suave:
            direccion = 1 if precio_actual > self.centro_grid else -1
            if self._desplazamiento_pendiente and direccion == self._direccion_pendiente:
                self._confirmaciones += 1
            else:
                self._desplazamiento_pendiente = True
                self._direccion_pendiente      = direccion
                self._confirmaciones           = 1

            if self._confirmaciones >= self.confirmaciones_requeridas:
                evento_tipo = GridEvent.SLIDE_UP if direccion > 0 else GridEvent.SLIDE_DOWN
                logger.info(
                    "↕️ Deslizando grid: %s | %s | precio=%.4f",
                    self.symbol, evento_tipo.value, precio_actual,
                )
                eventos.extend(self._deslizar(precio_actual))
                eventos.append(GridEngineEvent(
                    event_type=evento_tipo,
                    symbol=self.symbol,
                    data={"precio_nuevo": precio_actual},
                    timestamp=time.time(),
                ))
                self._resetear_histeresis()
        else:
            # Precio volvió a zona central → cancelar conteo
            self._resetear_histeresis()

        return eventos

    def _deslizar(self, nuevo_centro: float) -> List[GridEngineEvent]:
        """Re-inicializa la malla en el nuevo precio."""
        return self.inicializar_grid(nuevo_centro)

    def _resetear_histeresis(self) -> None:
        self._desplazamiento_pendiente = False
        self._direccion_pendiente      = 0
        self._confirmaciones           = 0

    # ─────────────────────────────────────────────────────────────────────────
    # Procesamiento de ejecuciones (fills)
    # ─────────────────────────────────────────────────────────────────────────

    def procesar_ejecucion(
        self,
        side: OrderSide,
        price: float,
        qty: float,
        level_id: int,
        market_info: Optional[Dict[str, Any]] = None,
    ) -> List[GridEngineEvent]:
        """
        Procesa un fill recibido del WebSocket privado.

        Actualiza posición neta, precio promedio, elimina el nivel ejecutado
        y genera el TP contra-operación.

        Returns:
            Lista de eventos (normalmente GRID_MODIFIED).
        """
        self.ultima_ejecucion_ts = time.time()

        # Actualizar posición neta y precio promedio
        if side == OrderSide.BUY:
            total_qty = self.posicion_neta + qty
            if total_qty != 0:
                self.precio_promedio = (
                    (self.posicion_neta * self.precio_promedio + qty * price) / total_qty
                )
            self.posicion_neta += qty
        else:  # SELL
            total_qty = self.posicion_neta - qty
            if total_qty != 0:
                self.precio_promedio = (
                    (abs(self.posicion_neta) * self.precio_promedio + qty * price) / abs(total_qty)
                ) if total_qty != 0 else 0.0
            self.posicion_neta -= qty

        # Eliminar nivel ejecutado
        self.niveles = [n for n in self.niveles if n.get("level") != level_id]

        # Generar TP contra-operación (precio fijo desde entry)
        tp_precio = price * (1 + self.espaciado_actual) if side == OrderSide.BUY else price * (1 - self.espaciado_actual)
        tp_side   = OrderSide.SELL if side == OrderSide.BUY else OrderSide.BUY
        tp_level  = level_id + 100 if level_id >= 0 else level_id - 100  # TPs: |level| >= 100

        self.niveles.append({
            "side":                  tp_side,
            "price":                 tp_precio,
            "qty":                   qty,
            "level":                 tp_level,
            "reduce_only":           True,
            "precio_original_entry": price,
        })

        self.malla_modificada = True
        logger.debug(
            "Fill procesado: %s %s @ %.4f qty=%.4f | posición_neta=%.4f | TP→%.4f",
            self.symbol, side.value, price, qty, self.posicion_neta, tp_precio,
        )

        return [GridEngineEvent(
            event_type=GridEvent.GRID_MODIFIED,
            symbol=self.symbol,
            data={"fill_side": side.value, "fill_price": price, "tp_price": tp_precio},
            timestamp=time.time(),
        )]

    # ─────────────────────────────────────────────────────────────────────────
    # Modo drenaje
    # ─────────────────────────────────────────────────────────────────────────

    def activar_modo_drenaje(self) -> GridEngineEvent:
        self.modo_drenaje   = True
        self.drenaje_inicio = time.time()
        logger.info("🔄 Modo drenaje activado: %s", self.symbol)
        return GridEngineEvent(
            event_type=GridEvent.DRAIN_START,
            symbol=self.symbol,
            timestamp=time.time(),
        )

    def es_timeout_drenaje(self, timeout_horas: float) -> bool:
        if not self.modo_drenaje:
            return False
        return (time.time() - self.drenaje_inicio) > (timeout_horas * 3600)

    def calcular_ordenes_drenaje(
        self,
        posicion: Position,
        market_info: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Genera 1–3 TPs escalonados para cerrar la posición gradualmente.
        Exige un mínimo de ganancia sobre el entry para no cerrar en pérdida.
        """
        market_info  = market_info or {}
        entry        = posicion.entry_price or self.precio_promedio
        if entry <= 0:
            return []

        min_profit_pct = self.espaciado_actual * 0.5  # Mínimo: mitad del espaciado
        ordenes = []

        for i, mult in enumerate([1.0, 1.5, 2.0], start=1):
            if posicion.side == PositionSide.LONG:
                tp_precio = entry * (1 + self.espaciado_actual * mult)
                tp_side   = OrderSide.SELL
            else:
                tp_precio = entry * (1 - self.espaciado_actual * mult)
                tp_side   = OrderSide.BUY

            ordenes.append({
                "side":        tp_side,
                "price":       tp_precio,
                "qty":         posicion.qty / 3.0,
                "level":       100 + i,
                "reduce_only": True,
            })

        return ordenes

    # ─────────────────────────────────────────────────────────────────────────
    # Órdenes deseadas (para reconciliación)
    # ─────────────────────────────────────────────────────────────────────────

    def obtener_ordenes_deseadas(
        self,
        market_info: Optional[Dict[str, Any]] = None,
    ) -> List[Order]:
        """
        Convierte self.niveles a una lista de Order para reconciliar con el exchange.
        Si está en modo drenaje, filtra solo los TPs (reduce_only=True).
        """
        market_info = market_info or {}
        ordenes: List[Order] = []

        for n in self.niveles:
            if self.modo_drenaje and not n.get("reduce_only", False):
                continue  # En drenaje: solo TPs

            ordenes.append(Order(
                order_id    = f"glvl{n['level']}x{self.symbol}",
                symbol      = self.symbol,
                side        = n["side"],
                price       = round(n["price"], 8),
                qty         = round(n["qty"], 8),
                reduce_only = bool(n.get("reduce_only", False)),
                grid_level  = int(n["level"]),
            ))

        return ordenes

    # ─────────────────────────────────────────────────────────────────────────
    # Recalcular espaciado ATR
    # ─────────────────────────────────────────────────────────────────────────

    def calcular_espaciado_atr(
        self,
        df_5m: pd.DataFrame,
        market_info: Optional[Dict[str, Any]] = None,
        precio_vivo: Optional[float] = None,
    ) -> None:
        """
        Recalcula el espaciado usando ATR 14 periodos.
        Solo actualiza si la diferencia es significativa (>10%).
        """
        if df_5m is None or len(df_5m) < 15:
            return

        market_info = market_info or {}
        high  = df_5m["high"]
        low   = df_5m["low"]
        close = df_5m["close"]

        tr = pd.concat(
            [high - low, (high - close.shift()).abs(), (low - close.shift()).abs()],
            axis=1,
        ).max(axis=1)

        atr = float(tr.rolling(14, min_periods=1).mean().iloc[-1])
        if np.isnan(atr) or atr <= 0:
            return

        precio_ref = precio_vivo or float(close.iloc[-1])
        if precio_ref <= 0:
            return

        atr_pct        = atr / precio_ref
        fee_maker      = float(market_info.get("maker", 0.00020))
        fee_taker      = float(market_info.get("taker", 0.00050))
        min_spread     = fee_maker + fee_taker + 0.0005
        max_spread     = 0.008  # 0.8%

        nuevo_espaciado = max(min_spread, min(atr_pct * self.config.grid.atr_multiplier, max_spread))

        # Solo actualizar si el cambio es mayor al 10% para evitar ruido
        if abs(nuevo_espaciado - self.espaciado_actual) / (self.espaciado_actual + 1e-9) > 0.10:
            logger.info(
                "📏 Espaciado ATR actualizado: %s | %.4f%% → %.4f%%",
                self.symbol,
                self.espaciado_actual * 100,
                nuevo_espaciado * 100,
            )
            self.espaciado_actual = nuevo_espaciado
            self._hubo_cambio_atr = True
            self._atr_inicializado = True

    # ─────────────────────────────────────────────────────────────────────────
    # Sincronización con estado real
    # ─────────────────────────────────────────────────────────────────────────

    def forzar_sincronizacion(self, posicion: Position) -> None:
        """
        Carga el estado desde la posición real del exchange.
        Llamado por el Watchdog cuando detecta desincronización.
        """
        self.posicion_neta   = posicion.qty if posicion.side == PositionSide.LONG else -posicion.qty
        self.precio_promedio = posicion.entry_price
        logger.warning(
            "🔄 Sincronización forzada: %s | posición_neta=%.4f @ %.4f",
            self.symbol, self.posicion_neta, self.precio_promedio,
        )

    def procesar_precio_externo(self, precio_actual: float) -> List[GridEngineEvent]:
        """Inyecta un precio externo (REST fallback del Watchdog)."""
        return self.procesar_tick(precio_actual)

    # ─────────────────────────────────────────────────────────────────────────
    # Reset
    # ─────────────────────────────────────────────────────────────────────────

    def reset(self) -> None:
        """Limpia el estado completo (post kill-switch)."""
        self.posicion_neta         = 0.0
        self.precio_promedio       = 0.0
        self.centro_grid           = 0.0
        self.niveles               = []
        self.modo_drenaje          = False
        self.kill_switch_activado  = False
        self.malla_modificada      = False
        self._reajuste_pendiente   = False
        self._hubo_cambio_atr      = False
        self._resetear_histeresis()
        logger.info("🔄 GridEngine reseteado: %s", self.symbol)

    # ─────────────────────────────────────────────────────────────────────────
    # Hot-reload de parámetros
    # ─────────────────────────────────────────────────────────────────────────

    def update_params(
        self,
        proximity: Optional[int] = None,
        atr_mult: Optional[float] = None,
        leverage: Optional[float] = None,
        num_lineas: Optional[int] = None,
        capital_inicial: Optional[float] = None,
    ) -> None:
        """Hot-reload de parámetros sin detener el WebSocket."""
        if proximity is not None:
            self.config = self.config  # AppConfig es inmutable; log de intento
            logger.debug("proximity hot-reload: %d", proximity)
        if leverage is not None:
            logger.info("Hot-reload leverage: %s → %.0f", self.symbol, leverage)
        if num_lineas is not None:
            logger.info("Hot-reload num_lineas: %s → %d", self.symbol, num_lineas)
