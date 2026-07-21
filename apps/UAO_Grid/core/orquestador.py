"""
orquestador.py — Director de la UAO_Grid.
Maneja el bucle principal, Watchdog, WebSocket y el ciclo de rotación.
"""
import logging
import os
import time
import json
import ssl
import threading
import queue
import requests
import pandas as pd
import ccxt
import websocket
import datetime
from core.database import Database
from core.providers import ExecutionProvider, OKXDemoAdapter, OKXRealAdapter
from core.engine import GridEngine
from core.analizador import analizar_lote
from core.okx_connector import obtener_futuros_usdt, filtrar_por_volumen
from core.ai_optimizer import AIOptimizerWorker
from core.okx_ws_client import OKXPrivateWS
from core.backtester import backtest_grid_top, _backtest_grid_simbolo

logger = logging.getLogger("UAO_Sclaping.GridOrquestador")


class WatchdogREST(threading.Thread):
    """
    Edge Case 7: Watchdog para detectar WebSocket congelado
    y desincronización de estado en RAM vs Exchange.
    """
    def __init__(self, provider: ExecutionProvider, engines: dict, exchange: ccxt.Exchange):
        super().__init__(daemon=True)
        self.provider = provider
        self.engines = engines
        self.exchange = exchange
        
        self.intervalo = int(os.getenv("WATCHDOG_INTERVAL_SECONDS", 120))
        self.timeout_ws = int(os.getenv("WATCHDOG_WS_TIMEOUT_SECONDS", 60))
        self.drift_pct = float(os.getenv("WATCHDOG_PRICE_DRIFT_PCT", 3))
        
        self.ultimos_precios_ws = {}
        self.ultimos_ticks_ts = {}
        self._lock = threading.Lock()

    def actualizar_precio_ws(self, symbol: str, precio: float):
        with self._lock:
            self.ultimos_precios_ws[symbol] = precio
            self.ultimos_ticks_ts[symbol] = time.time()

    def run(self):
        while True:
            time.sleep(self.intervalo)
            try:
                self._verificar()
            except Exception as e:
                logger.error(f"🐕 Watchdog error: {e}")

    def _verificar(self):
        if not self.engines:
            return
            
        now = time.time()
        
        # 1. Detectar WS congelado por engine
        with self._lock:
            for symbol, ts in list(self.ultimos_ticks_ts.items()):
                engine = self.engines.get(symbol)
                if engine and (now - ts) > self.timeout_ws:
                    logger.critical(f"🚨 WATCHDOG: Sin tick de WS por {(now - ts):.0f}s para {symbol}. WS congelado!")
                    engine.ws_reconectar = True

        # 2. Desincronización RAM vs REST (solo en modo Real)
        if self.provider.mode in {"real", "demo"}:
            for symbol, engine in list(self.engines.items()):
                if not engine.current_symbol:
                    continue
                try:
                    ticker = self.exchange.fetch_ticker(symbol)
                    precio_rest = float(ticker["last"])
                    
                    with self._lock:
                        precio_ram = self.ultimos_precios_ws.get(symbol, 0.0)
                        
                    if precio_ram > 0 and abs(precio_rest - precio_ram) / precio_ram > (self.drift_pct / 100.0):
                        logger.warning(f"🐕 WATCHDOG: Desincronización en {symbol}! RAM={precio_ram:.4f} vs REST={precio_rest:.4f}")
                        engine.procesar_precio_externo(precio_rest)

                    # 3. Posiciones reales vs RAM
                    pos_real = self.provider.get_open_positions(symbol)
                    pos_ram = getattr(engine, 'posicion_neta', 0.0)
                    
                    real_qty = pos_real[0].qty if pos_real else 0.0
                    
                    if abs(real_qty - abs(pos_ram)) > 1e-6:
                        logger.warning(f"🐕 WATCHDOG: Posición desincronizada en {symbol}! RAM={pos_ram} vs OKX={real_qty}")
                        if pos_real:
                            engine.forzar_sincronizacion(pos_real[0])
                        else:
                            engine.posicion_neta = 0.0
                            engine.precio_promedio = 0.0
                except Exception as e:
                    logger.error(f"🐕 Watchdog error revisando API REST para {symbol}: {e}")


