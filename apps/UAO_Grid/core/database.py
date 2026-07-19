"""
database.py — Gestión de la Base de Datos Local en SQLite.
Patrón RAM-first + Flush periódico para evitar sobreescribir disco.
"""
import sqlite3
import json
import logging
import os
import threading
from typing import Dict, Any, List, Optional
from datetime import datetime

logger = logging.getLogger("UAO_Sclaping.database")

class Database:
    def __init__(self, db_path: str = "./data/uao_grid.db"):
        self.db_path = db_path
        self._lock = threading.Lock()
        
        # Crear directorio si no existe
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        self._init_db()

    def _get_connection(self):
        """Retorna una nueva conexión SQLite. IMPORTANTE: No compartir entre hilos."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        """Inicializa las tablas de la base de datos."""
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                # Tabla Balance
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS balance (
                        id INTEGER PRIMARY KEY CHECK (id = 1),
                        usdt_total REAL NOT NULL,
                        usdt_available REAL NOT NULL,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')

                # Tabla Posiciones
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS positions (
                        symbol TEXT PRIMARY KEY,
                        side TEXT NOT NULL,
                        qty REAL NOT NULL,
                        entry_price REAL NOT NULL,
                        opened_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')

                # Tabla Órdenes
                cursor.execute('''
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
                ''')
                
                # Migración para DB existente
                try:
                    cursor.execute('ALTER TABLE orders ADD COLUMN grid_level INTEGER NOT NULL DEFAULT 0')
                except sqlite3.OperationalError:
                    pass  # Columna ya existe

                # Tabla Trades (Historial)
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS trades (
                        trade_id TEXT PRIMARY KEY,
                        symbol TEXT NOT NULL,
                        side TEXT NOT NULL,
                        price REAL NOT NULL,
                        qty REAL NOT NULL,
                        pnl REAL NOT NULL DEFAULT 0.0,
                        fee REAL NOT NULL DEFAULT 0.0,
                        executed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')

                # Tabla Estado del Grid (para recuperar la malla)
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS grid_state (
                        symbol TEXT PRIMARY KEY,
                        levels_json TEXT NOT NULL,
                        atr_value REAL NOT NULL,
                        center_price REAL NOT NULL,
                        modo_drenaje BOOLEAN NOT NULL DEFAULT 0,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')

                # Tabla Estado del Scanner
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS scanner_state (
                        id INTEGER PRIMARY KEY CHECK (id = 1),
                        ranking_json TEXT NOT NULL,
                        cycle_count INTEGER NOT NULL DEFAULT 0,
                        last_cycle_ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')

                # Tabla Config Overrides (AI Optimizer)
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS config_overrides (
                        param_key TEXT PRIMARY KEY,
                        param_value TEXT NOT NULL,
                        updated_by TEXT NOT NULL,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')

                # Tabla Blacklist (Símbolos Restringidos OKX)
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS blacklist (
                        symbol TEXT,
                        mode TEXT,
                        added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (symbol, mode)
                    )
                ''')

                # Tabla de Historial ML para configuraciones
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS ml_history (
                        session_id TEXT PRIMARY KEY,
                        symbol TEXT NOT NULL,
                        started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        analyzer_metrics TEXT NOT NULL,
                        math_params TEXT NOT NULL,
                        ai_factors TEXT NOT NULL,
                        pnl REAL DEFAULT 0.0,
                        win_rate REAL DEFAULT 0.0,
"""
database.py — Gestión de la Base de Datos Local en SQLite.
Patrón RAM-first + Flush periódico para evitar sobreescribir disco.
"""
import sqlite3
import json
import logging
import os
import threading
from typing import Dict, Any, List, Optional
from datetime import datetime

logger = logging.getLogger("UAO_Sclaping.database")

class Database:
    def __init__(self, db_path: str = "./data/uao_grid.db"):
        self.db_path = db_path
        self._lock = threading.Lock()
        
        # Crear directorio si no existe
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        self._init_db()

    def _get_connection(self):
        """Retorna una nueva conexión SQLite. IMPORTANTE: No compartir entre hilos."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        """Inicializa las tablas de la base de datos."""
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                # Tabla Balance
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS balance (
                        id INTEGER PRIMARY KEY CHECK (id = 1),
                        usdt_total REAL NOT NULL,
                        usdt_available REAL NOT NULL,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')

                # Tabla Posiciones
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS positions (
                        symbol TEXT PRIMARY KEY,
                        side TEXT NOT NULL,
                        qty REAL NOT NULL,
                        entry_price REAL NOT NULL,
                        opened_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')

                # Tabla Órdenes
                cursor.execute('''
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
                ''')
                
                # Migración para DB existente
                try:
                    cursor.execute('ALTER TABLE orders ADD COLUMN grid_level INTEGER NOT NULL DEFAULT 0')
                except sqlite3.OperationalError:
                    pass  # Columna ya existe

                # Tabla Trades (Historial)
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS trades (
                        trade_id TEXT PRIMARY KEY,
                        symbol TEXT NOT NULL,
                        side TEXT NOT NULL,
                        price REAL NOT NULL,
                        qty REAL NOT NULL,
                        pnl REAL NOT NULL DEFAULT 0.0,
                        fee REAL NOT NULL DEFAULT 0.0,
                        executed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')

                # Tabla Estado del Grid (para recuperar la malla)
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS grid_state (
                        symbol TEXT PRIMARY KEY,
                        levels_json TEXT NOT NULL,
                        atr_value REAL NOT NULL,
                        center_price REAL NOT NULL,
                        modo_drenaje BOOLEAN NOT NULL DEFAULT 0,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')

                # Tabla Estado del Scanner
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS scanner_state (
                        id INTEGER PRIMARY KEY CHECK (id = 1),
                        ranking_json TEXT NOT NULL,
                        cycle_count INTEGER NOT NULL DEFAULT 0,
                        last_cycle_ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')

                # Tabla Config Overrides (AI Optimizer)
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS config_overrides (
                        param_key TEXT PRIMARY KEY,
                        param_value TEXT NOT NULL,
                        updated_by TEXT NOT NULL,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')

                # Tabla Blacklist (Símbolos Restringidos OKX)
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS blacklist (
                        symbol TEXT,
                        mode TEXT,
                        added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (symbol, mode)
                    )
                ''')

                # Tabla de Historial ML para configuraciones
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS ml_history (
                        session_id TEXT PRIMARY KEY,
                        symbol TEXT NOT NULL,
                        started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        analyzer_metrics TEXT NOT NULL,
                        math_params TEXT NOT NULL,
                        ai_factors TEXT NOT NULL,
                        pnl REAL DEFAULT 0.0,
                        win_rate REAL DEFAULT 0.0,
                        profit_factor REAL DEFAULT 0.0,
                        total_trades INTEGER DEFAULT 0,
                        wins INTEGER DEFAULT 0,
                        gross_profit REAL DEFAULT 0.0,
                        gross_loss REAL DEFAULT 0.0
                    )
                ''')

                # Tabla Cache de Webhook del Grid (fallback offline)
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS grid_status_cache (
                        id INTEGER PRIMARY KEY CHECK (id = 1),
                        payload_json TEXT NOT NULL,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')

                conn.commit()
                logger.info(f"📁 Base de datos inicializada en {self.db_path}")

    # ── METODOS DE ESCRITURA BATCH / FLUSH ──

    def flush_state(self, balance_total: float, balance_available: float,
                    positions: List[Dict[str, Any]], orders: List[Dict[str, Any]],
                    grid_state: Optional[Dict[str, Any]] = None):
        """
        Guarda el estado principal (balance, posiciones, órdenes y grid) 
        en una sola transacción atómica.
        """
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                now = datetime.utcnow().isoformat()

                # 1. Update Balance
                cursor.execute('''
                    INSERT INTO balance (id, usdt_total, usdt_available, updated_at) 
                    VALUES (1, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET 
                        usdt_total=excluded.usdt_total,
                        usdt_available=excluded.usdt_available,
                        updated_at=excluded.updated_at
                ''', (balance_total, balance_available, now))

                # 2. Update Positions (Upsert para reducir I/O)
                current_symbols = [pos['symbol'] for pos in positions]
                for pos in positions:
                    cursor.execute('''
                        INSERT INTO positions (symbol, side, qty, entry_price, opened_at)
                        VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(symbol) DO UPDATE SET 
                            side=excluded.side,
                            qty=excluded.qty,
                            entry_price=excluded.entry_price
                    ''', (pos['symbol'], pos['side'], pos['qty'], pos['entry_price'], pos.get('opened_at', now)))
                
                # Eliminar posiciones que ya no existen
                if current_symbols:
                    placeholders = ','.join('?' * len(current_symbols))
                    cursor.execute(f"DELETE FROM positions WHERE symbol NOT IN ({placeholders})", current_symbols)
                else:
                    cursor.execute("DELETE FROM positions")

                # 3. Update Orders (Upsert para evitar fragmentación y bloqueos)
                # Obtener IDs actuales en RAM para limpiar huérfanas después del upsert
                current_order_ids = [ord_['order_id'] for ord_ in orders]
                for ord_ in orders:
                    cursor.execute('''
                        INSERT INTO orders (order_id, symbol, side, price, qty, status, reduce_only, updated_at, grid_level)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(order_id) DO UPDATE SET 
                            price=excluded.price,
                            qty=excluded.qty,
                            status=excluded.status,
                            updated_at=excluded.updated_at
                    ''', (ord_['order_id'], ord_['symbol'], ord_['side'], ord_['price'], 
                          ord_['qty'], ord_['status'], int(ord_.get('reduce_only', False)), now, ord_.get('grid_level', 0)))
                # Eliminar órdenes que ya no existen en RAM
                if current_order_ids:
                    placeholders = ','.join('?' * len(current_order_ids))
                    cursor.execute(f"DELETE FROM orders WHERE order_id NOT IN ({placeholders})", current_order_ids)
                else:
                    cursor.execute("DELETE FROM orders")

                # 4. Update Grid State
                if grid_state:
                    cursor.execute('''
                        INSERT INTO grid_state (symbol, levels_json, atr_value, center_price, modo_drenaje, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(symbol) DO UPDATE SET
                            levels_json=excluded.levels_json,
                            atr_value=excluded.atr_value,
                            center_price=excluded.center_price,
                            modo_drenaje=excluded.modo_drenaje,
                            updated_at=excluded.updated_at
                    ''', (grid_state['symbol'], json.dumps(grid_state['levels']), 
                          grid_state['atr_value'], grid_state['center_price'], 
                          int(grid_state.get('modo_drenaje', False)), now))

                conn.commit()

    def record_trade(self, trade_id: str, symbol: str, side: str, price: float, qty: float, pnl: float, fee: float, executed_at_ts: Optional[float] = None):
        """Registra un trade completado en el historial."""
        with self._lock:
            with self._get_connection() as conn:
                dt_str = datetime.utcfromtimestamp(executed_at_ts).isoformat() if executed_at_ts else datetime.utcnow().isoformat()
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT OR IGNORE INTO trades (trade_id, symbol, side, price, qty, pnl, fee, executed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (trade_id, symbol, side, price, qty, pnl, fee, dt_str))
                conn.commit()

    def create_ml_session(self, session_id: str, symbol: str, analyzer_metrics: dict, math_params: dict, ai_factors: dict):
        """Crea una sesión de ML para asociar un rendimiento empírico a un setup teórico."""
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO ml_history (session_id, symbol, analyzer_metrics, math_params, ai_factors)
                    VALUES (?, ?, ?, ?, ?)
                ''', (session_id, symbol, json.dumps(analyzer_metrics), json.dumps(math_params), json.dumps(ai_factors)))
                conn.commit()

    def update_ml_session_trade(self, symbol: str, pnl: float):
        """Actualiza el PnL, Win Rate y Profit Factor de la última sesión activa del símbolo."""
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT session_id, total_trades, wins, gross_profit, gross_loss, pnl 
                    FROM ml_history 
                    WHERE symbol=? 
                    ORDER BY started_at DESC LIMIT 1
                ''', (symbol,))
                row = cursor.fetchone()
                if row:
                    sid = row["session_id"]
                    t_trades = row["total_trades"] + 1
                    t_pnl = row["pnl"] + pnl
                    wins = row["wins"] + (1 if pnl > 0 else 0)
                    g_profit = row["gross_profit"] + (pnl if pnl > 0 else 0)
                    g_loss = row["gross_loss"] + (abs(pnl) if pnl < 0 else 0)
                    
                    win_rate = round((wins / t_trades) * 100, 2) if t_trades > 0 else 0.0
                    profit_factor = round((g_profit / g_loss), 2) if g_loss > 0 else (99.0 if g_profit > 0 else 0.0)
                    
                    cursor.execute('''
                        UPDATE ml_history 
                        SET total_trades=?, wins=?, gross_profit=?, gross_loss=?, pnl=?, win_rate=?, profit_factor=?
                        WHERE session_id=?
                    ''', (t_trades, wins, g_profit, g_loss, t_pnl, win_rate, profit_factor, sid))
                    conn.commit()

    def save_scanner_state(self, ranking: List[Dict[str, Any]], cycle_count: int):
        """Guarda el último resultado del scanner."""
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO scanner_state (id, ranking_json, cycle_count, last_cycle_ts)
                    VALUES (1, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        ranking_json=excluded.ranking_json,
                        cycle_count=excluded.cycle_count,
                        last_cycle_ts=excluded.last_cycle_ts
                ''', (json.dumps(ranking), cycle_count, datetime.utcnow().isoformat()))
                conn.commit()

    def get_scanner_state(self) -> Optional[List[Dict[str, Any]]]:
        """Obtiene el último resultado guardado del scanner."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT ranking_json FROM scanner_state WHERE id=1")
            row = cursor.fetchone()
            if row and row["ranking_json"]:
                try:
                    return json.loads(row["ranking_json"])
                except Exception:
                    return None
            return None

    # ── METODOS DE LECTURA (RECUPERACION) ──

    def load_balance(self) -> Optional[Dict[str, float]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT usdt_total, usdt_available FROM balance WHERE id=1")
            row = cursor.fetchone()
            if row:
                return {"usdt_total": row["usdt_total"], "usdt_available": row["usdt_available"]}
            return None

    def load_positions(self) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT symbol, side, qty, entry_price, opened_at FROM positions")
            return [dict(row) for row in cursor.fetchall()]

    def load_orders(self) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT order_id, symbol, side, price, qty, status, reduce_only, grid_level FROM orders")
            return [dict(row) for row in cursor.fetchall()]

    def load_grid_state(self, symbol: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT levels_json, atr_value, center_price, modo_drenaje FROM grid_state WHERE symbol=?", (symbol,))
            row = cursor.fetchone()
            if row:
                return {
                    "symbol": symbol,
                    "levels": json.loads(row["levels_json"]),
                    "atr_value": row["atr_value"],
                    "center_price": row["center_price"],
                    "modo_drenaje": bool(row["modo_drenaje"])
                }
            return None

    # ── AI OPTIMIZER OVERRIDES ──

    def update_config_overrides(self, params: Dict[str, Any], updated_by: str = "AI_Optimizer"):
        """Guarda configuraciones dinámicas generadas por la IA."""
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                now = datetime.utcnow().isoformat()
                for k, v in params.items():
                    cursor.execute('''
                        INSERT INTO config_overrides (param_key, param_value, updated_by, updated_at)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(param_key) DO UPDATE SET
                            param_value=excluded.param_value,
                            updated_by=excluded.updated_by,
                            updated_at=excluded.updated_at
                    ''', (k, str(v), updated_by, now))
                conn.commit()

    def get_config_overrides(self) -> Dict[str, str]:
        """Obtiene las configuraciones dinámicas guardadas."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT param_key, param_value FROM config_overrides")
            return {row["param_key"]: row["param_value"] for row in cursor.fetchall()}

    def get_recent_trades(self, limit: int = 5, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """Obtiene las operaciones más recientes."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if symbol:
                cursor.execute("SELECT * FROM trades WHERE symbol=? ORDER BY executed_at DESC LIMIT ?", (symbol, limit))
            else:
                cursor.execute("SELECT * FROM trades ORDER BY executed_at DESC LIMIT ?", (limit,))
            return [dict(row) for row in cursor.fetchall()]

    # ── BLACKLIST ──

    def agregar_a_lista_negra(self, symbol: str, modo: str):
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT OR IGNORE INTO blacklist (symbol, mode)
                    VALUES (?, ?)
                ''', (symbol, modo.lower()))
                conn.commit()
                logger.warning(f"🚫 Símbolo {symbol} añadido a lista negra de modo {modo}")

    def es_lista_negra(self, symbol: str, modo: str) -> bool:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM blacklist WHERE symbol=? AND mode=?", (symbol, modo.lower()))
            return cursor.fetchone() is not None

    def get_lista_negra(self) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT symbol, mode, added_at FROM blacklist ORDER BY added_at DESC")
            return [dict(row) for row in cursor.fetchall()]

    def remover_de_lista_negra(self, symbol: str, modo: str):
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM blacklist WHERE symbol=? AND mode=?", (symbol, modo.lower()))
                conn.commit()
                logger.info(f"✅ Símbolo {symbol} removido de la lista negra (modo {modo})")

    # ── GRID WEBHOOK CACHE (fallback offline) ──

    def save_grid_status_cache(self, payload: dict) -> None:
        """Persiste el último payload del webhook en SQLite como fallback offline."""
        with self._lock:
            with self._get_connection() as conn:
                conn.execute('''
                    INSERT INTO grid_status_cache (id, payload_json, updated_at)
                    VALUES (1, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        payload_json = excluded.payload_json,
                        updated_at   = excluded.updated_at
                ''', (json.dumps(payload), datetime.utcnow().isoformat()))
                conn.commit()

    def get_grid_status_cache(self) -> dict | None:
        """Retorna el último payload guardado localmente (fallback si la API está caída)."""
        with self._lock:
            with self._get_connection() as conn:
                row = conn.execute('SELECT payload_json FROM grid_status_cache WHERE id = 1').fetchone()
                if row:
                    return json.loads(row['payload_json'])
        return None
