"""
scanner_agent.py — Agente orquestador principal de UAO_Grid.
"""
import logging
import os
import time
import threading
import ccxt

from core.analizador import analizar_lote
from core.backtester import backtest_grid_top, formatear_grid_backtest
from core.estado import EstadoUAO
from core.database import Database
from core.okx_connector import filtrar_por_volumen, inicializar_okx, obtener_futuros_usdt

logger = logging.getLogger("UAO_Sclaping.GridScanner")

class GlobalTarget:
    def __init__(self):
        self.lock = threading.Lock()
        self.targets = []
        self.targets_version = 0

    def update_targets(self, targets_list):
        with self.lock:
            self.targets = targets_list
            self.targets_version += 1
            logger.info("🎯 GlobalTarget actualizado con %d símbolos potenciales (v%d).", len(targets_list), self.targets_version)

    def get_targets(self):
        with self.lock:
            return self.targets

    def get_targets_version(self):
        with self.lock:
            return self.targets_version

def _env_str(key: str, default: str) -> str:
    return os.getenv(key, default).strip()

def _env_int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except (ValueError, TypeError):
        return default

def _env_float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, str(default)))
    except (ValueError, TypeError):
        return default

class GridScannerAgent:
    def __init__(self):
        self.timeframe           = _env_str("SCAN_TIMEFRAME", "5m")
        self.limit               = _env_int("SCAN_LIMIT", 400)
        self.max_simbolos        = _env_int("SCAN_MAX_SYMBOLS", 400)
        self.cobertura_pct       = _env_float("SCAN_COVERAGE_PCT", 1)
        self.min_volume_usdt     = _env_float("SCAN_MIN_VOLUME_USDT", 500_000.0)
        self.ciclo_segundos      = 300 # Ciclo de busqueda default de 5 minutos
        self.top_n               = _env_int("SCAN_TOP_N", 200)
        self.execution_mode      = _env_str("EXECUTION_MODE", "SIMULATED")

        self.estado = EstadoUAO()
        self.db = Database()
        self.global_target = GlobalTarget()

        logger.info(
            "🤖 UAO_Grid iniciada | timeframe=%s | limit=%d | cobertura=%.0f%% | "
            "vol_min=$%.0f | ciclo=%ds | top=%d",
            self.timeframe, self.limit, self.cobertura_pct * 100,
            self.min_volume_usdt, self.ciclo_segundos, self.top_n
        )

    def _calcular_delay_rate_limit(self, exchange: ccxt.Exchange) -> float:
        rate_ms = getattr(exchange, "rateLimit", 150)
        return (rate_ms * 1.2) / 1_000.0

    def ejecutar_ciclo(self, exchange: ccxt.Exchange) -> bool:
        logger.info("\n" + "═" * 60)
        logger.info("🔄 CICLO GRID #%d — %s", self.estado.ciclos_totales + 1,
                    time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        logger.info("═" * 60)

        try:
            simbolos = obtener_futuros_usdt(exchange)
        except Exception as exc:
            logger.error("❌ Error obteniendo mercados OKX: %s", exc)
            return False

        if not simbolos:
            return False

        try:
            simbolos_liquidos = filtrar_por_volumen(exchange, simbolos, self.min_volume_usdt)
        except Exception as exc:
            logger.warning("⚠️ Error en filtrado de volumen: %s. Usando todos.", exc)
            simbolos_liquidos = simbolos
            
        # Filtro de Blacklist
        simbolos_liquidos = [s for s in simbolos_liquidos if not self.db.es_lista_negra(s, self.execution_mode)]

        if not simbolos_liquidos:
            return False

        delay = self._calcular_delay_rate_limit(exchange)
        n_filtrados = len(simbolos_liquidos)
        por_cobertura = max(1, int(n_filtrados * self.cobertura_pct))
        max_a_analizar = max(self.max_simbolos, por_cobertura)
        simbolos_a_analizar = simbolos_liquidos[:max_a_analizar]

        logger.info(
            "🔍 Analizando %d símbolos para GRID (limit=%d velas %s)...",
            len(simbolos_a_analizar), self.limit, self.timeframe
        )

        df_resultados = analizar_lote(
            exchange,
            simbolos_a_analizar,
            self.timeframe,
            self.limit,
            delay=delay
        )

        if df_resultados.empty:
            logger.warning("⚠️ Ciclo sin resultados válidos.")
            return False

        self.estado.actualizar_ranking(df_resultados, top_n=self.top_n)
        
        # Formato de consola para el ranking Grid
        resumen = ["\n📊 RANKING GRID (Max Ping-Pong / Zero Drift):"]
        for i, row in df_resultados.head(self.top_n).iterrows():
            sym = row["symbol"]
            score = row["score"]
            rng = row["rango_pct_promedio"] * 100
            drift = row["deriva_total_pct"] * 100
            resumen.append(f"{i+1:02d}. {sym:<12} | Score: {score:.4f} | Rango: {rng:.2f}% | Deriva: {drift:+.2f}%")
        logger.info("\n".join(resumen))

        # Backtest de Grid para el Top 10
        top_simbolos = [row["symbol"] for row in self.estado.ultimo_ranking]
        if top_simbolos:
            logger.info("💹 Backtest Grid Histórico | Optimizando espaciado de malla dinámico en el Top 10...")
            
            # --- NUEVO: Sincronización de Capital y Leverage ---
            cap_backtest = _env_float("GRID_CAPITAL_POR_OPERACION", 50.0)
            lev_backtest = _env_float("GRID_LEVERAGE", 15.0)
            
            try:
                bt_resultados = backtest_grid_top(
                    exchange=exchange,
                    top_symbols=top_simbolos,
                    timeframe=self.timeframe,
                    limit=self.limit,
                    capital=cap_backtest,  # Usar variable
                    leverage=lev_backtest, # Usar variable
                    delay=delay
                )
                self.estado.actualizar_backtest(bt_resultados)
                reporte_bt = formatear_grid_backtest(bt_resultados)
                logger.info(reporte_bt)
                
                if bt_resultados:
                    mejor_symbol = bt_resultados[0]["symbol"]
                    mejor_espaciado = bt_resultados[0].get("espaciado_pct", 0.003)
                    self.global_target.update_targets([{"symbol": mejor_symbol, "espaciado_pct": mejor_espaciado}])
                    logger.info(f"🏆 Ganador Definitivo del Grid Backtest: {mejor_symbol} (Distancia Óptima: {mejor_espaciado*100:.2f}%)")
                else:
                    self.global_target.update_targets(self.estado.ultimo_ranking)
                    
            except Exception as exc:
                logger.warning("⚠️ Error en Grid Backtest: %s", exc)
                self.global_target.update_targets(self.estado.ultimo_ranking)
        else:
            self.global_target.update_targets([])

        if self.global_target.get_targets():
            logger.info("⏱️ Ajustando ciclo de escáner a 30 minutos (1800s) mientras el Grid opera.")
            self.ciclo_segundos = 1800
        else:
            logger.info("⏱️ Sin ganador. Manteniendo ciclo de escáner en 300s (5 min).")
            self.ciclo_segundos = 300

        return True

    def run_forever(self, simulator_class=None) -> None:
        logger.info("🚀 Iniciando bucle principal de UAO_Grid...")
        try:
            exchange = inicializar_okx()
        except Exception as exc:
            logger.critical("💥 No se pudo inicializar OKX: %s. Abortando.", exc)
            return

        if simulator_class is None:
            from core.simulador import GridSimuladorEnVivo
            simulator_class = GridSimuladorEnVivo

        # --- NUEVO: Sincronización de Capital y Leverage en el Simulador ---
        cap_sim = _env_float("GRID_CAPITAL_POR_OPERACION", 50.0)
        lev_sim = _env_float("GRID_LEVERAGE", 15.0)

        sim = simulator_class(
            exchange=exchange,
            symbol="WAIT",
            capital=cap_sim,
            leverage=lev_sim,
            timeframe=self.timeframe,
            n_ops=5,
            intervalo=60,
            execution_mode=self.execution_mode,
            estado_global=self.estado,
            perfil={},
            bt_resultado={},
        )
        
        def run_simulator():
            sim.ejecutar_continuo(self.global_target, self.estado)
            
        sim_thread = threading.Thread(target=run_simulator, daemon=True)
        sim_thread.start()

        while True:
            try:
                exito = self.ejecutar_ciclo(exchange)
                if not exito:
                    self.estado.registrar_error()
            except ccxt.NetworkError as exc:
                logger.error("🌐 Error de red CCXT: %s.", exc)
                self.estado.registrar_error()
                try:
                    exchange = inicializar_okx()
                except Exception:
                    pass
            except Exception as exc:
                logger.error("❌ Error inesperado en ciclo: %s", exc, exc_info=True)
                self.estado.registrar_error()
            finally:
                logger.info("⏳ Esperando %d segundos para el próximo ciclo...\n", self.ciclo_segundos)
                time.sleep(self.ciclo_segundos)
