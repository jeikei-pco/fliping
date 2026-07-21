"""
orquestador.py — Director del sistema. V2.

RESPONSABILIDAD ÚNICA: Coordinar el ciclo de vida completo del bot.
Instanciar, conectar y coordinar todos los módulos. Sin lógica de negocio.

El orquestador NO:
  - Calcula métricas (Analizador)
  - Calcula parámetros del grid (Optimizador)
  - Simula estrategias (Backtester)
  - Mantiene estado del grid (Engine)
  - Habla con el exchange (Provider)
  - Decide overrides de IA (IAOptimizer)

El orquestador SÍ:
  - Crea y conecta todos los módulos anteriores
  - Gestiona el ciclo escaneo → backtest → operación
  - Gestiona los WebSockets públicos y privados
  - Publica el estado al frontend (webhook)
  - Gestiona múltiples símbolos simultáneos con locks

Cambios respecto a V1:
  - AppConfig inyectado una sola vez al inicio (no os.getenv disperso)
  - Activos Core como lista configurable (no hardcodeado)
  - Bug o.get('timestamp') sobre objeto Order: CORREGIDO
  - self.last_30_candles por símbolo (no global compartido)
  - active_symbols con lock consistente
  - WatchdogREST en watchdog.py (no aquí)
  - orquestador pasa de 994 líneas a ~450 líneas
"""
from __future__ import annotations

import json
import logging
import queue
import ssl
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

import requests
import websocket

from core.analizador import analizar_lote
from core.backtester import BacktestResult, backtest_lote
from core.config import AppConfig
from core.database import Database
from core.engine import GridEngine
from core.ia_optimizer import IAOptimizerWorker
from core.models import (
    GridEngineEvent,
    GridEvent,
    GridParameters,
    IAOverrides,
    Order,
    OrderSide,
)
from core.optimizador import OptimizadorGrid
from core.providers import OKXProvider, filtrar_por_volumen, obtener_futuros_usdt
from core.watchdog import WatchdogREST

logger = logging.getLogger("UAO_Grid.Orquestador")


