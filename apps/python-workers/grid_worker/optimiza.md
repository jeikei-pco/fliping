La naturaleza de los Símbolos en OKX
En OKX, los nombres de los símbolos no cambian entre el entorno real y el de pruebas. Un contrato perpetuo se llamará exactamente igual (por ejemplo, BTC-USDT-SWAP) tanto en Live como en Demo.

Por lo tanto, la alineación no depende de renombrar el símbolo, sino de enrutar la petición al entorno correcto.

2. Alineación de Datos (DTOs) por Módulo
Para que el ecosistema sea coherente cuando la variable sandbox = True, todos los módulos deben utilizar la misma instancia del cliente de conexión configurada para la red de pruebas:

Screener (consistency_screener.py): Cuando busque los mejores 20 tokens por volumen o volatilidad, debe solicitar los tickers a la URL del sandbox. Los precios y volúmenes en demo de OKX son simulados y difieren del mercado real.

Optimizador de IA (ai_optimizer.py): Las velas (OHLCV) que se le envíen al LLM para calcular los parámetros del Grid deben extraerse estrictamente del entorno demo. Si le envías velas reales para que optimice un bot que va a operar en demo, los rangos superior e inferior calculados fallarán.

Backtest (fast_backtester.py): Debe ejecutar su simulación vectorizada sobre la misma data OHLCV de demo que consumió el optimizador, garantizando que el profit simulado coincida con lo que el bot hará en la red de pruebas.

3. Cómo garantizar la alineación en el código
Si estás utilizando la librería CCXT en tu worker de Python (que es el estándar de la industria y lo que usas en el backend de Node.js), la alineación se logra forzando el modo sandbox en el objeto global de conexión antes de pasarlo a los módulos:

Python
import ccxt

# Instanciación global
exchange = ccxt.okx({
    'apiKey': 'TU_API_KEY',
    'secret': 'TU_SECRET',
    'password': 'TU_PASSWORD',
})

# ALINEACIÓN ESTRICTA: Si es modo demo, se activa el sandbox.
# Esto enruta automáticamente Screener, Backtest y Optimizador a los DTOs de prueba.
if MODO_DEMO:
    exchange.set_sandbox_mode(True) 
Si estás usando llamadas directas (requests/websockets) en okx_ws.py, debes asegurarte de que el header x-simulated-trading: 1 se inyecte en todas las peticiones REST y que la URL del WebSocket apunte a wss://[wspap.okx.com:8443/ws/v5/public](https://wspap.okx.com:8443/ws/v5/public) (el endpoint de demo) en lugar de la URL de producción.