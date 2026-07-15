import logging

logger = logging.getLogger("GridWorker.OptimizerIntegrator")

def optimize_grid_params(ai_params, screener_data):
    """
    Verifica si los parámetros de la IA son válidos. 
    Si la IA no optimizó, calcula una alternativa basada en el resultado del screener de consistencia.
    """
    
    # 1. Validación de existencia
    if ai_params:
        logger.info(f"Parámetros de IA recibidos para {screener_data.get('symbol')}. Validando...")
        return ai_params
    
    logger.warning(f"IA no optimizó para {screener_data.get('symbol')}. Aplicando fallback de consistencia.")
    
    # Extraer métricas del screener (con valores por defecto seguros)
    avg_body = screener_data.get('avg_body_pct', 0.5)
    std_dev = screener_data.get('std_dev', 0.1)
    quality = screener_data.get('quality', 0.5)
    
    # 2. Desplazamiento (Grid Spacing)
    # Si la calidad es baja (muchas mechas), ampliamos el espaciado un 20% extra
    base_spacing = avg_body * 0.8
    quality_modifier = 1.0 if quality > 0.5 else 1.2
    grid_spacing = max(0.15, round(base_spacing * quality_modifier, 2))
    
    # 3. Líneas de Malla (Grid Lines)
    # A mayor volatilidad total, más líneas para distribuir el capital
    volatility_score = avg_body + std_dev
    if volatility_score > 2.0:
        grid_lines = 20
    elif volatility_score > 1.0:
        grid_lines = 14
    else:
        grid_lines = 8
        
    # 4. Apalancamiento (Leverage)
    # Inversamente proporcional al tamaño de la vela. Max 20x, Min 2x.
    leverage = min(20.0, max(2.0, round(15.0 / (avg_body if avg_body > 0 else 0.5))))
    
    # 5. Frecuencia de Recálculo (Minutos)
    # Si el par es muy errático (alta desviación), recalculamos la malla más rápido
    if std_dev > 0.5:
        recalc_minutes = 60   # Cada hora (12 velas de 5m)
    elif std_dev > 0.2:
        recalc_minutes = 120  # Cada 2 horas (24 velas de 5m)
    else:
        recalc_minutes = 240  # Cada 4 horas (48 velas de 5m)
        
    return {
        "grid_spacing_factor": grid_spacing,
        "grid_lines": grid_lines,
        "leverage": int(leverage),
        "recalculate_every_minutes": recalc_minutes,
        "direction": screener_data.get('trend', 'neutral'),
        "source": "consistency_screener_fallback"
    }
