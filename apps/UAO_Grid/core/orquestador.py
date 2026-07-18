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
import pandas as pd
import ccxt
import websocket
import datetime
from core.database import Database
from core.providers import ExecutionProvider, OKXDemoAdapter, OKXRealAdapter
from core.engine import GridEngine
from core.analizador import analizar_lote
from core.backtester import backtest_grid_top
from core.okx_connector import obtener_futuros_usdt, filtrar_por_volumen
from core.ai_optimizer import AIOptimizerWorker
from core.okx_ws_client import OKXPrivateWS

logger = logging.getLogger("UAO_Sclaping.GridOrquestador")


class WatchdogREST(threading.Thread):
    """
    Edge Case 7: Watchdog para detectar WebSocket congelado
    y desincronización de estado en RAM vs Exchange.
    """
    def __init__(self, provider: ExecutionProvider, engine: GridEngine, exchange: ccxt.Exchange):
        super().__init__(daemon=True)
        self.provider = provider
        self.engine = engine
        self.exchange = exchange
        
        self.intervalo = int(os.getenv("WATCHDOG_INTERVAL_SECONDS", 120))
        self.timeout_ws = int(os.getenv("WATCHDOG_WS_TIMEOUT_SECONDS", 60))
        self.drift_pct = float(os.getenv("WATCHDOG_PRICE_DRIFT_PCT", 1.0))
        
        self.ultimo_precio_ws = 0.0
        self.ultimo_tick_ts = time.time()
        self._lock = threading.Lock()

    def actualizar_precio_ws(self, precio: float):
        with self._lock:
            self.ultimo_precio_ws = precio
            self.ultimo_tick_ts = time.time()

    def run(self):
        while True:
            time.sleep(self.intervalo)
            try:
                self._verificar()
            except Exception as e:
                logger.error(f"🐕 Watchdog error: {e}")

    def _verificar(self):
        with self._lock:
            segundos_sin_tick = time.time() - self.ultimo_tick_ts
            precio_ram = self.ultimo_precio_ws

        # 1. Detectar WS congelado
        if segundos_sin_tick > self.timeout_ws:
            logger.critical(f"🚨 WATCHDOG: Sin tick de WS por {segundos_sin_tick:.0f}s. WS congelado!")
            self.engine.ws_reconectar = True
            return

        if not self.engine.current_symbol:
            return

        # 2. Desincronización RAM vs REST (solo en modo Real)
        if self.provider.mode in {"real", "demo"}:
            try:
                ticker = self.exchange.fetch_ticker(self.engine.current_symbol)
                precio_rest = float(ticker["last"])
                
                if precio_ram > 0 and abs(precio_rest - precio_ram) / precio_ram > (self.drift_pct / 100.0):
                    logger.warning(f"🐕 WATCHDOG: Desincronización! RAM={precio_ram:.4f} vs REST={precio_rest:.4f}")
                    self.engine.procesar_precio_externo(precio_rest)

                # 3. Posiciones reales vs RAM
                pos_real = self.provider.get_open_positions(self.engine.current_symbol)
                pos_ram = self.engine.posicion_neta
                
                real_qty = pos_real[0].qty if pos_real else 0.0
                
                if abs(real_qty - abs(pos_ram)) > 1e-6:
                    logger.warning(f"🐕 WATCHDOG: Posición desincronizada! RAM={pos_ram} vs OKX={real_qty}")
                    if pos_real:
                        self.engine.forzar_sincronizacion(pos_real[0])
                    else:
                        self.engine.posicion_neta = 0.0
                        self.engine.precio_promedio = 0.0
            except Exception as e:
                logger.error(f"🐕 Watchdog fallo API: {e}")


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
            
        self.engine = GridEngine(symbol="", capital_inicial=capital, leverage=leverage)
        self.watchdog = WatchdogREST(self.provider, self.engine, self.exchange)
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
        self.limit = int(os.getenv("SCAN_LIMIT", 200))
        self.min_volume = float(os.getenv("SCAN_MIN_VOLUME_USDT", 1_000_000.0))
        self.top_n = int(os.getenv("SCAN_TOP_N", 10))

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
            return self.engine.current_symbol
        market = getattr(self.exchange, "markets_by_id", {}).get(inst_id)
        if isinstance(market, list) and market:
            return market[0].get("symbol", self.engine.current_symbol)
        if isinstance(market, dict):
            return market.get("symbol", self.engine.current_symbol)
        return inst_id.replace("-USDT-SWAP", "/USDT:USDT").replace("-", "/", 1)

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
            getattr(self.provider, "is_demo", False),
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
                if self.engine.current_symbol and symbol != self.engine.current_symbol:
                    logger.info("Fill WS recibido para %s mientras se opera %s", symbol, self.engine.current_symbol)
                if not self.engine.current_symbol:
                    self.engine.current_symbol = symbol
                self.engine.procesar_ejecucion_simulada(side, price, qty, grid_level)
                
                # --- NUEVO: FORZAR RECONCILIACIÓN INMEDIATA ---
                logger.info("⚡ [WS] Triggering instant reconciliation for TP/Grid adjustment.")
                self._ejecutar_reconciliacion_inmediata(self.engine.current_symbol)
                # ----------------------------------------------
                event_ts = time.time()
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
            logger.info("🎯 [WS] FILL detectado %s %s qty=%s price=%s level=%s", symbol, side, qty, price, grid_level)
            # NOTA: No llamamos wakeup_event.set() aquí porque eso forzaría un escaneo completo 
            # de todo el mercado (analizar_y_backtestear) en cada grid fill. La reconciliación de órdenes 
            # ocurre naturalmente en el siguiente tick del WS público (en _loop_operativo).
        except Exception as exc:
            logger.error("Error procesando fill WS privado: %s", exc, exc_info=True)

    def _ejecutar_reconciliacion_inmediata(self, symbol: str):
        try:
            # Obtenemos el precio actual del engine o un ticker rápido
            ticker = self.exchange.fetch_ticker(symbol)
            precio_actual = float(ticker['last'])
            market_info = self.exchange.markets.get(symbol, {})
            
            # Obtener lo que el engine quiere que exista
            deseadas = self.engine.obtener_ordenes_deseadas(precio_actual, market_info)
            
            # Obtener lo que hay realmente
            actuales = self.provider.get_open_orders(symbol)
            
            # Reconciliar (esto usa tu lógica existente de CCXT)
            self.provider.reconciliar_ordenes(deseadas, actuales)
            self.engine.malla_modificada = False # Resetear flag
            logger.info(f"✅ Reconciliación inmediata exitosa para {symbol}")
            
        except Exception as e:
            logger.error(f"❌ Error en reconciliación inmediata: {e}")

    def _webhook_worker(self):
        """Worker dedicado para enviar webhooks sin crear hilos por tick."""
        import urllib.request
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
                # Mostrar alerta y continuar, el webhook es solo informativo
                logger.warning(f"⚠️ Webhook no disponible ({e}). No afecta el funcionamiento, continuando...")
            finally:
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
        """Ciclo principal cada 30 min."""
        
        # --- NUEVO: SUPERVISOR DE TIMEOUT DE DRENAJE ---
        if self.engine.modo_drenaje:
            timeout_horas = float(os.getenv("GRID_ROTATION_TIMEOUT_HOURS", 2.0))
            if self.engine.es_timeout_drenaje(timeout_horas):
                logger.warning(f"⏱️ [BOTÓN DE PÁNICO] Drenaje en {self.engine.current_symbol} superó {timeout_horas}h. Cerrando a mercado para forzar rotación.")
                
                # 1. Limpiar las órdenes "pacientes" que no se llenaron
                self.provider.cancel_all_orders(self.engine.current_symbol)
                
                # 2. Cerrar agresivamente a mercado asumiendo pérdida/breakeven
                self.provider.close_position_market(self.engine.current_symbol)
                self.engine.modo_drenaje = False

                # Pausa de seguridad para que OKX elimine la posición de su API REST.
                time.sleep(3.0)
                
                # 3. Disparar la rotación hacia el símbolo pendiente
                proximo_symbol = getattr(self.engine, 'simbolo_destino_pendiente', None)
                if proximo_symbol:
                    self.engine.simbolo_destino_pendiente = None
                    self._iniciar_operacion(proximo_symbol)
                return  # Salir del ciclo para no interferir con la rotación
            else:
                logger.info(f"⏳ Drenaje paciente activo en {self.engine.current_symbol}. Esperando rentabilidad...")
                # No hacemos nuevo escaneo si estamos esperando un drenaje paciente
                return 
        # -----------------------------------------------

        # --- SUPERVISOR DE MOMENTUM (posición ganadora fuera de Top1) --------
        destino_pendiente = getattr(self.engine, 'simbolo_destino_pendiente', None)
        if destino_pendiente and not self.engine.modo_drenaje:
            symbol_momentum = self.engine.current_symbol
            try:
                df_momentum = self._fetch_velas(symbol_momentum)
                if df_momentum is not None and len(df_momentum) >= 20:
                    closes = df_momentum['close']
                    ema_fast = closes.ewm(span=5,  adjust=False).mean()
                    ema_slow = closes.ewm(span=20, adjust=False).mean()
                    # Calcular ATR actual para detectar caída de volatilidad
                    hi = df_momentum['high']; lo = df_momentum['low']
                    tr = pd.concat([hi - lo, (hi - closes.shift()).abs(), (lo - closes.shift()).abs()], axis=1).max(axis=1)
                    atr_pct = tr.rolling(14).mean().iloc[-1] / closes.iloc[-1]

                    direccion_cambiada = ema_fast.iloc[-1] < ema_slow.iloc[-1] and ema_fast.iloc[-2] >= ema_slow.iloc[-2]
                    volatilidad_muerta = atr_pct < float(os.getenv("MOMENTUM_MIN_ATR_SPACING", "0.003"))

                    if direccion_cambiada or volatilidad_muerta:
                        razon = "cruce EMA (cambio de dirección)" if direccion_cambiada else "ATR caído (mercado lateral)"
                        logger.info(f"⚡ [MOMENTUM FIN] {razon} detectado en {symbol_momentum}. Cerrando posición y rotando a {destino_pendiente}.")
                        self.provider.cancel_all_orders(symbol_momentum)
                        self.provider.close_position_market(symbol_momentum)
                        self.engine.simbolo_destino_pendiente = None
                        time.sleep(2.0)
                        self._iniciar_operacion(destino_pendiente)
                        return
                    else:
                        logger.info(f"⚡ [MOMENTUM ACTIVO] EMA fast>{ema_fast.iloc[-1]:.5f} | ATR: {atr_pct*100:.3f}% — manteniendo posición en {symbol_momentum}")
                        return  # Seguir esperando — no hacer re-escaneo
            except Exception as e:
                logger.error(f"❌ Error supervisor momentum: {e}")
        # ---------------------------------------------------------------------

        # 0. Verificación de balance
        bal = self.provider.get_balance()
        usdt_disponible = bal.get("usdt_available", 0.0)
        posiciones_crudas_validas = self.provider.get_open_positions()
        
        if not posiciones_crudas_validas and usdt_disponible < self.engine.capital_inicial:
            logger.critical(f"⚠️ BALANCE INSUFICIENTE: Tienes {usdt_disponible:.2f} USDT disponibles, pero la configuración pide {self.engine.capital_inicial:.2f} USDT. Pausando peticiones...")
            return

        # 1. Hot-reload de IA (y configuraciones desde Telegram)
        overrides = self.db.get_config_overrides()
        if overrides:
            self.engine.update_params(
                atr_mult=overrides.get("GRID_ATR_MULTIPLIER"),
                leverage=overrides.get("GRID_LEVERAGE"),
                num_lineas=overrides.get("GRID_NUM_LINEAS_LADO"),
                capital_inicial=overrides.get("GRID_CAPITAL_POR_OPERACION")
            )

        # 2. Re-escanear
        nuevo_top = self._analizar_y_backtestear()
        if not nuevo_top:
            return

        # 3. Rotación o continuación (Lógica Estricta de Posiciones Abiertas)
        symbol_actual = self.engine.current_symbol
        posiciones_crudas = self._filtrar_posiciones_operables(posiciones_crudas_validas)
        
        # === NUEVO: FILTRO DE ACTIVOS CORE (Hold Mode) ===
        # Lista de monedas principales que NO deben drenarse, solo cancelar órdenes.
        core_assets = ["BTC/", "ETH/", "SOL/", "OKB/", "OKT/"] 
        todas_posiciones = []
        
        for p in posiciones_crudas:
            is_core = any(p.symbol.startswith(coin) for coin in core_assets)
            
            if is_core and p.symbol != nuevo_top:
                logger.info(f"🛡️ Activo Core en Hold detectado ({p.symbol}). Manteniendo posición abierta, cancelando órdenes (Evadiendo Drenaje).")
                self.provider.cancel_all_orders(p.symbol)
            else:
                todas_posiciones.append(p)
        # =================================================
        
        if todas_posiciones:
            # Hay posiciones colgadas. NO PODEMOS ir a un símbolo limpio.
            symbols_con_posicion = list(set([p.symbol for p in todas_posiciones]))
            
            if symbol_actual in symbols_con_posicion:
                target_symbol = symbol_actual
            else:
                # Seleccionar el más rentable si hay múltiples huérfanos
                mejor_pnl = -float('inf')
                target_symbol = symbols_con_posicion[0]
                for p in todas_posiciones:
                    try:
                        ticker = self.exchange.fetch_ticker(p.symbol)
                        current_price = float(ticker['last'])
                        pnl = (current_price - p.entry_price) * p.qty if p.side == "LONG" else (p.entry_price - current_price) * p.qty
                        if pnl > mejor_pnl:
                            mejor_pnl = pnl
                            target_symbol = p.symbol
                    except Exception:
                        pass
            
            if target_symbol != nuevo_top:
                # El símbolo tiene posición pero ya no es Top 1.
                # ── LÓGICA MOMENTUM: NO drenar si hay ganancia + volatilidad ──
                if symbol_actual != target_symbol:
                    logger.info(f"🔀 Hay posiciones abiertas. Retomando símbolo con posición: {target_symbol}")
                    self._iniciar_operacion(target_symbol)

                # Calcular PnL actual de la posición y su tamaño real
                pnl_actual = 0.0
                potencial_atr = 0.0
                pos_actual = next((p for p in todas_posiciones if p.symbol == target_symbol), None)
                if pos_actual:
                    try:
                        ticker_now = self.exchange.fetch_ticker(target_symbol)
                        price_now  = float(ticker_now['last'])
                        contract_sz = float(self.exchange.markets.get(target_symbol, {}).get("contractSize", 1.0))
                        
                        if pos_actual.side == "LONG":
                            pnl_actual = (price_now - pos_actual.entry_price) * pos_actual.qty * contract_sz
                        else:
                            pnl_actual = (pos_actual.entry_price - price_now) * pos_actual.qty * contract_sz
                            
                        # Potencial de ganancia de 1 movimiento ATR (1 línea de grid)
                        potencial_atr = self.engine.espaciado_actual * price_now * pos_actual.qty * contract_sz
                    except Exception:
                        pass

                # Detectar volatilidad: usar el ATR actual del engine
                hay_volatilidad = self.engine.espaciado_actual > float(os.getenv("MOMENTUM_MIN_ATR_SPACING", "0.003"))

                # ── LÓGICA MOMENTUM: Aprovechar ganancia O volatilidad recuperadora ──
                # Si el PnL es negativo pero menor que la ganancia potencial de 1.5 ATRs, lo consideramos recuperable
                pnl_recuperable = (pnl_actual < 0) and (abs(pnl_actual) <= (potencial_atr * 1.5))

                if (pnl_actual > 0 or pnl_recuperable) and hay_volatilidad:
                    estado_pnl = f"+{pnl_actual:.4f}" if pnl_actual > 0 else f"{pnl_actual:.4f} (recuperable vs {potencial_atr:.4f})"
                    if self.engine.modo_drenaje:
                        self.engine.modo_drenaje = False
                        logger.info(
                            f"⚡ [MOMENTUM] Cancelando drenaje — PnL: {estado_pnl} USDT | "
                            f"ATR-spacing: {self.engine.espaciado_actual*100:.3f}% | Aprovechando volatilidad."
                        )
                    else:
                        logger.info(
                            f"⚡ [MOMENTUM] Manteniendo posición en {target_symbol} "
                            f"(PnL: {estado_pnl} USDT) — sin drenar. Top1 pendiente: {nuevo_top}"
                        )
                    self.engine.simbolo_destino_pendiente = nuevo_top
                else:
                    # PnL negativo profundo o mercado lateral → drenar normalmente
                    if not self.engine.modo_drenaje:
                        razon = "pérdida muy profunda" if pnl_actual <= 0 else "volatilidad insuficiente"
                        logger.info(f"🚿 Activando MODO DRENAJE para {target_symbol} ({razon}: PnL={pnl_actual:.4f}, Potencial={potencial_atr:.4f}, meta: rotar a {nuevo_top})")
                        self.engine.activar_modo_drenaje()
                        self.engine.simbolo_destino_pendiente = nuevo_top

            else:
                # El símbolo huerfano CASUALMENTE es el Top 1. Operarlo normal.
                if symbol_actual != target_symbol:
                    self._iniciar_operacion(target_symbol)
                else:
                    logger.info(f"✅ Manteniendo operación en TOP 1 con posición: {target_symbol}")
                    df_5m = self._fetch_velas(target_symbol)
                    self.engine.calcular_espaciado_atr(df_5m, self.exchange.markets.get(target_symbol, {}))
                    
        else:
            # Sistema completamente limpio, sin posiciones en ningún lado.
            if symbol_actual != nuevo_top:
                logger.info(f"🔀 Sin posiciones abiertas. Rotando libremente hacia nuevo TOP 1: {nuevo_top}")
                self._iniciar_operacion(nuevo_top)
            else:
                logger.info(f"✅ Manteniendo operación en TOP 1 limpio: {symbol_actual}")
                df_5m = self._fetch_velas(symbol_actual)
                self.engine.calcular_espaciado_atr(df_5m, self.exchange.markets.get(symbol_actual, {}))

    def _iniciar_operacion(self, symbol: str):
        self.engine.reset()
        self.engine.current_symbol = symbol
        
        # [NUEVO] Ajustar apalancamiento en el exchange antes de operar
        if hasattr(self.provider, "set_leverage"):
            self.provider.set_leverage(self.engine.leverage, symbol)
        
        # Recuperar estado si había
        pos_real = self._filtrar_posiciones_operables(self.provider.get_open_positions(symbol))
        if pos_real:
            self.engine.forzar_sincronizacion(pos_real[0])
            
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
            self.engine.niveles = [{"side": o.side, "price": o.price, "qty": o.qty, "level": getattr(o, "grid_level", 1)} for o in abiertas]
            if not df_5m.empty:
                self.engine.centro_grid = df_5m['close'].iloc[-1]
            logger.info(f"🔄 [ESTADO] Recuperados {len(self.engine.niveles)} niveles de órdenes abiertas.")
        else:
            self.provider.cancel_all_orders(symbol)
            
        self.engine.calcular_espaciado_atr(df_5m, self.exchange.markets.get(symbol, {}))
        
        # Si no hay hilo WS para este symbol, lo levantamos
        # Usamos threading para no bloquear el _ciclo_reescaneo
        t = threading.Thread(target=self._loop_operativo, args=(symbol,), daemon=True)
        t.start()

    def _loop_operativo(self, symbol: str):
        """Mantiene el WebSocket para el symbol y evalúa en cada tick."""
        inst_id = symbol.replace("/", "-").replace(":USDT", "-SWAP")
        ws_url = "wss://wspap.okx.com:8443/ws/v5/public?brokerId=9999" if getattr(self, "mode", "demo").lower() == "demo" else "wss://ws.okx.com:8443/ws/v5/public"
        
        last_webhook_time = 0
        webhook_url = os.getenv("WEBHOOK_URL", "http://172.17.0.1:8002/api/webhook/grid")
        import urllib.request
        
        while self.engine.current_symbol == symbol:
            self.engine.ws_reconectar = False
            try:
                ws = websocket.create_connection(ws_url, sslopt={"cert_reqs": ssl.CERT_NONE}, timeout=20)
                ws.send(json.dumps({"op": "subscribe", "args": [{"channel": "tickers", "instId": inst_id}]}))
                logger.info(f"🔌 Conectado WS a {inst_id}")

                while not self.engine.ws_reconectar and self.engine.current_symbol == symbol:
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

                    # 0. Actualizar vela en vivo (y Rollover)
                    if hasattr(self, 'last_30_candles') and self.last_30_candles:
                        last_candle = self.last_30_candles[-1]
                        now_sec = int(time.time())
                        aligned_now = (now_sec // 300) * 300
                        
                        if aligned_now > last_candle['time']:
                            # Rollover a nueva vela
                            new_candle = {
                                'time': aligned_now,
                                'open': precio_actual,
                                'high': precio_actual,
                                'low': precio_actual,
                                'close': precio_actual,
                                'volume': 0
                            }
                            self.last_30_candles.append(new_candle)
                            if len(self.last_30_candles) > 200:
                                self.last_30_candles.pop(0)
                        else:
                            # Actualizar vela en curso
                            last_candle['close'] = precio_actual
                            last_candle['high'] = max(last_candle['high'], precio_actual)
                            last_candle['low'] = min(last_candle['low'], precio_actual)
                            
                    # === NUEVO: RESPIRACIÓN EN VIVO (CADA 30s CON DATOS DEL EXCHANGE) ===
                    now_sec = int(time.time())
                    if now_sec - getattr(self, 'last_atr_calc_time', 0) >= 30:
                        self.last_atr_calc_time = now_sec
                        
                        try:
                            # 1. Obtener datos 100% reales del exchange para Auto-Grid Inteligente
                            df_temp = self._fetch_velas(symbol)
                            
                            if not df_temp.empty:
                                # 2. La IA recalcula el ATR y TPs instantáneamente
                                self.engine.calcular_espaciado_atr(df_temp, self.exchange.markets.get(symbol, {}))
                                self.engine.actualizar_tps_dinamicos(df_temp)
                                
                                # 3. Chequear breakout y ajustar la malla si es necesario
                                self.engine.chequear_breakout_malla(precio_actual)
                                if self.engine.malla_necesita_reajuste(precio_actual): 
                                    self.engine._desplazar_grid(df_temp.iloc[-1]['close'])
                                    logger.info(f"🔄 [RESPIRACIÓN VIVO 30s] Malla ajustada para {symbol}")
                                
                        except Exception as e:
                            logger.error(f"❌ Fallo en deslizamiento en vivo de la IA: {e}")
                    
                    # Inicializar grid en el primer tick real si no hay niveles
                    if not self.engine.niveles:
                        grids_sugeridos = 10
                        if hasattr(self, 'mejor_params') and self.mejor_params and self.mejor_params.get("symbol") == symbol:
                            grids_sugeridos = self.mejor_params.get("num_grids", 10)
                            
                        self.engine.modo_drenaje = False 
                        self.engine.inicializar_grid(precio_actual, num_grids_sugerido=grids_sugeridos)
                        
                    self.watchdog.actualizar_precio_ws(precio_actual)
                    
                    # 1. Kill-Switch (Prioridad Máxima)
                    if self.engine.evaluar_kill_switch(precio_actual):
                        self.provider.cancel_all_orders(symbol)
                        self.provider.close_position_market(symbol)
                        self.engine.reset()
                        self.engine.current_symbol = None
                        logger.warning(f"🚨 Posición cerrada por Kill-Switch. Forzando re-escaneo inmediato.")

                        # Bloquear el símbolo: evita reentrar en un activo recién cerrado por Stop-Loss global.
                        self.db.agregar_a_lista_negra(symbol, self.mode)

                        # Pausa de seguridad para que OKX asiente la orden y actualice su API REST.
                        time.sleep(3.0)

                        self.wakeup_event.set()
                        break  # Sale del loop de este symbol
                        
                    # 2. Histéresis y Deslizamiento Normal
                    self.engine.procesar_precio_externo(precio_actual)
                    
                    # 3. Fills: procesados por WS privado en _handle_real_fill.

                    # 3.5. Chequeo de fin de Modo Drenaje
                    if self.engine.modo_drenaje and abs(self.engine.posicion_neta) < 1e-9:
                        logger.info(f"🚿 [DRENAJE COMPLETADO] Posición en {symbol} cerrada. Forzando re-escaneo inmediato.")
                        self.provider.cancel_all_orders(symbol)
                        self.engine.reset()
                        self.engine.current_symbol = None
                        self.wakeup_event.set()
                        break
                            
                    # 4. Reconciliación Batch (optimizada): solo tocar REST si la malla cambió y pasaron 30s.
                    now_sec_recon = int(time.time())
                    if getattr(self.engine, 'malla_modificada', False) and (now_sec_recon - getattr(self, 'last_reconcile_time', 0) > 30):
                        self.last_reconcile_time = now_sec_recon
                        market_info = self.exchange.markets.get(symbol, {})
                        deseadas = self.engine.obtener_ordenes_deseadas(precio_actual, market_info)
                        
                        # Solo validamos balance si vamos a abrir una malla nueva (sin posición previa).
                        if deseadas and abs(self.engine.posicion_neta) < 1e-9:
                            bal_actual = self.provider.get_balance()
                            usdt_disp = bal_actual.get("usdt_available", 0.0)
                            
                            if usdt_disp < self.engine.capital_inicial:
                                logger.critical(f"🛑 BALANCE INSUFICIENTE en {symbol}. Disp: {usdt_disp:.2f} USDT | Req: {self.engine.capital_inicial:.2f} USDT. Abortando y esperando próximo ciclo.")
                                self.provider.cancel_all_orders(symbol)
                                self.engine.reset()
                                self.engine.current_symbol = None
                                self.wakeup_event.set()
                                break

                        if getattr(self.engine, 'posicion_no_operable', False):
                            logger.warning(
                                f"⚠️ [DRENAJE OMITIDO] Posición residual de {symbol} menor al mínimo operable. "
                                "Liberando símbolo para continuar el escaneo."
                            )
                            self.provider.cancel_all_orders(symbol)
                            if hasattr(self.provider, "_positions"):
                                self.provider._positions.pop(symbol, None)
                                if hasattr(self.provider, "_force_flush"):
                                    self.provider._force_flush()
                            self.engine.reset()
                            self.engine.current_symbol = None
                            self.wakeup_event.set()
                            break

                        actuales = self.provider.get_open_orders(symbol)
                        
                        try:
                            self.provider.reconciliar_ordenes(deseadas, actuales)
                            self.engine.malla_modificada = False
                            self.force_balance_sync.set()  # Forzar actualización de UI con nuevos datos del exchange
                        except Exception as e:
                            if "51155" in str(e):
                                logger.critical(f"🛑 Símbolo {symbol} restringido por OKX (51155). Bloqueando permanentemente.")
                                self.db.agregar_a_lista_negra(symbol, self.mode)
                                self.engine.reset()
                                self.engine.current_symbol = None
                                self.wakeup_event.set()
                                break
                            raise
                    
                    # 5. Enviar webhook al frontend — datos 100% en vivo desde WS privado OKX
                    now = time.time()
                    if now - last_webhook_time > 1.0:
                        last_webhook_time = now
                        try:
                            contract_size = float(self.exchange.markets.get(symbol, {}).get("contractSize", 1.0))
                            ws_ok = self.ws_private is not None

                            # ── Balance: WS privado (account channel) ─────────────────────────
                            if ws_ok and self.ws_private.has_live_balance():
                                bal = self.ws_private.get_live_balance()
                            else:
                                # Fallback: REST cada 30 s (solo hasta que llegue el primer push WS)
                                must_sync = self.force_balance_sync.is_set() or now - getattr(self, '_last_rest_bal', 0.0) > 30.0
                                if must_sync:
                                    self.cached_bal = self.provider.get_balance()
                                    self._last_rest_bal = now
                                    self.force_balance_sync.clear()
                                bal = getattr(self, 'cached_bal', {
                                    "usdt_total":     getattr(self.engine, 'capital_inicial', 0),
                                    "usdt_available": getattr(self.engine, 'capital_inicial', 0),
                                })

                            # ── Posiciones: WS privado (positions channel) ─────────────────────
                            if ws_ok and self.ws_private.has_live_positions():
                                ws_positions = self.ws_private.get_live_positions()
                                # Filtrar por símbolo activo y normalizar instId → ccxt symbol
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
                                    pnl_ws       = p["pnl"]
                                # Sincronizar engine con lo que dice el exchange
                                if abs(net_qty_ws - self.engine.posicion_neta) > 1e-6:
                                    self.engine.posicion_neta  = net_qty_ws
                                    self.engine.precio_promedio = avg_price_ws
                                net_qty  = net_qty_ws
                                avg_price = avg_price_ws
                                pnl      = pnl_ws
                            else:
                                # Fallback: RAM del engine + PnL calculado
                                pnl = 0.0
                                if abs(self.engine.posicion_neta) > 1e-9:
                                    if self.engine.posicion_neta > 0:
                                        pnl = (precio_actual - self.engine.precio_promedio) * self.engine.posicion_neta * contract_size
                                    else:
                                        pnl = (self.engine.precio_promedio - precio_actual) * abs(self.engine.posicion_neta) * contract_size
                                net_qty   = self.engine.posicion_neta
                                avg_price = self.engine.precio_promedio
                                positions_ui = []
                                if abs(net_qty) > 1e-9:
                                    positions_ui.append({
                                        "symbol":      symbol,
                                        "side":        "LONG" if net_qty > 0 else "SHORT",
                                        "qty":         float(abs(net_qty)),
                                        "entry_price": float(avg_price),
                                        "pnl":         float(pnl),
                                    })

                            # ── Órdenes abiertas: WS privado (orders channel) ──────────────────
                            if ws_ok and self.ws_private.has_live_orders():
                                expected_inst = symbol.replace("/", "-").replace(":USDT", "-SWAP")
                                orders_ui = [
                                    {
                                        "side":        o["side"],
                                        "price":       float(o["price"]),
                                        "qty":         float(o["qty"]),
                                        "status":      "OPEN",
                                        "reduce_only": o.get("reduce_only", False),
                                    }
                                    for o in self.ws_private.get_live_orders()
                                    if o.get("symbol") == expected_inst or o.get("symbol") == symbol
                                ]
                            else:
                                # Fallback: malla RAM del engine (disponible antes del primer push WS)
                                orders_ui = [
                                    {
                                        "side":   str(getattr(o, "side", "")).upper(),
                                        "price":  float(getattr(o, "price", 0)),
                                        "qty":    float(getattr(o, "qty", 0)),
                                        "status": "OPEN",
                                    }
                                    for o in self.provider.get_open_orders(symbol)
                                ] if not ws_ok else []

                            # ── Historial de fills: WS privado ─────────────────────────────────
                            last_fills = []
                            if ws_ok:
                                expected_inst = symbol.replace("/", "-").replace(":USDT", "-SWAP")
                                for f in self.ws_private.get_live_fills():
                                    if f.get("symbol") not in (expected_inst, symbol):
                                        continue
                                    last_fills.append({
                                        "side":         f["side"],
                                        "price":        f["price"],
                                        "qty":          f["qty"],
                                        "total_monto":  f.get("total_monto", f["price"] * f["qty"]),
                                        "realized_pnl": f.get("realized_pnl", 0),
                                        "time":         f["time"],
                                    })
                            # Fallback: historial RAM del engine (fills procesados por _handle_real_fill)
                            if not last_fills:
                                for f in self.last_fills_history[-20:]:
                                    if f.get("symbol") != symbol:
                                        continue
                                    p_f = float(f.get("price", 0))
                                    q_f = float(f.get("qty",   0))
                                    bucket = (int(f.get("time", now)) // 300) * 300
                                    last_fills.append({
                                        "side":         str(f.get("side", "")).upper(),
                                        "price":        p_f,
                                        "qty":          q_f,
                                        "total_monto":  p_f * q_f,
                                        "realized_pnl": float(f.get("realized_pnl", 0)),
                                        "time":         bucket,
                                    })

                            # ── Payload final ──────────────────────────────────────────────────
                            payload = {
                                "symbol":          symbol,
                                "active_symbol":   symbol,
                                "current_symbol":  symbol,
                                "chart_symbol":    symbol,
                                "execution_mode":  self.mode.upper(),
                                "precio":          precio_actual,
                                "current_price":   precio_actual,
                                "last_price":      precio_actual,
                                "updated_at":      now,
                                # Balance desde WS privado (account channel)
                                "balance":         bal.get("usdt_total",     getattr(self.engine, 'capital_inicial', 0)),
                                "available_margin":bal.get("usdt_available", getattr(self.engine, 'capital_inicial', 0)),
                                # PnL / posición desde WS privado (positions channel)
                                "pnl_no_realizado":pnl,
                                "net_qty":         net_qty,
                                "avg_price":       avg_price,
                                # Órdenes desde WS privado (orders channel)
                                "open_orders":     orders_ui,
                                "open_positions":  positions_ui,
                                # Historial de fills desde WS privado (orders/fill channel)
                                "last_fills":      last_fills,
                                "leverage":        getattr(self.engine, 'leverage', 15),
                                "contract_size":   contract_size,
                                "candles":         getattr(self, 'last_30_candles', []),
                            }

                            self.webhook_queue.put((webhook_url, payload))
                        except Exception as e:
                            logger.error(f"❌ Fallo al preparar webhook: {e}")



            except Exception as e:
                logger.error(f"🔌 Error WS {inst_id}: {e}")
                if 'ws' in locals():
                    try:
                        ws.close()
                    except:
                        pass
                time.sleep(5)

    def _fetch_velas(self, symbol) -> pd.DataFrame:
        try:
            velas = self.exchange.fetch_ohlcv(symbol, self.timeframe, limit=self.limit)
            return pd.DataFrame(velas, columns=["timestamp", "open", "high", "low", "close", "volume"])
        except:
            return pd.DataFrame()

    def _analizar_y_backtestear(self) -> str:
        """Retorna el symbol ganador."""
        try:
            simbolos = obtener_futuros_usdt(self.exchange)
            liquidos = filtrar_por_volumen(self.exchange, simbolos, self.min_volume)
            
            # FILTRO BLACKLIST
            liquidos = [s for s in liquidos if not self.db.es_lista_negra(s, self.mode)]
            
            df_res = analizar_lote(self.exchange, liquidos[:self.limit], self.timeframe, self.limit)
            
            if df_res.empty: return ""
            
            top_n = self.top_n
            top_syms = df_res["symbol"].head(top_n).tolist()
            
            self.db.save_scanner_state(df_res.head(top_n).to_dict(orient="records"), 1)
            
            logger.info("💹 Ejecutando Backtest dinámico sobre Top N...")
            capital = float(os.getenv("GRID_CAPITAL_POR_OPERACION", 5.0))
            leverage = float(os.getenv("GRID_LEVERAGE", 15.0))
            # ✅ FIX: Nueva firma del backtester (maneja temporalidades internamente)
            bt_resultados = backtest_grid_top(self.exchange, top_syms, capital, leverage)
            
            if bt_resultados:
                self.mejor_params = bt_resultados[0]
                return bt_resultados[0]["symbol"]
            return top_syms[0]
        except Exception as e:
            logger.error(f"Error analizando mercados: {e}")
            return ""
