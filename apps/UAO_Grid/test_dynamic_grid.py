import logging
import time
from core.simulador import GridSimuladorEnVivo, SimulatedProvider
from core.engine import GridEngine
from core.okx_connector import inicializar_okx

# Configurar logging para ver la salida detallada
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

class MockGlobalTarget:
    def get_targets(self):
        # Simular un espaciado dinámico
        return [{"symbol": "BTC/USDT:USDT", "rango_pct_promedio": 0.005}]
    def get_targets_version(self):
        return 1

class MockEstadoGlobal:
    def __init__(self):
        self.simulacion_vivo = []
        self.balance_simulacion = 20.0
    def actualizar_simulacion(self, ops, equity):
        self.simulacion_vivo = ops
        self.balance_simulacion = equity

def test_simulacion():
    exchange = inicializar_okx()
    target = MockGlobalTarget()
    estado = MockEstadoGlobal()
    
    # Inicializar GridSimuladorEnVivo, que ahora internamente crea GridEngine y SimulatedProvider
    sim = GridSimuladorEnVivo(exchange, "BTC/USDT:USDT", capital=20.0, n_ops=5)
    
    print("🚀 Iniciando prueba de Grid Dinámico con Arquitectura Desacoplada...")
    
    try:
        sim.ejecutar_continuo(target, estado)
    except KeyboardInterrupt:
        print("\n✅ Prueba terminada por el usuario.")

if __name__ == "__main__":
    test_simulacion()
