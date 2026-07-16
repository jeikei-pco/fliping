# ─────────────────────────────────────────────────────────────────────────────
# UAO_Sclaping — Dockerfile
#
# Agente de filtrado cuantitativo de futuros USDT en OKX.
# Optimizada para scalping de micro-rangos (Modo Simulación).
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.11-slim

WORKDIR /app

# ── Instalar dependencias ──────────────────────────────────────────────────────
# Asumimos que el build se hace desde la carpeta raíz del proyecto
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Copiar código de la UAO ────────────────────────────────────────────────────
COPY . .

# Variables de entorno por defecto
ENV SCAN_TIMEFRAME=5m
ENV SCAN_TOP_N=10
ENV SCAN_MIN_VOLUME_USDT=1000000

CMD ["python", "simulation_main.py"]