class GridOrquestador:
    """
    Director del sistema de grid trading.
    Instanciado una vez en main.py.
    """

    def __init__(self, config: AppConfig) -> None:
        self.config   = config
        self.db       = Database(config.database.db_path)
        self.provider = OKXProvider(config)

        # ── Estado multi-símbolo ────────────────────────────────────────────────
        self._engines_lock    = threading.RLock()
        self._engines:        Dict[str, GridEngine]      = {}
        self._mejores_params: Dict[str, BacktestResult]  = {}
        self._candles_por_sym: Dict[str, list]           = {}   # ← por símbolo, no global

        # ── IA Optimizer ────────────────────────────────────────────────────────
        self._overrides_lock  = threading.Lock()
        self._ia_overrides    = self.db.get_ia_overrides()
        self._optimizador     = OptimizadorGrid(config, self._ia_overrides)

        # ── Webhooks ────────────────────────────────────────────────────────────
        self._webhook_queue   = queue.Queue(maxsize=50)
        self._last_fills_history: list = []

        # ── Control de ciclo ────────────────────────────────────────────────────
        self._wakeup_event     = threading.Event()
        self._stop_event       = threading.Event()
        self._ciclo_count      = 0

    # ─────────────────────────────────────────────────────────────────────────
    # Entry point
    # ─────────────────────────────────────────────────────────────────────────

    def run(self) -> None:
        """Inicia todos los daemons y el loop principal."""
        logger.info("🚀 GridOrquestador iniciado — modo=%s", self.config.exchange.mode)

        # Daemons
        self._iniciar_ia_worker()
        self._iniciar_watchdog()
        self._iniciar_webhook_worker()
        self._iniciar_ws_privado()

        # Loop principal de escaneo
        while not self._stop_event.is_set():
            try:
                self._ciclo_reescaneo()
            except Exception as exc:
                logger.error("❌ Ciclo reescaneo error: %s", exc, exc_info=True)

            self._wakeup_event.wait(timeout=self.config.scanner.cycle_seconds)
            self._wakeup_event.clear()

    def stop(self) -> None:
        self._stop_event.set()
        self._wakeup_event.set()

    # ─────────────────────────────────────────────────────────────────────────
    # Ciclo de escaneo principal
    # ─────────────────────────────────────────────────────────────────────────

    def _ciclo_reescaneo(self) -> None:
        self._ciclo_count += 1
        logger.info("🔄 Ciclo #%d iniciado", self._ciclo_count)

        # 1. Hot-reload overrides de IA
        self._recargar_overrides()

        # 2. Verificar timeouts de drenaje
        self._verificar_drenajes()

        # 3. Análisis + Backtest
        mejores = self._analizar_y_backtestear()
        if not mejores:
            logger.warning("⚠️ Sin símbolos rentables en este ciclo")
            return

        # 4. Obtener posiciones actuales del exchange
        posiciones_activas = {p.symbol: p for p in self.provider.get_open_positions()}

        # 5. Gestión de activos core siempre activos
        for sym in self.config.scanner.core_symbols:
            if sym not in self._engines and sym in [m.symbol for m in mejores]:
                self._iniciar_operacion(sym)

        # 6. Iniciar nuevas operaciones
        max_simbolos = self.config.scanner.symbols_to_trade
        with self._engines_lock:
            activos = set(self._engines.keys())

        for resultado in mejores:
            if len(activos) >= max_simbolos:
                break
            if resultado.symbol not in activos:
                if self._iniciar_operacion(resultado.symbol):
                    activos.add(resultado.symbol)

        logger.info("✅ Ciclo #%d completado | Activos: %s", self._ciclo_count, list(activos))

    def _analizar_y_backtestear(self) -> List[BacktestResult]:
        """Pipeline: futuros USDT → volumen → análisis → backtest."""
        try:
            # 1. Filtro de mercados
            markets = self.provider.fetch_markets()
            futuros = obtener_futuros_usdt(markets)

            # 2. Filtro por volumen
            tickers = self.provider.fetch_tickers(futuros[:200])
            filtrados = filtrar_por_volumen(tickers, futuros, self.config.scanner.min_volume_usdt)

            # 3. Filtro de blacklist
            blacklist = self.db.get_blacklist()
            filtrados = [s for s in filtrados if s not in blacklist]

            if not filtrados:
                return []

            logger.info("📊 Analizando %d símbolos...", len(filtrados))
            metricas_lista = analizar_lote(
                exchange=self.provider.exchange,
                simbolos=filtrados[:self.config.scanner.max_symbols_to_analyze],
                tickers_info=tickers,
                workers=self.config.scanner.analysis_workers,
            )

            if not metricas_lista:
                return []

            # Guardar en caché para la IA
            for m in metricas_lista:
                self.db.save_market_metrics(m.symbol, m.__dict__, m.score)

            # 4. Descargar datos para backtest de todos los símbolos analizados.
            candidatos = metricas_lista
            datos_backtest = []
            for metrics in candidatos:
                df = self.provider.fetch_ohlcv(metrics.symbol, timeframe="5m", limit=288)
                if not df.empty:
                    datos_backtest.append((df, metrics))

            # 5. Backtest paralelo
            capital = self.config.capital.capital_per_operation
            resultados = backtest_lote(datos_backtest, capital_total=capital)

            # 6. Guardar en DB para aprendizaje IA
            for r in resultados:
                self.db.save_backtest(r)

            # 7. Guardar scanner state
            self.db.save_scanner_state(
                [r.__dict__ for r in resultados],
                cycle_count=self._ciclo_count,
            )

            return resultados[:self.config.scanner.backtest_top_n]

        except Exception as exc:
            logger.error("_analizar_y_backtestear error: %s", exc, exc_info=True)
            return []

    # ─────────────────────────────────────────────────────────────────────────
    # Gestión de operaciones por símbolo
    # ─────────────────────────────────────────────────────────────────────────

    def _iniciar_operacion(self, symbol: str) -> bool:
        """
        Crea el GridEngine y lanza el loop operativo para un símbolo.
        Retorna True si se inició correctamente.
        """
        # Buscar resultado de backtest
        with self._engines_lock:
            resultado = self._mejores_params.get(symbol)

        if not resultado:
            logger.warning("No hay backtest para %s — no se inicia", symbol)
            return False

        try:
            # Calcular parámetros finales usando el optimizador con overrides
            metricas_cache = self.db.get_market_metrics_cache(max_age_minutes=35)
            metrics = next((m["metrics"] for m in metricas_cache if m["symbol"] == symbol), None)
            if not metrics:
                logger.warning("Sin métricas en caché para %s", symbol)
                return False

            # Ajustar leverage en el exchange
            self.provider.set_leverage(resultado.apalancamiento, symbol)

            # Crear parámetros
            params = GridParameters(
                symbol            = symbol,
                valido            = True,
                modo              = resultado.modo_optimo,
                precio_actual     = 0.0,  # Se inicializa en el primer tick
                apalancamiento    = resultado.apalancamiento,
                num_grids         = resultado.num_grids,
                espaciado_pct     = resultado.espaciado_pct,
                capital_por_linea = self.config.capital.capital_per_operation / resultado.num_grids,
                tamaño_orden_usdt = self.config.capital.capital_per_operation,
                limite_superior   = 0.0,
                limite_inferior   = 0.0,
            )

            engine = GridEngine(params, self.config)

            with self._engines_lock:
                self._engines[symbol] = engine
                self._mejores_params[symbol] = resultado

            # Lanzar loop operativo
            hilo = threading.Thread(
                target=self._loop_operativo,
                args=(symbol,),
                daemon=True,
                name=f"Loop-{symbol}",
            )
            hilo.start()
            logger.info("✅ Operación iniciada: %s | modo=%s", symbol, resultado.modo_optimo.value)
            return True

        except Exception as exc:
            logger.error("_iniciar_operacion %s error: %s", symbol, exc)
            return False

    def _loop_operativo(self, symbol: str) -> None:
        """
        WebSocket público de tickers para un símbolo.
        Ejecuta el loop de ticks en tiempo real.
        """
        exchange = self.provider.exchange
        ws_url   = "wss://ws.okx.com:8443/ws/v5/public"
        if exchange.urls.get("api", {}).get("ws", {}).get("public"):
            ws_url = exchange.urls["api"]["ws"]["public"]

        inst_id  = symbol.replace("/", "-").replace(":USDT", "-SWAP")
        sub_msg  = json.dumps({
            "op": "subscribe",
            "args": [{"channel": "tickers", "instId": inst_id}],
        })

        def on_message(ws, msg):
            try:
                data = json.loads(msg)
                if data.get("event"):
                    return

                for item in data.get("data", []):
                    precio = float(item.get("last", 0.0))
                    if precio <= 0:
                        continue

                    # Notificar Watchdog
                    if hasattr(self, "_watchdog"):
                        self._watchdog.registrar_tick(symbol, precio)

                    with self._engines_lock:
                        engine = self._engines.get(symbol)
                    if not engine:
                        ws.close()
                        return

                    # Inicializar grid en el primer tick
                    if engine.centro_grid <= 0:
                        market_info = exchange.markets.get(symbol, {})
                        engine.inicializar_grid(precio, market_info)

                    # Procesar tick
                    eventos = engine.procesar_tick(precio)
                    self._procesar_eventos(symbol, eventos, precio)

                    # Reconciliar si hubo cambios
                    if engine.malla_modificada:
                        self._reconciliar(symbol)
                        engine.malla_modificada = False

                    # Webhook
                    self._encolar_webhook(symbol, precio, engine)

            except Exception as exc:
                logger.error("on_message %s: %s", symbol, exc)

        def on_error(ws, err):
            logger.error("WS error %s: %s", symbol, err)
            with self._engines_lock:
                engine = self._engines.get(symbol)
            if engine:
                engine.ws_reconectar = True

        def on_close(ws, code, msg):
            logger.warning("WS cerrado: %s (code=%s)", symbol, code)

        def on_open(ws):
            ws.send(sub_msg)
            logger.info("📡 WS conectado: %s", symbol)

        while True:
            with self._engines_lock:
                engine = self._engines.get(symbol)
            if not engine:
                break
            if engine.kill_switch_activado:
                self._manejar_kill_switch(symbol)
                break

            try:
                ws = websocket.WebSocketApp(
                    ws_url,
                    on_message=on_message,
                    on_error=on_error,
                    on_close=on_close,
                    on_open=on_open,
                )
                ws.run_forever(
                    sslopt={"cert_reqs": ssl.CERT_NONE},
                    ping_interval=20,
                    ping_timeout=10,
                    reconnect=3,
                )
            except Exception as exc:
                logger.error("loop_operativo %s: %s", symbol, exc)

            time.sleep(5)  # Pausa antes de reconectar

    def _reconciliar(self, symbol: str) -> None:
        """Reconcilia las órdenes en el exchange con el estado del engine."""
        try:
            with self._engines_lock:
                engine = self._engines.get(symbol)
            if not engine:
                return

            market_info = self.provider.exchange.markets.get(symbol, {})

            # Obtener precio actual para determinar market_info
            ticker = self.provider.fetch_tickers([symbol])
            precio = float(ticker.get(symbol, {}).get("last", 0.0))

            deseadas = engine.obtener_ordenes_deseadas(market_info)
            actuales = self.provider.get_open_orders(symbol)
            self.provider.reconciliar_ordenes(deseadas, actuales)

        except Exception as exc:
            if "51155" in str(exc):
                logger.critical("🚫 Símbolo restringido: %s", symbol)
                self._detener_operacion(symbol, reason="51155_restricted")
            else:
                logger.error("reconciliar %s: %s", symbol, exc)

    # ─────────────────────────────────────────────────────────────────────────
    # Procesamiento de eventos del Engine
    # ─────────────────────────────────────────────────────────────────────────

    def _procesar_eventos(
        self, symbol: str, eventos: List[GridEngineEvent], precio_actual: float
    ) -> None:
        """Interpreta los eventos emitidos por el GridEngine."""
        for evento in eventos:
            if evento.event_type == GridEvent.KILL_SWITCH:
                logger.critical("🚨 Kill-switch event recibido: %s", symbol)
                self._manejar_kill_switch(symbol)

            elif evento.event_type in (GridEvent.SLIDE_UP, GridEvent.SLIDE_DOWN):
                logger.info("↕️ Grid deslizado: %s → %s", symbol, evento.event_type.value)

    def _manejar_kill_switch(self, symbol: str) -> None:
        """Gestiona el kill-switch: cancelar órdenes, cerrar posición, detener engine."""
        logger.critical("🚨 Gestionando kill-switch: %s", symbol)
        try:
            self.provider.cancel_all_orders(symbol)
            self.provider.close_position_market(symbol)
        except Exception as exc:
            logger.error("kill-switch error: %s", exc)
        self._detener_operacion(symbol, reason="kill_switch")

    def _detener_operacion(self, symbol: str, reason: str = "") -> None:
        """Limpia el estado del engine y lo elimina del dict activo."""
        with self._engines_lock:
            engine = self._engines.pop(symbol, None)
            self._mejores_params.pop(symbol, None)
        if engine:
            engine.reset()
        if reason:
            self.db.add_to_blacklist(symbol, reason=reason)
            logger.info("🔴 Operación detenida: %s (%s)", symbol, reason)

    # ─────────────────────────────────────────────────────────────────────────
    # Fills del WebSocket privado
    # ─────────────────────────────────────────────────────────────────────────

    def manejar_fill(self, fill_data: Dict[str, Any]) -> None:
        """
        Callback del WebSocket privado.
        Procesa un fill real y actualiza el engine correspondiente.
        """
        try:
            symbol    = self._symbol_from_inst_id(fill_data.get("instId", ""))
            side_raw  = str(fill_data.get("side", "")).upper()
            side      = OrderSide.BUY if side_raw == "BUY" else OrderSide.SELL
            price     = float(fill_data.get("fillPx",    0.0))
            qty       = float(fill_data.get("fillSz",    0.0))
            fee       = abs(float(fill_data.get("fee",   0.0)))
            trade_id  = fill_data.get("tradeId",  str(uuid.uuid4()))
            cl_ord_id = fill_data.get("clOrdId",  "")
            level_id  = self._parse_grid_level(cl_ord_id)

            if not symbol or price <= 0 or qty <= 0:
                return

            with self._engines_lock:
                engine = self._engines.get(symbol)

            if engine:
                market_info = self.provider.exchange.markets.get(symbol, {})
                eventos = engine.procesar_ejecucion(side, price, qty, level_id, market_info)
                self._procesar_eventos(symbol, eventos, price)

                # Reconciliar inmediatamente tras fill
                self._reconciliar(symbol)
                engine.malla_modificada = False

            # PnL aproximado (comisiones ya incluidas)
            pnl_estimado = 0.0  # Se calcula exacto al cerrar el TP
            self.db.record_trade(
                trade_id=trade_id, symbol=symbol,
                side=side_raw, price=price, qty=qty,
                pnl=pnl_estimado, fee=fee,
            )
            self.db.update_ml_session_trade(symbol, pnl_estimado)

            # Historial de fills para webhook
            self._last_fills_history.append(fill_data)
            if len(self._last_fills_history) > 50:
                self._last_fills_history.pop(0)

        except Exception as exc:
            logger.error("manejar_fill error: %s", exc)

    # ─────────────────────────────────────────────────────────────────────────
    # Overrides de IA
    # ─────────────────────────────────────────────────────────────────────────

    def _recargar_overrides(self) -> None:
        """Carga los overrides de la DB y actualiza el optimizador."""
        with self._overrides_lock:
            overrides = self.db.get_ia_overrides()
            self._ia_overrides = overrides
            self._optimizador.actualizar_overrides(overrides)

    def _on_nuevos_overrides(self, overrides: IAOverrides) -> None:
        """Callback del IAOptimizerWorker cuando la IA genera nuevos overrides."""
        self.db.save_ia_overrides(overrides)
        with self._overrides_lock:
            self._ia_overrides = overrides
            self._optimizador.actualizar_overrides(overrides)
        # Despertar el ciclo para aplicar en el próximo escaneo
        self._wakeup_event.set()
        logger.info("🤖 Nuevos overrides de IA aplicados — despertando ciclo")

    def _verificar_drenajes(self) -> None:
        """Verifica si algún engine en modo drenaje superó el timeout."""
        with self._engines_lock:
            symbols = list(self._engines.keys())
        for sym in symbols:
            with self._engines_lock:
                engine = self._engines.get(sym)
            if engine and engine.modo_drenaje:
                if engine.es_timeout_drenaje(self.config.grid.drain_timeout_hours):
                    logger.warning("🔄 Timeout de drenaje: %s → reiniciando", sym)
                    self._detener_operacion(sym, reason="drain_timeout")
                    self._wakeup_event.set()

    # ─────────────────────────────────────────────────────────────────────────
    # Daemons de soporte
    # ─────────────────────────────────────────────────────────────────────────

    def _iniciar_ia_worker(self) -> None:
        if not self.config.ia.enabled:
            logger.info("🤖 IA Optimizer desactivado (AI_OPTIMIZER_ENABLED=false)")
            return

        self._ia_worker = IAOptimizerWorker(
            ia_config       = self.config.ia,
            get_metrics_fn  = self.db.get_trading_metrics,
            on_overrides_fn = self._on_nuevos_overrides,
        )
        self._ia_worker.start()
        logger.info("🤖 IA Optimizer iniciado")

    def _iniciar_watchdog(self) -> None:
        def get_ticker(symbol: str) -> float:
            try:
                t = self.provider.fetch_tickers([symbol])
                return float(t.get(symbol, {}).get("last", 0.0))
            except Exception:
                return 0.0

        def on_ws_frozen(symbol: str) -> None:
            with self._engines_lock:
                engine = self._engines.get(symbol)
            if engine:
                engine.ws_reconectar = True

        def on_price_drift(symbol: str, precio_rest: float) -> None:
            with self._engines_lock:
                engine = self._engines.get(symbol)
            if engine:
                engine.procesar_precio_externo(precio_rest)

        def on_position_drift(symbol: str, posicion_real: Any) -> None:
            with self._engines_lock:
                engine = self._engines.get(symbol)
            if engine:
                engine.forzar_sincronizacion(posicion_real)

        self._watchdog = WatchdogREST(
            config            = self.config,
            get_engines_fn    = lambda: dict(self._engines),
            get_positions_fn  = self.provider.get_open_positions,
            get_ticker_fn     = get_ticker,
            on_ws_frozen      = on_ws_frozen,
            on_price_drift    = on_price_drift,
            on_position_drift = on_position_drift,
        )
        self._watchdog.start()
        logger.info("🐕 Watchdog iniciado")

    def _iniciar_ws_privado(self) -> None:
        """
        WS privado de OKX para fills en tiempo real.
        Implementación simplificada: usar okx_ws_client.OKXPrivateWS de V1 si disponible.
        """
        try:
            from core.okx_ws_client import OKXPrivateWS  # type: ignore
            creds = {
                "api_key":    self.config.exchange.okx_api_key,
                "api_secret": self.config.exchange.okx_api_secret,
                "passphrase": self.config.exchange.okx_passphrase,
                "is_demo":    self.config.exchange.is_demo,
            }
            self._ws_private = OKXPrivateWS(creds=creds, on_fill=self.manejar_fill)
            self._ws_private.start()
            logger.info("🔒 WebSocket privado iniciado")
        except ImportError:
            logger.warning("⚠️ okx_ws_client no disponible — fills solo via REST (Watchdog)")

    def _iniciar_webhook_worker(self) -> None:
        def worker():
            while not self._stop_event.is_set():
                try:
                    payload = self._webhook_queue.get(timeout=2)
                    self._enviar_webhook(payload)
                except queue.Empty:
                    pass

        hilo = threading.Thread(target=worker, daemon=True, name="WebhookWorker")
        hilo.start()

    def _encolar_webhook(
        self, symbol: str, precio: float, engine: GridEngine
    ) -> None:
        """Encola el payload del webhook sin bloquear el loop operativo."""
        if not self.config.webhook.url:
            return
        try:
            payload = {
                "symbol":       symbol,
                "precio":       precio,
                "posicion":     engine.posicion_neta,
                "precio_prom":  engine.precio_promedio,
                "espaciado":    engine.espaciado_actual,
                "num_niveles":  len(engine.niveles),
                "modo":         engine.modo_estrategia.value,
                "kill_switch":  engine.kill_switch_activado,
                "ts":           time.time(),
            }
            if not self._webhook_queue.full():
                self._webhook_queue.put_nowait(payload)
        except Exception:
            pass

    def _enviar_webhook(self, payload: Dict[str, Any]) -> None:
        try:
            requests.post(
                self.config.webhook.url,
                json=payload,
                timeout=self.config.webhook.timeout_seconds,
            )
            self.db.save_grid_status_cache(payload)
        except Exception as exc:
            logger.debug("Webhook error: %s", exc)

    # ─────────────────────────────────────────────────────────────────────────
    # Helpers de conversión
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _symbol_from_inst_id(inst_id: str) -> str:
        """Convierte 'BTC-USDT-SWAP' → 'BTC/USDT:USDT'."""
        if not inst_id or not inst_id.endswith("-SWAP"):
            return ""
        partes = inst_id.replace("-SWAP", "").split("-")
        if len(partes) == 2:
            return f"{partes[0]}/{partes[1]}:{partes[1]}"
        return ""

    @staticmethod
    def _parse_grid_level(cl_ord_id: str) -> int:
        """Parsea 'glvl5xTSxI' → 5, 'glvlm3xTSxI' → -3."""
        if not cl_ord_id or not cl_ord_id.startswith("glvl"):
            return 0
        try:
            level_str = cl_ord_id.split("x", 1)[0].replace("glvl", "")
            return int(level_str.replace("m", "-"))
        except (ValueError, IndexError):
            return 0