class GridOrquestador:
    def __init__(self, mode: str, exchange: ccxt.Exchange):
        self.mode = mode.lower()
        self.exchange = exchange
        self.db = Database()
        
        capital = float(os.getenv("GRID_CAPITAL_POR_OPERACION", 50))
        leverage = float(os.getenv("GRID_LEVERAGE", 15.0))
        
        overrides = self.db.get_config_overrides()
        if "GRID_CAPITAL_POR_OPERACION" in overrides:
            capital = float(overrides["GRID_CAPITAL_POR_OPERACION"])
        if "GRID_LEVERAGE" in overrides:
            leverage = float(overrides["GRID_LEVERAGE"])
        
        # ── INYECCIÓN DE ADAPTADORES ──
        if self.mode == "real":
            self.provider: ExecutionProvider = OKXRealAdapter(exchange=self.exchange)
        else:
            self.provider: ExecutionProvider = OKXDemoAdapter(exchange=self.exchange)
        # ──────────────────────────────
        
        self.exchange = self.provider.exchange
        self.ws_private = None
        self._engine_lock = threading.RLock()
            
        self.engines = {} # Dict[str, GridEngine]
        self.engines_lock = threading.RLock()
        self.max_active_symbols = int(os.getenv("GRID_SYMBOLS_TO_TRADE", "3"))
        
        self.watchdog = WatchdogREST(self.provider, self.engines, self.exchange)
        self.ai_worker = None
        self.last_fills_history = []
        self.last_30_candles = []
        
        self.force_balance_sync = threading.Event()
        # Cola dedicada para webhooks (evita crear un hilo por tick)
        self.webhook_queue = queue.Queue()
        threading.Thread(target=self._webhook_worker, daemon=True).start()
        
        # Evento para despertar el orquestador sin esperar los 30 min
        self.wakeup_event = threading.Event()
        
        if os.getenv("AI_OPTIMIZER_ENABLED", "true").lower() == "true":
            self.ai_worker = AIOptimizerWorker(self.db)
            
        self.ciclo_segundos = int(os.getenv("SCAN_CYCLE_SECONDS", 1800))
        self.timeframe = os.getenv("SCAN_TIMEFRAME", "5m")
        self.limit = int(os.getenv("SCAN_LIMIT", 288))
        self.min_volume = float(os.getenv("SCAN_MIN_VOLUME_USDT", 1_000_000))
        self.top_n = int(os.getenv("SCAN_TOP_N", 30))
        self.scan_cycle_count = 0

    def _min_qty_mercado(self, symbol: str) -> float:
        market = self.exchange.markets.get(symbol, {}) if getattr(self.exchange, "markets", None) else {}
        try:
            return float(market.get("limits", {}).get("amount", {}).get("min") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def _filtrar_posiciones_operables(self, posiciones):
        operables = []
        for p in posiciones:
            min_qty = self._min_qty_mercado(p.symbol)
            if min_qty > 0 and p.qty < min_qty:
                logger.warning(
                    f"⚠️ Ignorando posición residual {p.symbol}: qty={p.qty} menor al mínimo operable {min_qty}."
                )
                if hasattr(self.provider, "_positions"):
                    self.provider._positions.pop(p.symbol, None)
                    self.provider.cancel_all_orders(p.symbol)
                    if hasattr(self.provider, "_force_flush"):
                        self.provider._force_flush()
                continue
            operables.append(p)
        return operables


    def _symbol_from_inst_id(self, inst_id: str) -> str:
        if not inst_id:
            return ""
        market = getattr(self.exchange, "markets_by_id", {}).get(inst_id)
        if isinstance(market, list) and market:
            return market[0].get("symbol", "")
        if isinstance(market, dict):
            return market.get("symbol", "")
        return inst_id.replace("-USDT-SWAP", "/USDT:USDT").replace("-SWAP", ":USDT").replace("-", "/", 1)

    def _parse_grid_level(self, cl_ord_id: str):
        if not cl_ord_id or "glvl" not in cl_ord_id:
            return None
        try:
            return int(cl_ord_id.split("x", 1)[0].replace("glvl", ""))
        except (ValueError, IndexError):
            return None

    def _start_private_ws(self):
        if self.ws_private or not getattr(self.provider, "has_private_credentials", False):
            if not getattr(self.provider, "has_private_credentials", False):
                logger.warning("WS privado OKX no iniciado: faltan credenciales privadas.")
            return
        creds = self.provider.credentials
        self.ws_private = OKXPrivateWS(
            creds.get("api_key", ""),
            creds.get("api_secret", ""),
            creds.get("passphrase", ""),
            getattr(self.provider, "is_demo", True),
            self._handle_real_fill,
        )
        self.ws_private.start()

    def _handle_real_fill(self, fill_data: dict):
        try:
            inst_id = fill_data.get("instId", "")
            symbol = self._symbol_from_inst_id(inst_id)
            side = str(fill_data.get("side", "")).upper()
            price = float(fill_data.get("fillPx") or fill_data.get("avgPx") or fill_data.get("px") or 0.0)
            qty = float(fill_data.get("fillSz") or fill_data.get("accFillSz") or fill_data.get("sz") or 0.0)
            if not side or price <= 0 or qty <= 0:
                return
            cl_ord_id = fill_data.get("clOrdId", "")
            grid_level = self._parse_grid_level(cl_ord_id)
            with self._engine_lock:
                engine = self.engines.get(symbol)
                if not engine:
                    logger.debug(f"Fill WS ignorado para {symbol}, no hay engine activo.")
                    return
                market_info = self.exchange.markets.get(symbol, {})
                fill_timestamp = float(fill_data.get("ts") or fill_data.get("fillTime") or time.time())
                if fill_timestamp > 10_000_000_000:
                    fill_timestamp /= 1000.0
                engine.procesar_ejecucion_simulada(
                    side, price, qty, grid_level, market_info,
                    fill_fee=float(fill_data.get("fee") or 0.0),
                    order_id=str(fill_data.get("ordId") or cl_ord_id or ""),
                    fill_timestamp=fill_timestamp,
                )
                self.db.save_grid_cycles(
                    symbol=symbol,
                    levels=engine.niveles,
                    cycles=engine.cycles_snapshot(),
                    blocked_levels=engine.blocked_levels,
                    center_price=engine.centro_grid,
                    modo_drenaje=engine.modo_drenaje,
                )
                
                # --- NUEVO: FORZAR RECONCILIACIÓN INMEDIATA ---
                logger.info(f"⚡ [WS] Triggering instant reconciliation for {symbol} TP/Grid adjustment.")
                self._ejecutar_reconciliacion_inmediata(symbol)
                # ----------------------------------------------
                
                event_ts = fill_timestamp
                
                # --- NUEVO: REGISTRO HISTÓRICO Y APRENDIZAJE ML ---
                realized_pnl = float(fill_data.get("pnl") or 0.0)
                fee = float(fill_data.get("fee") or 0.0)
                trade_id = fill_data.get("tradeId") or str(int(event_ts * 1000))
                
                self.db.record_trade(
                    trade_id=trade_id,
                    symbol=symbol,
                    side=side,
                    price=price,
                    qty=qty,
                    pnl=realized_pnl,
                    fee=fee,
                    executed_at_ts=event_ts
                )
                
                # Actualizar el desempeño empírico de la sesión actual de la IA
                self.db.update_ml_session_trade(symbol, realized_pnl - fee)
                # --------------------------------------------------
                self.last_fills_history.append({
                    "side": side,
                    "price": price,
                    "qty": qty,
                    "realized_pnl": float(fill_data.get("pnl") or 0.0),
                    "fee": float(fill_data.get("fee") or 0.0),
                    "time": int(event_ts),
                    "timestamp": event_ts,
                    "symbol": symbol,
                    "grid_level": grid_level,
                })
                self.last_fills_history = self.last_fills_history[-50:]
                self.force_balance_sync.set()
            logger.info("🎯 [WS] SEÑAL EJECUTADA: %s %s qty=%s price=%s level=%s", symbol, side, qty, price, grid_level)
            # NOTA: No llamamos wakeup_event.set() aquí porque eso forzaría un escaneo completo 
            # de todo el mercado (analizar_y_backtestear) en cada grid fill. La reconciliación de órdenes 
            # ocurre naturalmente en el siguiente tick del WS público (en _loop_operativo).
        except Exception as exc:
            logger.error("Error procesando fill WS privado: %s", exc, exc_info=True)

    def _ejecutar_reconciliacion_inmediata(self, symbol: str):
        try:
            engine = self.engines.get(symbol)
            if not engine: return
            
            # Obtenemos el precio actual del engine o un ticker rápido
            ticker = self.exchange.fetch_ticker(symbol)
            precio_actual = float(ticker['last'])
            market_info = self.exchange.markets.get(symbol, {})
            
            # Obtener lo que el engine quiere que exista
            deseadas = engine.obtener_ordenes_deseadas(precio_actual, market_info)
            
            # Obtener lo que hay realmente
            actuales = self.provider.get_open_orders(symbol)
            
            # Reconciliar (esto usa tu lógica existente de CCXT)
            self.provider.reconciliar_ordenes(deseadas, actuales)
            engine.malla_modificada = False # Resetear flag
            logger.info(f"✅ Reconciliación inmediata exitosa para {symbol}")
            
        except Exception as e:
            logger.error(f"❌ Error en reconciliación inmediata: {e}")

    def _webhook_worker(self):
        """Worker dedicado para enviar webhooks sin crear hilos por tick."""
        import urllib.request
        import time
        last_error_log = 0
        while True:
            url, data = self.webhook_queue.get()
            try:
                req = urllib.request.Request(
                    url,
                    data=json.dumps(data).encode('utf-8'),
                    headers={'Content-Type': 'application/json'}
                )
                urllib.request.urlopen(req, timeout=2)
            except Exception as e:
                # Mostrar alerta cada 60s para no hacer spam, el webhook es solo informativo
                now = time.time()
                if now - last_error_log > 60:
                    logger.warning(f"⚠️ Webhook no disponible ({e}). No afecta el funcionamiento, continuando... (silenciando este error por 60s)")
                    last_error_log = now
            finally:
                try:
                    self.db.save_grid_status_cache(data)
                except Exception as db_err:
                    logger.debug(f"Error guardando cache local del webhook: {db_err}")
                self.webhook_queue.task_done()

    def run(self):
        logger.info(f"🚀 Iniciando UAO_Grid en modo {self.mode.upper()}")
        
        # Iniciar hilos daemon
        self.watchdog.start()
        self._start_private_ws()
        if self.ai_worker:
            self.ai_worker.start()

        # Ciclo principal de orquestación
        while True:
            try:
                self._ciclo_reescaneo()
            except Exception as e:
                logger.error(f"❌ Error en ciclo re-escaneo: {e}", exc_info=True)
                
            logger.info(f"⏳ Esperando {self.ciclo_segundos}s o hasta que se libere una posición...")
            self.wakeup_event.wait(self.ciclo_segundos)
            self.wakeup_event.clear()

    def _ciclo_reescaneo(self):
        """Ciclo principal multi-motor."""
        with self.engines_lock:
            active_symbols = list(self.engines.keys())

        # --- 1. SUPERVISOR DE TIMEOUT DE DRENAJE ---
        timeout_horas = float(os.getenv("GRID_ROTATION_TIMEOUT_HOURS", 2.0))
        for symbol in active_symbols:
            engine = self.engines.get(symbol)
            if not engine or not engine.modo_drenaje:
                continue
                
            if engine.es_timeout_drenaje(timeout_horas):
                logger.warning(f"⏱️ [BOTÓN DE PÁNICO] Drenaje en {symbol} superó {timeout_horas}h. Cerrando a mercado para forzar rotación.")
                self.provider.cancel_all_orders(symbol)
                self.provider.close_position_market(symbol)
                engine.modo_drenaje = False
                engine.simbolo_destino_pendiente = None
                time.sleep(1.0)
            else:
                logger.info(f"⏳ Drenaje paciente activo en {symbol}. Esperando rentabilidad...")

        # 0. Verificación de balance
        bal = self.provider.get_balance()
        usdt_disponible = bal.get("usdt_available", 0.0)
        
        # 1. Hot-reload de IA (y configuraciones desde Telegram)
        overrides = self.db.get_config_overrides()
        if overrides:
            capital = float(overrides.get("GRID_CAPITAL_POR_OPERACION", os.getenv("GRID_CAPITAL_POR_OPERACION", 50)))
            for engine in self.engines.values():
                engine.update_params(
                    atr_mult=overrides.get("GRID_ATR_MULTIPLIER"),
                    leverage=overrides.get("GRID_LEVERAGE"),
                    num_lineas=overrides.get("GRID_NUM_LINEAS_LADO"),
                    capital_inicial=capital
                )
        else:
            capital = float(os.getenv("GRID_CAPITAL_POR_OPERACION", 50))

        # 2. Re-escanear
        nuevos_tops = self._analizar_y_backtestear()
        if not nuevos_tops:
            return

        # 3. FILTRO DE ACTIVOS CORE (Hold Mode) Y RECUPERACIÓN DE POSICIONES
        posiciones_crudas = self.provider.get_open_positions()
        posiciones_operables = self._filtrar_posiciones_operables(posiciones_crudas)
        core_assets = ["BTC/", "ETH/", "SOL/", "OKB/", "OKT/"] 
        
        for p in posiciones_operables:
            is_core = any(p.symbol.startswith(coin) for coin in core_assets)
            if is_core and p.symbol not in nuevos_tops:
                if p.symbol not in active_symbols:
                    logger.info(f"🛡️ Activo Core en Hold detectado ({p.symbol}). Manteniendo posición abierta, cancelando órdenes (Evadiendo Drenaje).")
                    self.provider.cancel_all_orders(p.symbol)
            elif not is_core:
                # Recuperar motores para operaciones que ya existen en el exchange y no están en memoria
                if p.symbol not in active_symbols:
                    logger.info(f"🔄 Recuperando motor para posición existente: {p.symbol}")
                    self._iniciar_operacion(p.symbol)
                    active_symbols.append(p.symbol)

        # 4. GESTIÓN DE ENGINES EXISTENTES
        for symbol in active_symbols:
            engine = self.engines.get(symbol)
            if not engine: continue
            
            pos_actual = next((p for p in posiciones_operables if p.symbol == symbol), None)
            
            if symbol in nuevos_tops:
                if engine.modo_drenaje:
                    engine.modo_drenaje = False
                    logger.info(f"✅ {symbol} volvió al Top. Cancelando drenaje.")
                
                # Actualizar la malla con el perfil validado del último backtest.
                try:
                    precio_vivo = self.exchange.fetch_ticker(symbol).get('last')
                except Exception:
                    precio_vivo = None
                self._inicializar_engine_con_profile(engine, symbol, precio_vivo, rebuild=True)
            else:
                # Símbolo ya no está en el Top. Decidir si mantener (momentum) o drenar
                if not pos_actual:
                    if engine.modo_drenaje:
                        logger.info(f"🚿 {symbol} terminó de drenar (posición 0). Deteniendo motor.")
                        engine.kill_switch_activado = True # Detiene el hilo
                        # El hilo se encargará de borrarse a sí mismo, pero podemos forzarlo:
                        with self.engines_lock:
                            if symbol in self.engines:
                                del self.engines[symbol]
                    else:
                        logger.info(f"🔀 {symbol} ya no está en el top y no tiene posiciones. Cerrando motor para rotar.")
                        self.provider.cancel_all_orders(symbol)
                        engine.kill_switch_activado = True
                        with self.engines_lock:
                            if symbol in self.engines:
                                del self.engines[symbol]
                    continue
                
                # Hay posición abierta y no está en top_n
                if not engine.modo_drenaje:
                    # LÓGICA MOMENTUM
                    try:
                        df_momentum = self._fetch_velas(symbol)
                        if df_momentum is not None and len(df_momentum) >= 20:
                            closes = df_momentum['close']
                            ema_fast = closes.ewm(span=5,  adjust=False).mean()
                            ema_slow = closes.ewm(span=20, adjust=False).mean()
                            hi = df_momentum['high']; lo = df_momentum['low']
                            tr = pd.concat([hi - lo, (hi - closes.shift()).abs(), (lo - closes.shift()).abs()], axis=1).max(axis=1)
                            atr_pct = tr.rolling(14).mean().iloc[-1] / closes.iloc[-1]

                            direccion_cambiada = ema_fast.iloc[-1] < ema_slow.iloc[-1] and ema_fast.iloc[-2] >= ema_slow.iloc[-2]
                            volatilidad_muerta = atr_pct < float(os.getenv("MOMENTUM_MIN_ATR_SPACING", "0.003"))

                            if direccion_cambiada or volatilidad_muerta:
                                razon = "cruce EMA" if direccion_cambiada else "ATR caído"
                                logger.info(f"⚡ [MOMENTUM FIN] {razon} en {symbol}. Activando MODO DRENAJE.")
                                engine.activar_modo_drenaje()
                            else:
                                logger.info(f"⚡ [MOMENTUM ACTIVO] EMA fast>{ema_fast.iloc[-1]:.5f} | ATR: {atr_pct*100:.3f}% — manteniendo {symbol}")
                    except Exception as e:
                        logger.error(f"❌ Error momentum {symbol}: {e}")
                        engine.activar_modo_drenaje()

        # 5. INICIAR NUEVOS MOTORES
        simbolos_con_posiciones = len(set(
            p.symbol for p in posiciones_operables 
            if not any(p.symbol.startswith(coin) for coin in core_assets)
        ))

        for symbol in nuevos_tops:
            with self.engines_lock:
                # Contamos cuántos NO están en drenaje (slots ocupados productivos)
                slots_productivos = sum(1 for e in self.engines.values() if not e.modo_drenaje)
                
            if symbol not in self.engines:
                if simbolos_con_posiciones >= self.max_active_symbols:
                    logger.info(f"⚠️ Máximo de símbolos con posiciones alcanzado ({simbolos_con_posiciones}/{self.max_active_symbols}). No se abre {symbol}.")
                    continue
                    
                if slots_productivos < self.max_active_symbols:
                    if usdt_disponible >= capital * 0.95:  # 95% margen de error por comisiones/rounding
                        logger.info(f"✨ Iniciando nuevo motor para TOP: {symbol} (Slots {slots_productivos+1}/{self.max_active_symbols})")
                        self._iniciar_operacion(symbol)
                        usdt_disponible -= capital # Reservamos capital lógicamente
                        simbolos_con_posiciones += 1 # Aumentar preventivamente el contador para el siguiente ciclo
                    else:
                        logger.warning(f"⚠️ Sin balance para iniciar {symbol}. Disponible: {usdt_disponible:.2f} < Requerido: {capital:.2f}")
                else:
                    # Todos los slots productivos están ocupados
                    pass

    def _fetch_velas(self, symbol: str, limit: int = 200) -> pd.DataFrame:
        """Obtiene velas del mercado a través de CCXT y las convierte a DataFrame."""
        try:
            velas = self.exchange.fetch_ohlcv(symbol, self.timeframe, limit=limit)
            df = pd.DataFrame(velas, columns=["timestamp", "open", "high", "low", "close", "volume"])
            return df
        except Exception as e:
            logger.error(f"❌ Error fetching velas for {symbol}: {e}")
            return pd.DataFrame()

    def _profile_para_symbol(self, symbol: str):
        return getattr(self, "mejores_params", {}).get(symbol)

    def _inicializar_engine_con_profile(self, engine: GridEngine, symbol: str, precio: float, rebuild: bool = True) -> bool:
        profile = self._profile_para_symbol(symbol)
        if not profile:
            logger.warning("⚠️ No hay ValidatedOptimizationProfile para %s; no se inicializa malla.", symbol)
            return False
        if not precio or float(precio) <= 0:
            try:
                precio = float(self.exchange.fetch_ticker(symbol).get("last") or 0.0)
            except Exception:
                precio = 0.0
        if precio <= 0:
            logger.warning("⚠️ Precio inválido para %s; no se inicializa malla.", symbol)
            return False

        engine.optimization_profile = profile
        engine.espaciado_actual = float(profile.optimization.get("grid_spacing_pct", 0.0))
        engine.capital_inicial = float(profile.optimization.get("capital", engine.capital_inicial))
        engine.leverage = float(profile.optimization.get("leverage", engine.leverage))
        engine.modo_estrategia = str(profile.optimization.get("preferred_mode", "NEUTRAL")).upper()
        engine.num_grids_optimo = int(profile.optimization.get("grid_lines", engine.num_grids_optimo))
        engine.num_lineas_lado = max(1, engine.num_grids_optimo // 2)

        if rebuild:
            engine.inicializar_grid(profile, float(precio))
        return True

    def _iniciar_operacion(self, symbol: str):
        capital = float(os.getenv("GRID_CAPITAL_POR_OPERACION", 50))
        leverage = float(os.getenv("GRID_LEVERAGE", 15.0))
        
        with self.engines_lock:
            if symbol not in self.engines:
                self.engines[symbol] = GridEngine(symbol=symbol, capital_inicial=capital, leverage=leverage)
            engine = self.engines[symbol]

        engine.reset()
        engine.current_symbol = symbol

        persisted_grid = self.db.load_grid_state(symbol)
        if persisted_grid:
            engine.restore_cycles(
                persisted_grid.get("cycles", {}),
                persisted_grid.get("blocked_levels", []),
            )
            engine.niveles = persisted_grid.get("levels", [])
            engine.centro_grid = float(persisted_grid.get("center_price") or 0.0)
            engine.modo_drenaje = bool(persisted_grid.get("modo_drenaje", False))
            engine.malla_modificada = True
            logger.info(
                "🔁 [CICLOS] Estado recuperado para %s: %d ciclos, %d niveles bloqueados.",
                symbol, len(engine.grid_cycles), len(engine.blocked_levels),
            )
        
        # --- NUEVO: APLICAR EL APALANCAMIENTO DINÁMICO E INICIAR SESIÓN ML ---
        leverage_optimo = engine.leverage # Fallback por defecto
        if hasattr(self, 'mejores_params') and symbol in self.mejores_params:
            params_symbol = self.mejores_params[symbol]
            leverage_optimo = params_symbol.get("apalancamiento", engine.leverage)
            engine.leverage = leverage_optimo # Actualizar el engine
            engine.num_grids_optimo = params_symbol.get("num_grids", 6)
            engine.espaciado_optimo = params_symbol.get("espaciado_pct")
            engine.modo_estrategia = str(params_symbol.get("modo", "NEUTRAL")).upper()
            if params_symbol.get("capital_por_linea") and params_symbol.get("num_grids"):
                engine.capital_inicial = float(params_symbol["capital_por_linea"]) * int(params_symbol["num_grids"])
            
            # Iniciar sesión de aprendizaje para ML
            session_id = f"{symbol}_{int(time.time())}"
            self.db.create_ml_session(
                session_id=session_id,
                symbol=symbol,
                analyzer_metrics=params_symbol.get("analisis_original", {}),
                math_params=params_symbol.get("params_optimos", {}),
                ai_factors=params_symbol.get("ai_overrides", {}),
                final_params=params_symbol.get("params_optimos", {}),
                backtest_metrics={
                    "pnl_neto": params_symbol.get("pnl_neto", 0.0),
                    "roi_pct": params_symbol.get("roi_pct", 0.0),
                    "drawdown": params_symbol.get("drawdown", 0.0),
                    "win_rate": params_symbol.get("win_rate", 0.0),
                    "profit_factor": params_symbol.get("profit_factor", 0.0),
                    "operaciones": params_symbol.get("operaciones", 0),
                },
                setup_source=params_symbol.get("source", "UNKNOWN"),
            )
            logger.info(f"🧠 [ML] Nueva sesión de aprendizaje registrada para {symbol} (ID: {session_id})")
            
        # Ajustar apalancamiento en OKX antes de operar
        if hasattr(self.provider, "set_leverage"):
            self.provider.set_leverage(leverage_optimo, symbol)
        # -------------------------------------------------
        
        # Recuperar estado si había
        pos_real = self._filtrar_posiciones_operables(self.provider.get_open_positions(symbol))
        if pos_real:
            engine.forzar_sincronizacion(pos_real[0])
            
        df_5m = self._fetch_velas(symbol)
        if not df_5m.empty:
            # Preparar velas para TV: time (segundos), open, high, low, close
            df_tv = df_5m.copy()
            df_tv['time'] = df_tv['timestamp'] // 1000
            self.last_30_candles = df_tv[['time', 'open', 'high', 'low', 'close', 'volume']].tail(200).to_dict(orient="records")
        else:
            self.last_30_candles = []
        abiertas = self.provider.get_open_orders(symbol)
        if abiertas:
            persisted_levels = list(engine.niveles)
            engine.niveles = []
            for o in abiertas:
                level = getattr(o, "grid_level", 1)
                metadata = next((n for n in persisted_levels if n.get("level") == level and n.get("side") == o.side), {})
                engine.niveles.append({
                    "side": o.side,
                    "price": o.price,
                    "qty": o.qty,
                    "level": level,
                    **{key: metadata[key] for key in ("cycle_id", "base_level", "base_price", "precio_original_entrada", "source_fill_price") if key in metadata},
                })
            if not df_5m.empty:
                engine.centro_grid = df_5m['close'].iloc[-1]
            logger.info(f"🔄 [ESTADO] Recuperados {len(engine.niveles)} niveles de órdenes abiertas para {symbol}.")
        else:
            self.provider.cancel_all_orders(symbol)
            if persisted_grid and engine.niveles:
                engine.malla_modificada = True
            
        try:
            precio_vivo = self.exchange.fetch_ticker(symbol).get('last')
        except:
            precio_vivo = None
            
        self._inicializar_engine_con_profile(engine, symbol, precio_vivo, rebuild=not bool(abiertas))
        
        # Si no hay hilo WS para este symbol, lo levantamos
        t = threading.Thread(target=self._loop_operativo, args=(symbol,), daemon=True)
        t.start()

    def _loop_operativo(self, symbol: str):
        """Mantiene el WebSocket para el symbol y evalúa en cada tick."""
        engine = self.engines.get(symbol)
        if not engine:
            return
            
        inst_id = symbol.replace("/", "-").replace(":USDT", "-SWAP")
        ws_url = "wss://wspap.okx.com:8443/ws/v5/public?brokerId=9999" if getattr(self, "mode", "demo").lower() == "demo" else "wss://ws.okx.com:8443/ws/v5/public"
        
        last_webhook_time = 0
        webhook_url = os.getenv("WEBHOOK_URL", "http://host.docker.internal:4000/api/webhook/grid")
        import urllib.request
        
        # === VARIABLES DE RASTREO PARA RESPIRACIÓN CONDICIONADA ===
        last_trade_time = int(time.time())
        last_pos_net = getattr(engine, 'posicion_neta', 0.0)
        
        tf = getattr(self, 'timeframe', '5m')
        tf_mins = int(tf[:-1]) if tf.endswith('m') else int(tf[:-1])*60 if tf.endswith('h') else 5
        tres_velas_sec = 3 * tf_mins * 60
        
        while not getattr(engine, 'kill_switch_activado', False) and engine.current_symbol == symbol:
            engine.ws_reconectar = False
            try:
                ws = websocket.create_connection(ws_url, sslopt={"cert_reqs": ssl.CERT_NONE}, timeout=20)
                ws.send(json.dumps({"op": "subscribe", "args": [{"channel": "tickers", "instId": inst_id}]}))
                logger.info(f"🔌 Conectado WS a {inst_id}")

                while not engine.ws_reconectar and not getattr(engine, 'kill_switch_activado', False) and engine.current_symbol == symbol:
                    try:
                        raw = ws.recv()
                    except websocket.WebSocketTimeoutException:
                        ws.send("ping")
                        continue
                        
                    if not raw:
                        continue
                    if raw == "pong":
                        continue
                        
                    try:
                        msg = json.loads(raw)
                    except Exception as e:
                        logger.error(f"🔌 Error WS {inst_id}: {e}")
                        continue
                        
                    if "data" not in msg: continue
                    
                    # Usar el último precio transaccionado ('last') para evitar distorsiones por spreads anchos
                    precio_actual = float(msg["data"][0]["last"])
                    
                    self.watchdog.actualizar_precio_ws(symbol, precio_actual)

                    # === SEGUIMIENTO DE OPERACIONES Y POSICIONES ===
                    pos_actual = getattr(engine, 'posicion_neta', 0.0)
                    if abs(pos_actual - last_pos_net) > 1e-9:
                        last_trade_time = int(time.time())
                        
                    posicion_cerrada = (abs(pos_actual) < 1e-9 and abs(last_pos_net) >= 1e-9)
                    last_pos_net = pos_actual

                    # 0. Actualizar vela en vivo (y Rollover)
                    if hasattr(self, 'last_30_candles') and self.last_30_candles:
                        last_candle = self.last_30_candles[-1]
                        now_sec = int(time.time())
                        vela_inicio = last_candle['time']
                        
                        if precio_actual > last_candle['high']: last_candle['high'] = precio_actual
                        if precio_actual < last_candle['low']:  last_candle['low'] = precio_actual
                        last_candle['close'] = precio_actual
                        
                        if now_sec >= vela_inicio + (tf_mins * 60):
                            self.last_30_candles.append({
                                'time': vela_inicio + (tf_mins * 60),
                                'open': precio_actual, 'high': precio_actual,
                                'low': precio_actual, 'close': precio_actual, 'volume': 0
                            })
                            if len(self.last_30_candles) > 200:
                                self.last_30_candles.pop(0)

                    # 1. Ejecución de la estrategia a través del motor Grid
                    engine.procesar_precio_externo(precio_actual)
                    
                    # === NUEVO: RESPIRACIÓN EN VIVO (CONDICIONADA) ===
                    now_sec = int(time.time())
                    
                    # === NUEVO: LIMPIEZA DE ÓRDENES ESTANCADAS CADA 15 MIN ===
                    if not hasattr(engine, 'last_stagnation_check'):
                        engine.last_stagnation_check = now_sec
                    
                    if now_sec - engine.last_stagnation_check >= 900:  # 15 minutos
                        engine.last_stagnation_check = now_sec
                        # Si no ha habido trades en los últimos 15 mins
                        if now_sec - last_trade_time >= 900:
                            try:
                                actuales = self.provider.get_open_orders(symbol)
                                time_15m_ago_ms = (now_sec - 900) * 1000
                                # Verificar si existen órdenes creadas hace 15 min o más
                                ordenes_viejas = [o for o in actuales if o.get('timestamp') and o['timestamp'] <= time_15m_ago_ms]
                                
                                if ordenes_viejas:
                                    logger.warning(f"🧹 [ESTANCAMIENTO] {symbol} lleva 15 min sin ejecutar y con órdenes viejas. Cancelando y cerrando grid.")
                                    try:
                                        self.exchange.cancel_all_orders(symbol)
                                    except Exception as e:
                                        logger.error(f"Error cancelando órdenes de {symbol}: {e}")
                                    engine.reset()
                                    engine.kill_switch_activado = True
                                    self.wakeup_event.set()
                                    break
                            except Exception as e:
                                logger.error(f"Error verificando estancamiento en {symbol}: {e}")
                    
                    # Respirar si pasaron 3 velas sin operaciones (modificado: ya no forzamos por posición cerrada)
                    condicion_respiracion = (now_sec - last_trade_time >= tres_velas_sec)
                    
                    if condicion_respiracion:
                        if posicion_cerrada or (now_sec - getattr(self, 'last_atr_calc_time', 0) >= 30):
                            try:
                                self._inicializar_engine_con_profile(
                                    engine,
                                    symbol,
                                    precio_actual,
                                    rebuild=abs(engine.posicion_neta) < 1e-9,
                                )
                                self.last_atr_calc_time = now_sec
                                # Si hubo respiración, reajustar last_trade_time para que no spamee
                                last_trade_time = now_sec
                            except Exception as e:
                                logger.error(f"Error refrescando perfil de grid en vivo para {symbol}: {e}")
                    # =================================================
                    
                    if engine.malla_necesita_reajuste(precio_actual) and not engine.modo_drenaje:
                        if abs(engine.posicion_neta) > 1e-9:
                            if engine.chequear_breakout_malla(precio_actual):
                                logger.warning(f"🚨 [BREAKOUT] Precio fuera de límites en {symbol}. Recalculando Malla.")
                                try:
                                    self._inicializar_engine_con_profile(engine, symbol, precio_actual, rebuild=True)
                                except Exception as e:
                                    logger.error(f"Error recargando perfil tras breakout en {symbol}: {e}")
                            # else: 
                            # Si es solo un llenado intermedio, NO recalculamos. Mantenemos el TP estático.
                        else:
                            # Sin inventario, podemos centrar la malla libremente
                            logger.info(f"🎯 [GRID] Posición plana en {symbol}, centrando malla nueva.")
                            self._inicializar_engine_con_profile(engine, symbol, precio_actual, rebuild=True)
                    
                    if engine.malla_modificada:
                        with self._engine_lock:
                            market_info = self.exchange.markets.get(symbol, {})
                            deseadas = engine.obtener_ordenes_deseadas(precio_actual, market_info)
                            
                            actuales = self.provider.get_open_orders(symbol)
                            
                            try:
                                self.provider.reconciliar_ordenes(deseadas, actuales)
                                engine.malla_modificada = False
                                self.force_balance_sync.set()  # Forzar actualización de UI con nuevos datos del exchange
                            except Exception as e:
                                if "51155" in str(e):
                                    logger.critical(f"🛑 Símbolo {symbol} restringido por OKX (51155). Bloqueando permanentemente.")
                                    self.db.agregar_a_lista_negra(symbol, self.mode)
                                    engine.reset()
                                    engine.kill_switch_activado = True
                                    self.wakeup_event.set()
                                    break
                                raise
                        
                    # 5. Enviar webhook al frontend — datos 100% en vivo desde WS privado OKX
                    now = time.time()
                    if now - last_webhook_time > 1.0:
                        last_webhook_time = now
                        try:
                            # Delegar la tarea pesada al worker
                            self.webhook_queue.put((webhook_url, self._generar_payload_webhook(precio_actual, engine)))
                        except Exception as e:
                            logger.debug(f"Cola webhook llena o error en {symbol}: {e}")
                            
            except Exception as e:
                logger.error(f"🔴 WS caido para {symbol}: {e}. Reconectando en 5s...")
                time.sleep(5)
                
        # Fuera del while principal (kill_switch activado o cambio de symbol)
        logger.info(f"🛑 Hilo WS para {symbol} terminado.")
        with self.engines_lock:
            if symbol in self.engines:
                del self.engines[symbol]

    def _generar_payload_webhook(self, precio_actual: float, engine: GridEngine) -> dict:
        symbol = engine.current_symbol
        contract_size = float(self.exchange.markets.get(symbol, {}).get("contractSize", 1.0))
        ws_ok = self.ws_private is not None

        # ── Balance: WS privado (account channel) ─────────────────────────
        now = time.time()
        if ws_ok and self.ws_private.has_live_balance():
            bal = self.ws_private.get_live_balance()
        else:
            must_sync = self.force_balance_sync.is_set() or now - getattr(self, '_last_rest_bal', 0.0) > 30.0
            if must_sync:
                self.cached_bal = self.provider.get_balance()
                self._last_rest_bal = now
                self.force_balance_sync.clear()
            bal = getattr(self, 'cached_bal', {
                "usdt_total":     getattr(engine, 'capital_inicial', 0),
                "usdt_available": getattr(engine, 'capital_inicial', 0),
            })

        # ── Posiciones: WS privado (positions channel) ─────────────────────
        if ws_ok and self.ws_private.has_live_positions():
            ws_positions = self.ws_private.get_live_positions()
            expected_inst = symbol.replace("/", "-").replace(":USDT", "-SWAP")
            positions_ui = []
            net_qty_ws   = 0.0
            avg_price_ws = 0.0
            pnl_ws       = 0.0
            for p in ws_positions:
                if p["symbol"] != expected_inst and p["symbol"] != symbol:
                    continue
                positions_ui.append({
                    "symbol":      symbol,
                    "side":        p["side"],
                    "qty":         p["qty"],
                    "entry_price": p["entry_price"],
                    "pnl":         p["pnl"],
                })
                if p["side"] == "LONG":
                    net_qty_ws   += p["qty"]
                else:
                    net_qty_ws   -= p["qty"]
                avg_price_ws = p["entry_price"]
                pnl_ws      += p["pnl"]
                
            engine.posicion_neta = net_qty_ws
            engine.precio_promedio = avg_price_ws
            
            # Sincronización en memoria de estado UI
            pos_act = {
                "qty": abs(net_qty_ws),
                "side": "LONG" if net_qty_ws > 0 else ("SHORT" if net_qty_ws < 0 else "FLAT"),
                "entry_price": avg_price_ws,
                "pnl": pnl_ws
            }
        else:
            # Fallback REST/memoria
            pnl_ram = 0.0
            if abs(engine.posicion_neta) > 1e-9:
                if engine.posicion_neta > 0:
                    pnl_ram = (precio_actual - engine.precio_promedio) * engine.posicion_neta * contract_size
                else:
                    pnl_ram = (engine.precio_promedio - precio_actual) * abs(engine.posicion_neta) * contract_size
                    
            pos_act = {
                "qty": abs(engine.posicion_neta),
                "side": "LONG" if engine.posicion_neta > 0 else ("SHORT" if engine.posicion_neta < 0 else "FLAT"),
                "entry_price": engine.precio_promedio,
                "pnl": pnl_ram
            }
            positions_ui = [pos_act] if pos_act["qty"] > 0 else []

        # ── Órdenes: WS privado (orders channel) ───────────────────────────
        if ws_ok and self.ws_private.has_live_orders():
            ws_orders = self.ws_private.get_live_orders()
            expected_inst = symbol.replace("/", "-").replace(":USDT", "-SWAP")
            open_orders_ui = []
            for o in ws_orders:
                if o["symbol"] != expected_inst and o["symbol"] != symbol:
                    continue
                open_orders_ui.append({
                    "id":    o["order_id"],
                    "side":  o["side"],
                    "price": o["price"],
                    "qty":   o["qty"]
                })
        else:
            # Fallback REST/memoria
            open_orders_ui = []
            for n in engine.niveles:
                open_orders_ui.append({
                    "id": f"virtual_{n['level']}",
                    "side": n["side"],
                    "price": n["price"],
                    "qty": n["qty"]
                })

        # ── Historial Fills: WS privado ─────────────────────────────────────
        if ws_ok and self.ws_private.has_live_orders(): # fills usa list interna
            ws_fills = self.ws_private.get_live_fills()
            expected_inst = symbol.replace("/", "-").replace(":USDT", "-SWAP")
            fills_ui = []
            for f in ws_fills:
                if f["symbol"] != expected_inst and f["symbol"] != symbol:
                    continue
                fills_ui.append({
                    "time": f["time"],
                    "side": f["side"],
                    "price": f["price"],
                    "qty": f["qty"],
                    "realized_pnl": f["realized_pnl"],
                    "fee": getattr(f, 'fee', 0)
                })
            self.last_fills_history = fills_ui[:50]
            
        data = {
            "timestamp": int(time.time() * 1000),
            "symbol": symbol,
            "mode": self.mode.upper(),
            "status": "DRENAJE (Esperando Cierre)" if engine.modo_drenaje else "OPERANDO (Grid Activo)",
            "bot_active": True,
            "current_price": precio_actual,
            "position": pos_act,
            "balance": bal,
            "open_orders": open_orders_ui,
            "recent_fills": self.last_fills_history,
            "candles_30": getattr(self, 'last_30_candles', [])
        }
        return data

    def _analizar_y_backtestear(self) -> list[str]:
        """
        Flujo completo: 
        1. Analizador filtra los símbolos para operar.
        2. Optimizador matemático calcula la configuración inicial por cada símbolo analizado.
        3. Se ejecuta el backtest base para todos los símbolos candidatos.
        4. Se seleccionan los mejores resultados finales rentables listos para operar.
        """
        from core.backtester import backtest_grid_top, _backtest_grid_simbolo
        
        logger.info("  [1/6] Analizando mercado y filtrando símbolos operables...")
        simbolos_activos = obtener_futuros_usdt(self.exchange)
        simbolos_operables = filtrar_por_volumen(self.exchange, simbolos_activos, self.min_volume)
        
        if not simbolos_operables:
            logger.warning("  No hay símbolos que cumplan con el volumen mínimo.")
            return []

        # 1. Analizador filtra los símbolos
        df_analisis = analizar_lote(self.exchange, simbolos_operables, timeframe=self.timeframe, limit=self.limit)
        if df_analisis.empty:
            return []

        analisis_candidatos = df_analisis.to_dict("records")
        capital_inicial = float(os.getenv("GRID_CAPITAL_POR_OPERACION", 50.0))

        logger.info(
            "  [2 & 3] Ejecutando optimizador matemático y Backtest base para %d símbolos analizados...",
            len(analisis_candidatos),
        )
        # Backtest base utilizando las métricas analizadas para todos los símbolos candidatos.
        resultados_backtest = backtest_grid_top(self.exchange, analisis_candidatos, capital=capital_inicial)
        if not resultados_backtest:
            return []

        # Seleccionar los mejores después de que todos pasaron por optimizador + backtest.
        ranking_inicial = resultados_backtest[:self.top_n]
        
        resultados_finales = ranking_inicial

        # Ordenar los resultados finales de mayor a menor PnL
        resultados_finales.sort(key=lambda x: x.backtest.get("PnL", -999.0), reverse=True)
        
        # Seleccionar el Top 3 final listo para operar en vivo/demo
        top_3_resultados = resultados_finales[:3]
        self.mejores_params = {res.symbol: res for res in top_3_resultados}
        self.scan_cycle_count += 1
        ranking_serializable = [res.to_legacy_dict() for res in top_3_resultados]
        self.db.save_scanner_state(ranking_serializable, self.scan_cycle_count)
        
        # Enviar Top 10 al webhook de backtest para actualizar el UI
        webhook_grid_url = os.getenv("WEBHOOK_URL", "http://host.docker.internal:4000/api/webhook/grid")
        if webhook_grid_url:
            webhook_backtest_url = webhook_grid_url.replace("/webhook/grid", "/webhook/backtest")
            try:
                top_10_resultados = resultados_finales[:10]
                ranking_top_10 = [res.to_legacy_dict() for res in top_10_resultados]
                self.webhook_queue.put((webhook_backtest_url, ranking_top_10))
            except Exception as e:
                logger.error(f"Error encolando webhook de backtest: {e}")

        top_3_symbols = [res.symbol for res in top_3_resultados]
        
        logger.info(f"🚀 [4/4] Símbolos seleccionados y validados para iniciar operación: {top_3_symbols}")
        return top_3_symbols
