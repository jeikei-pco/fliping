"""
database.py — Gestión de la Base de Datos Local (SQLite) V2.

Patrón RAM-first + Flush periódico.
Sin lógica de negocio. Solo persistencia.

Nuevas tablas en V2:
  - backtest_history: historial de backtests para que la IA aprenda
  - ia_overrides_history: historial de overrides aplicados
  - bad_configs: configuraciones que causaron drawdown > umbral
  - market_metrics_cache: caché de análisis por símbolo
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from core.models import BacktestResult, IAOverrides, TradingMetrics

logger = logging.getLogger("UAO_Grid.Database")

# ─────────────────────────────────────────────────────────────────────────────
# DDL: Esquema completo V2 (versionado)
# ─────────────────────────────────────────────────────────────────────────────

_SCHEMA_VERSION = 2

_DDL_STATEMENTS = [
    # ── V1 (preservadas) ──────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS balance (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        usdt_total REAL NOT NULL,
        usdt_available REAL NOT NULL,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS positions (
        symbol TEXT PRIMARY KEY,
        side TEXT NOT NULL,
        qty REAL NOT NULL,
        entry_price REAL NOT NULL,
        opened_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS orders (
        order_id TEXT PRIMARY KEY,
        symbol TEXT NOT NULL,
        side TEXT NOT NULL,
        price REAL NOT NULL,
        qty REAL NOT NULL,
        status TEXT NOT NULL,
        reduce_only BOOLEAN NOT NULL DEFAULT 0,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        grid_level INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS trades (
        trade_id TEXT PRIMARY KEY,
        symbol TEXT NOT NULL,
        side TEXT NOT NULL,
        price REAL NOT NULL,
        qty REAL NOT NULL,
        pnl REAL NOT NULL DEFAULT 0,
        fee REAL NOT NULL DEFAULT 0,
        executed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS grid_state (
        symbol TEXT PRIMARY KEY,
        niveles_json TEXT NOT NULL,
        espaciado REAL NOT NULL DEFAULT 0,
        centro REAL NOT NULL DEFAULT 0,
        posicion_neta REAL NOT NULL DEFAULT 0,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS scanner_state (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        ranking_json TEXT NOT NULL,
        cycle_count INTEGER NOT NULL DEFAULT 0,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS config_overrides (
        param_key TEXT PRIMARY KEY,
        param_value TEXT NOT NULL,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS blacklist (
        symbol TEXT NOT NULL,
        mode TEXT NOT NULL DEFAULT 'ALL',
        reason TEXT,
        added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (symbol, mode)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ml_history (
        session_id TEXT PRIMARY KEY,
        symbol TEXT NOT NULL,
        score REAL,
        total_trades INTEGER DEFAULT 0,
        wins INTEGER DEFAULT 0,
        gross_profit REAL DEFAULT 0,
        gross_loss REAL DEFAULT 0,
        win_rate REAL DEFAULT 0,
        profit_factor REAL DEFAULT 0,
        params_json TEXT,
        started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        ended_at TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS grid_status_cache (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        payload_json TEXT NOT NULL,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    # ── V2 (nuevas) ────────────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS backtest_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        modo TEXT NOT NULL,
        apalancamiento INTEGER NOT NULL,
        num_grids INTEGER NOT NULL,
        espaciado_pct REAL NOT NULL,
        pnl_neto REAL NOT NULL,
        operaciones INTEGER NOT NULL,
        win_rate REAL NOT NULL,
        profit_factor REAL NOT NULL,
        max_drawdown REAL NOT NULL,
        sharpe_ratio REAL NOT NULL,
        calmar_ratio REAL NOT NULL,
        expectancy REAL NOT NULL,
        recovery_factor REAL NOT NULL,
        score_backtest REAL NOT NULL,
        params_json TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ia_overrides_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        grid_step_pct REAL,
        grid_density_factor REAL,
        leverage_factor REAL,
        capital_factor REAL,
        max_leverage INTEGER,
        min_score REAL,
        min_consistency REAL,
        min_oscillation REAL,
        model_used TEXT,
        confidence REAL DEFAULT 1.0,
        applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS bad_configs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        modo TEXT NOT NULL,
        apalancamiento INTEGER,
        espaciado_pct REAL,
        drawdown_pct REAL NOT NULL,
        pnl_neto REAL NOT NULL,
        reason TEXT,
        flagged_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS market_metrics_cache (
        symbol TEXT PRIMARY KEY,
        metrics_json TEXT NOT NULL,
        score REAL NOT NULL,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS schema_version (
        version INTEGER PRIMARY KEY,
        applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
]

# Índices para consultas frecuentes
_INDEX_STATEMENTS = [
    "CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol)",
    "CREATE INDEX IF NOT EXISTS idx_trades_executed_at ON trades(executed_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_backtest_symbol ON backtest_history(symbol)",
    "CREATE INDEX IF NOT EXISTS idx_backtest_score ON backtest_history(score_backtest DESC)",
    "CREATE INDEX IF NOT EXISTS idx_bad_configs_symbol ON bad_configs(symbol)",
]


# ─────────────────────────────────────────────────────────────────────────────
# Database
# ─────────────────────────────────────────────────────────────────────────────

class Database:
    """
    Gestión de la base de datos SQLite local.

    Thread-safe: todas las operaciones de escritura usan self._lock.
    Cada método crea su propia conexión (no compartir entre hilos).
    """

    def __init__(self, db_path: str = "./data/uao_grid.db") -> None:
        self.db_path = db_path
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self._init_db()

    # ── Conexión ─────────────────────────────────────────────────────────────

    def _get_conn(self) -> sqlite3.Connection:
        """Retorna una nueva conexión SQLite. No compartir entre hilos."""
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self) -> None:
        """Inicializa el esquema completo y aplica migraciones."""
        with self._lock:
            with self._get_conn() as conn:
                cursor = conn.cursor()

                # Crear tablas
                for stmt in _DDL_STATEMENTS:
                    cursor.execute(stmt)

                # Crear índices
                for stmt in _INDEX_STATEMENTS:
                    cursor.execute(stmt)

                # Migración V1 → V2: añadir columnas que pueden faltar en DB existentes
                _safe_migrations = [
                    "ALTER TABLE orders ADD COLUMN grid_level INTEGER NOT NULL DEFAULT 0",
                    "ALTER TABLE ml_history ADD COLUMN gross_profit REAL DEFAULT 0",
                    "ALTER TABLE ml_history ADD COLUMN gross_loss REAL DEFAULT 0",
                    "ALTER TABLE ml_history ADD COLUMN win_rate REAL DEFAULT 0",
                    "ALTER TABLE ml_history ADD COLUMN profit_factor REAL DEFAULT 0",
                ]
                for migration in _safe_migrations:
                    try:
                        cursor.execute(migration)
                    except sqlite3.OperationalError:
                        pass  # Columna ya existe

                # Marcar versión
                cursor.execute(
                    "INSERT OR IGNORE INTO schema_version (version) VALUES (?)",
                    (_SCHEMA_VERSION,),
                )
                conn.commit()

        logger.info("✅ Base de datos inicializada: %s (schema v%d)", self.db_path, _SCHEMA_VERSION)

    # ── Escrituras principales ────────────────────────────────────────────────

    def flush_state(
        self,
        balance: Optional[Dict[str, float]] = None,
        positions: Optional[List[Dict[str, Any]]] = None,
        orders: Optional[List[Dict[str, Any]]] = None,
        grid_state: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Escritura batch atómica de estado en RAM → SQLite."""
        with self._lock:
            with self._get_conn() as conn:
                cursor = conn.cursor()

                if balance:
                    cursor.execute(
                        "INSERT OR REPLACE INTO balance (id, usdt_total, usdt_available, updated_at) "
                        "VALUES (1, ?, ?, CURRENT_TIMESTAMP)",
                        (balance.get("usdt_total", 0.0), balance.get("usdt_available", 0.0)),
                    )

                if positions is not None:
                    cursor.execute("DELETE FROM positions")
                    for p in positions:
                        cursor.execute(
                            "INSERT OR REPLACE INTO positions (symbol, side, qty, entry_price) VALUES (?, ?, ?, ?)",
                            (p["symbol"], p["side"], p["qty"], p["entry_price"]),
                        )

                if orders is not None:
                    cursor.execute("DELETE FROM orders")
                    for o in orders:
                        cursor.execute(
                            "INSERT OR REPLACE INTO orders "
                            "(order_id, symbol, side, price, qty, status, reduce_only, grid_level) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                            (
                                o["order_id"], o["symbol"], o["side"],
                                o["price"], o["qty"], o.get("status", "OPEN"),
                                int(o.get("reduce_only", False)), o.get("grid_level", 0),
                            ),
                        )

                if grid_state:
                    cursor.execute(
                        "INSERT OR REPLACE INTO grid_state "
                        "(symbol, niveles_json, espaciado, centro, posicion_neta, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
                        (
                            grid_state["symbol"],
                            json.dumps(grid_state.get("niveles", [])),
                            grid_state.get("espaciado", 0.0),
                            grid_state.get("centro", 0.0),
                            grid_state.get("posicion_neta", 0.0),
                        ),
                    )

                conn.commit()

    def record_trade(
        self,
        trade_id: str,
        symbol: str,
        side: str,
        price: float,
        qty: float,
        pnl: float = 0.0,
        fee: float = 0.0,
        executed_at: Optional[str] = None,
    ) -> None:
        """Registra un trade ejecutado. Idempotente (INSERT OR IGNORE)."""
        ts = executed_at or datetime.now(timezone.utc).isoformat()
        with self._lock:
            with self._get_conn() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO trades "
                    "(trade_id, symbol, side, price, qty, pnl, fee, executed_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (trade_id, symbol, side, price, qty, pnl, fee, ts),
                )
                conn.commit()

    # ── Backtest History (V2) ─────────────────────────────────────────────────

    def save_backtest(self, result: BacktestResult) -> None:
        """Persiste un resultado de backtest para que la IA aprenda."""
        with self._lock:
            with self._get_conn() as conn:
                conn.execute(
                    """
                    INSERT INTO backtest_history (
                        symbol, modo, apalancamiento, num_grids, espaciado_pct,
                        pnl_neto, operaciones, win_rate, profit_factor,
                        max_drawdown, sharpe_ratio, calmar_ratio, expectancy,
                        recovery_factor, score_backtest, params_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        result.symbol, result.modo_optimo.value, result.apalancamiento,
                        result.num_grids, result.espaciado_pct, result.pnl_neto,
                        result.operaciones, result.win_rate, result.profit_factor,
                        result.max_drawdown, result.sharpe_ratio, result.calmar_ratio,
                        result.expectancy, result.recovery_factor, result.score_backtest,
                        json.dumps(result.params_usados),
                    ),
                )
                conn.commit()

    def get_backtest_history(
        self,
        symbol: Optional[str] = None,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        """Retorna historial de backtests para análisis de la IA."""
        with self._get_conn() as conn:
            if symbol:
                rows = conn.execute(
                    "SELECT * FROM backtest_history WHERE symbol=? ORDER BY score_backtest DESC LIMIT ?",
                    (symbol, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM backtest_history ORDER BY score_backtest DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [dict(r) for r in rows]

    def flag_bad_config(
        self,
        symbol: str,
        modo: str,
        apalancamiento: int,
        espaciado_pct: float,
        drawdown_pct: float,
        pnl_neto: float,
        reason: str = "",
    ) -> None:
        """Marca una configuración como mala para que la IA la evite."""
        with self._lock:
            with self._get_conn() as conn:
                conn.execute(
                    "INSERT INTO bad_configs "
                    "(symbol, modo, apalancamiento, espaciado_pct, drawdown_pct, pnl_neto, reason) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (symbol, modo, apalancamiento, espaciado_pct, drawdown_pct, pnl_neto, reason),
                )
                conn.commit()
        logger.warning("🚫 Config marcada como mala: %s %s lev=%d drawdown=%.1f%%", symbol, modo, apalancamiento, drawdown_pct)

    def get_bad_configs(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """Retorna configuraciones históricamente malas."""
        with self._get_conn() as conn:
            if symbol:
                rows = conn.execute(
                    "SELECT * FROM bad_configs WHERE symbol=? ORDER BY drawdown_pct DESC",
                    (symbol,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM bad_configs ORDER BY drawdown_pct DESC LIMIT 100"
                ).fetchall()
        return [dict(r) for r in rows]

    # ── IA Overrides (V2) ─────────────────────────────────────────────────────

    def save_ia_overrides(self, overrides: IAOverrides) -> None:
        """Persiste los overrides de la IA en historial y tabla activa."""
        with self._lock:
            with self._get_conn() as conn:
                # Historial
                conn.execute(
                    """
                    INSERT INTO ia_overrides_history (
                        grid_step_pct, grid_density_factor, leverage_factor, capital_factor,
                        max_leverage, min_score, min_consistency, min_oscillation,
                        model_used, confidence
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        overrides.grid_step_pct, overrides.grid_density_factor,
                        overrides.leverage_factor, overrides.capital_factor,
                        overrides.max_leverage, overrides.min_score,
                        overrides.min_consistency, overrides.min_oscillation,
                        overrides.model_used, overrides.confidence,
                    ),
                )
                # Tabla activa (compat con V1)
                active = {
                    "GRID_DENSITY_FACTOR": str(overrides.grid_density_factor),
                    "LEVERAGE_FACTOR": str(overrides.leverage_factor),
                    "CAPITAL_FACTOR": str(overrides.capital_factor),
                    "MAX_LEVERAGE": str(overrides.max_leverage),
                    "MIN_SCORE": str(overrides.min_score),
                    "MIN_CONSISTENCY": str(overrides.min_consistency),
                    "MIN_OSCILLATION": str(overrides.min_oscillation),
                }
                if overrides.grid_step_pct is not None:
                    active["GRID_STEP_PCT"] = str(overrides.grid_step_pct)

                for key, val in active.items():
                    conn.execute(
                        "INSERT OR REPLACE INTO config_overrides (param_key, param_value, updated_at) "
                        "VALUES (?, ?, CURRENT_TIMESTAMP)",
                        (key, val),
                    )
                conn.commit()

    def get_ia_overrides(self) -> IAOverrides:
        """Carga los overrides activos desde la DB. Retorna defaults si no hay."""
        with self._get_conn() as conn:
            rows = conn.execute("SELECT param_key, param_value FROM config_overrides").fetchall()

        raw = {r["param_key"]: r["param_value"] for r in rows}

        def _f(key: str, default: float) -> float:
            try:
                return float(raw[key]) if key in raw else default
            except (ValueError, TypeError):
                return default

        def _i(key: str, default: int) -> int:
            try:
                return int(float(raw[key])) if key in raw else default
            except (ValueError, TypeError):
                return default

        return IAOverrides(
            grid_step_pct=float(raw["GRID_STEP_PCT"]) if "GRID_STEP_PCT" in raw else None,
            grid_density_factor=_f("GRID_DENSITY_FACTOR", 1.0),
            leverage_factor=_f("LEVERAGE_FACTOR", 1.0),
            capital_factor=_f("CAPITAL_FACTOR", 1.0),
            max_leverage=_i("MAX_LEVERAGE", 20),
            min_score=_f("MIN_SCORE", 30.0),
            min_consistency=_f("MIN_CONSISTENCY", 0.0),
            min_oscillation=_f("MIN_OSCILLATION", 0.0),
        )

    # ── Market Metrics Cache (V2) ─────────────────────────────────────────────

    def save_market_metrics(self, symbol: str, metrics: Dict[str, Any], score: float) -> None:
        """Guarda caché de análisis de mercado por símbolo."""
        with self._lock:
            with self._get_conn() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO market_metrics_cache "
                    "(symbol, metrics_json, score, updated_at) VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
                    (symbol, json.dumps(metrics), score),
                )
                conn.commit()

    def get_market_metrics_cache(self, max_age_minutes: int = 30) -> List[Dict[str, Any]]:
        """Retorna métricas cacheadas no más antiguas que max_age_minutes."""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM market_metrics_cache "
                "WHERE datetime(updated_at) >= datetime('now', ? || ' minutes') "
                "ORDER BY score DESC",
                (f"-{max_age_minutes}",),
            ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            try:
                d["metrics"] = json.loads(d.pop("metrics_json", "{}"))
            except json.JSONDecodeError:
                d["metrics"] = {}
            result.append(d)
        return result

    # ── Trading Metrics (V2) ──────────────────────────────────────────────────

    def get_trading_metrics(self, limit: int = 500) -> TradingMetrics:
        """
        Calcula métricas de rendimiento real del bot desde los trades históricos.
        La IA Optimizer recibe este objeto en lugar de calcular las métricas por sí misma.
        """
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM trades ORDER BY executed_at DESC LIMIT ?",
                (limit,),
            ).fetchall()

        trades = [dict(r) for r in rows]
        if not trades:
            return TradingMetrics()

        total = len(trades)
        wins  = [t for t in trades if t["pnl"] > 0]
        losses = [t for t in trades if t["pnl"] < 0]

        gross_profit = sum(t["pnl"] for t in wins)
        gross_loss   = abs(sum(t["pnl"] for t in losses))

        win_rate     = round(len(wins) / total * 100, 2) if total else 0.0
        pf           = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (99.0 if gross_profit > 0 else 0.0)
        net_pnl      = round(sum(t["pnl"] - t.get("fee", 0) for t in trades), 2)
        avg_profit   = round(gross_profit / len(wins), 2) if wins else 0.0
        avg_loss     = round(gross_loss / len(losses), 2) if losses else 0.0

        longs  = [t for t in trades if t["side"].upper() == "BUY"]
        shorts = [t for t in trades if t["side"].upper() == "SELL"]
        long_wr  = round(len([t for t in longs  if t["pnl"] > 0]) / len(longs)  * 100, 2) if longs  else 0.0
        short_wr = round(len([t for t in shorts if t["pnl"] > 0]) / len(shorts) * 100, 2) if shorts else 0.0

        max_wins, max_losses, cur_w, cur_l = 0, 0, 0, 0
        for t in reversed(trades):
            if t["pnl"] > 0:
                cur_w += 1; cur_l = 0; max_wins = max(max_wins, cur_w)
            elif t["pnl"] < 0:
                cur_l += 1; cur_w = 0; max_losses = max(max_losses, cur_l)

        pnl_by_hour: Dict[str, float] = {}
        for t in trades:
            try:
                hour = t["executed_at"][:13].split("T")[-1].split(":")[0]
                pnl_by_hour[hour] = round(pnl_by_hour.get(hour, 0.0) + t["pnl"] - t.get("fee", 0), 2)
            except Exception:
                pass

        per_symbol: Dict[str, Dict[str, Any]] = {}
        for t in trades:
            sym = t["symbol"]
            if sym not in per_symbol:
                per_symbol[sym] = {"trades": 0, "wins": 0, "pnl": 0.0, "gross_profit": 0.0, "gross_loss": 0.0}
            per_symbol[sym]["trades"] += 1
            per_symbol[sym]["pnl"] += t["pnl"] - t.get("fee", 0)
            if t["pnl"] > 0:
                per_symbol[sym]["wins"] += 1
                per_symbol[sym]["gross_profit"] += t["pnl"]
            elif t["pnl"] < 0:
                per_symbol[sym]["gross_loss"] += abs(t["pnl"])

        # Solo top 15 por número de trades
        per_symbol = dict(sorted(per_symbol.items(), key=lambda x: x[1]["trades"], reverse=True)[:15])

        # Añadir métricas derivadas
        for sym, stat in per_symbol.items():
            gl = stat["gross_loss"]
            gp = stat["gross_profit"]
            stat["win_rate_real"] = round(stat["wins"] / stat["trades"] * 100, 2)
            stat["profit_factor_real"] = round(gp / gl, 2) if gl > 0 else (99.0 if gp > 0 else 0.0)

        # Backtest recientes
        recent_bt = self.get_backtest_history(limit=50)

        return TradingMetrics(
            total_trades=total,
            win_rate_pct=win_rate,
            profit_factor=pf,
            net_pnl=net_pnl,
            avg_profit=avg_profit,
            avg_loss=avg_loss,
            long_win_rate=long_wr,
            short_win_rate=short_wr,
            max_consecutive_wins=max_wins,
            max_consecutive_losses=max_losses,
            pnl_by_utc_hour=pnl_by_hour,
            per_symbol=per_symbol,
            recent_backtests=recent_bt,
        )

    # ── Scanner State ─────────────────────────────────────────────────────────

    def save_scanner_state(self, ranking: List[Dict[str, Any]], cycle_count: int = 0) -> None:
        with self._lock:
            with self._get_conn() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO scanner_state (id, ranking_json, cycle_count, updated_at) "
                    "VALUES (1, ?, ?, CURRENT_TIMESTAMP)",
                    (json.dumps(ranking), cycle_count),
                )
                conn.commit()

    def get_scanner_state(self) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            row = conn.execute("SELECT ranking_json FROM scanner_state WHERE id=1").fetchone()
        if not row:
            return []
        try:
            return json.loads(row["ranking_json"])
        except json.JSONDecodeError:
            return []

    # ── Trades ───────────────────────────────────────────────────────────────

    def get_recent_trades(
        self,
        limit: int = 500,
        symbol: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            if symbol:
                rows = conn.execute(
                    "SELECT * FROM trades WHERE symbol=? ORDER BY executed_at DESC LIMIT ?",
                    (symbol, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM trades ORDER BY executed_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [dict(r) for r in rows]

    # ── Grid State ────────────────────────────────────────────────────────────

    def load_grid_state(self, symbol: str) -> Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM grid_state WHERE symbol=?", (symbol,)
            ).fetchone()
        if not row:
            return None
        d = dict(row)
        try:
            d["niveles"] = json.loads(d.pop("niveles_json", "[]"))
        except json.JSONDecodeError:
            d["niveles"] = []
        return d

    # ── Blacklist ─────────────────────────────────────────────────────────────

    def add_to_blacklist(self, symbol: str, mode: str = "ALL", reason: str = "") -> None:
        with self._lock:
            with self._get_conn() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO blacklist (symbol, mode, reason) VALUES (?, ?, ?)",
                    (symbol, mode, reason),
                )
                conn.commit()

    def remove_from_blacklist(self, symbol: str) -> None:
        with self._lock:
            with self._get_conn() as conn:
                conn.execute("DELETE FROM blacklist WHERE symbol=?", (symbol,))
                conn.commit()

    def get_blacklist(self) -> List[str]:
        with self._get_conn() as conn:
            rows = conn.execute("SELECT symbol FROM blacklist").fetchall()
        return [r["symbol"] for r in rows]

    def is_blacklisted(self, symbol: str) -> bool:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM blacklist WHERE symbol=?", (symbol,)
            ).fetchone()
        return row is not None

    # ── Balance ───────────────────────────────────────────────────────────────

    def load_balance(self) -> Dict[str, float]:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM balance WHERE id=1").fetchone()
        if not row:
            return {"usdt_total": 0.0, "usdt_available": 0.0}
        return {"usdt_total": row["usdt_total"], "usdt_available": row["usdt_available"]}

    # ── ML Session (V1 compat) ────────────────────────────────────────────────

    def create_ml_session(self, session_id: str, symbol: str, score: float, params: Dict[str, Any]) -> None:
        with self._lock:
            with self._get_conn() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO ml_history (session_id, symbol, score, params_json) VALUES (?, ?, ?, ?)",
                    (session_id, symbol, score, json.dumps(params)),
                )
                conn.commit()

    def update_ml_session_trade(self, symbol: str, pnl: float) -> None:
        with self._lock:
            with self._get_conn() as conn:
                conn.execute(
                    """
                    UPDATE ml_history SET
                        total_trades = total_trades + 1,
                        wins = wins + CASE WHEN ? > 0 THEN 1 ELSE 0 END,
                        gross_profit = gross_profit + CASE WHEN ? > 0 THEN ? ELSE 0 END,
                        gross_loss = gross_loss + CASE WHEN ? < 0 THEN ABS(?) ELSE 0 END,
                        win_rate = CAST(wins + CASE WHEN ? > 0 THEN 1 ELSE 0 END AS REAL) / (total_trades + 1)
                    WHERE session_id = (
                        SELECT session_id FROM ml_history WHERE symbol=? ORDER BY started_at DESC LIMIT 1
                    )
                    """,
                    (pnl, pnl, pnl, pnl, pnl, pnl, symbol),
                )
                conn.commit()

    # ── Grid Status Cache (V1 compat) ─────────────────────────────────────────

    def save_grid_status_cache(self, payload: Dict[str, Any]) -> None:
        with self._lock:
            with self._get_conn() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO grid_status_cache (id, payload_json, updated_at) "
                    "VALUES (1, ?, CURRENT_TIMESTAMP)",
                    (json.dumps(payload),),
                )
                conn.commit()

    def get_grid_status_cache(self) -> Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            row = conn.execute("SELECT payload_json FROM grid_status_cache WHERE id=1").fetchone()
        if not row:
            return None
        try:
            return json.loads(row["payload_json"])
        except json.JSONDecodeError:
            return None
