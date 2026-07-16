"""
estado.py — Gestión de estado persistente del agente UAO_Sclaping.

Mantiene en memoria y en disco (JSON) el último ranking calculado,
timestamps del ciclo, y métricas de salud del agente.

No almacena credenciales ni información sensible del usuario.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

logger = logging.getLogger("UAO_Sclaping.estado")

# Ruta del archivo de estado (relativa al módulo)
_DEFAULT_STATE_PATH = Path(__file__).parent.parent / "uao_sclaping_estado.json"


class EstadoUAO:
    """
    Gestiona el estado persistente de la UAO_Sclaping.

    El archivo de estado guarda:
      - ultimo_ranking:  Lista de top símbolos con sus métricas
      - ultimo_ciclo_ts: Timestamp del último ciclo completado
      - ciclos_totales:  Contador de ciclos ejecutados
      - errores_totales: Contador de errores acumulados
    """

    def __init__(self, state_path: str = str(_DEFAULT_STATE_PATH)):
        self._path = Path(state_path)
        self._state: Dict[str, Any] = self._cargar()

    def _cargar(self) -> Dict[str, Any]:
        """Carga estado desde disco. Si no existe, retorna estado inicial vacío."""
        if self._path.exists():
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                logger.info("📂 Estado previo cargado: %s", self._path)
                return data
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("⚠️ Error leyendo estado previo: %s. Iniciando desde cero.", exc)

        return {
            "ultimo_ranking":   [],
            "ultimo_ciclo_ts":  None,
            "ciclos_totales":   0,
            "errores_totales":  0,
            "parametros": {},
            "perfiles_top":    [],   # Perfil detallado del Top N (freq, alto, bajo, patrón)
            "backtest_top2":   [],   # Resultados del backtest Long/Short del Top 2
            "simulacion_vivo": [],   # Historial de operaciones paper trading del ganador
            "balance_simulacion": 20.0,
            "simulacion_ordenes_abiertas": [],
            "simulacion_posicion_neta": 0.0,
            "precio_promedio_entrada": 0.0,
            "simulacion_simbolo_activo": "",
        }

    def guardar(self) -> None:
        """Persiste el estado actual al disco de forma atómica."""
        tmp_path = self._path.with_suffix(".json.tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(self._state, f, indent=2, ensure_ascii=False, default=str)
            os.replace(tmp_path, self._path)
        except OSError as exc:
            logger.error("❌ Error guardando estado: %s", exc)
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)

    def actualizar_ranking(self, df: pd.DataFrame, top_n: int = 10) -> None:
        """
        Actualiza el ranking con los resultados del último ciclo.

        Args:
            df:    DataFrame resultado de analizar_lote()
            top_n: Cuántos símbolos conservar en el ranking
        """
        if df.empty:
            return

        top = df.head(top_n)
        self._state["ultimo_ranking"] = top.to_dict(orient="records")
        self._state["ultimo_ciclo_ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self._state["ciclos_totales"] = self._state.get("ciclos_totales", 0) + 1
        self.guardar()

    def registrar_error(self) -> None:
        """Incrementa el contador de errores."""
        self._state["errores_totales"] = self._state.get("errores_totales", 0) + 1
        self.guardar()

    def actualizar_parametros(self, params: Dict[str, Any]) -> None:
        """Guarda los parámetros de configuración usados en el último ciclo."""
        self._state["parametros"] = params
        self.guardar()

    def actualizar_perfiles(self, perfiles: List[Dict[str, Any]]) -> None:
        """
        Persiste los perfiles detallados del Top N tras el perfilado profundo.

        Args:
            perfiles: Lista de dicts retornados por profiler.perfilar_top()
        """
        self._state["perfiles_top"] = perfiles
        self.guardar()

    def actualizar_backtest(self, resultados: List[Dict[str, Any]]) -> None:
        """
        Persiste los resultados del backtest del Top 2.

        Args:
            resultados: Lista de dicts retornados por backtester.backtest_top10()
        """
        self._state["backtest_top2"] = resultados
        self.guardar()

    def actualizar_simulacion(self, historial: List[Dict[str, Any]], balance: float, 
                              ordenes_abiertas: List[Dict[str, Any]] = None, 
                              posicion_neta: float = 0.0, 
                              precio_promedio: float = 0.0,
                              simbolo_activo: str = "") -> None:
        """
        Persiste el historial de operaciones del simulador en vivo, balance y estado de ejecución.
        """
        self._state["simulacion_vivo"] = historial
        self._state["balance_simulacion"] = balance
        if ordenes_abiertas is not None:
            self._state["simulacion_ordenes_abiertas"] = ordenes_abiertas
        self._state["simulacion_posicion_neta"] = posicion_neta
        self._state["precio_promedio_entrada"] = precio_promedio
        self._state["simulacion_simbolo_activo"] = simbolo_activo
        self.guardar()

    @property
    def ultimo_ranking(self) -> List[Dict]:
        return self._state.get("ultimo_ranking", [])

    @property
    def ciclos_totales(self) -> int:
        return self._state.get("ciclos_totales", 0)

    @property
    def errores_totales(self) -> int:
        return self._state.get("errores_totales", 0)

    @property
    def ultimo_ciclo_ts(self) -> Optional[str]:
        return self._state.get("ultimo_ciclo_ts")

    @property
    def perfiles_top(self) -> List[Dict[str, Any]]:
        return self._state.get("perfiles_top", [])

    @property
    def backtest_top2(self) -> List[Dict[str, Any]]:
        return self._state.get("backtest_top2", [])

    @property
    def simulacion_vivo(self) -> List[Dict[str, Any]]:
        return self._state.get("simulacion_vivo", [])

    @property
    def balance_simulacion(self) -> float:
        return self._state.get("balance_simulacion", 20.0)

    @property
    def simulacion_ordenes_abiertas(self) -> List[Dict[str, Any]]:
        return self._state.get("simulacion_ordenes_abiertas", [])

    @property
    def simulacion_posicion_neta(self) -> float:
        return self._state.get("simulacion_posicion_neta", 0.0)

    @property
    def precio_promedio_entrada(self) -> float:
        return self._state.get("precio_promedio_entrada", 0.0)
