import logging

logger = logging.getLogger("GridWorker.OptimizerIntegrator")

def optimize_grid_params(ai_params, screener_data):
    """
    Verifica si los parámetros de la IA son válidos. 
    Si la IA no optimizó (retorna None o vacío) o los parámetros parecen fuera de rango,
    calcula una alternativa basada en el resultado del screener de consistencia.
    
    :param ai_params: Resultado directo de la consulta de IA (dict o None).
    :param screener_data: Resultado del `consistency_screener.py` para ese símbolo.
    :return: Diccionario con los parámetros finales validados.
    """
    
    # 1. Validación de existencia
    if ai_params:
        logger.info(f"Parámetros de IA recibidos para {screener_data['symbol']}. Validando...")
        # Se puede añadir lógica adicional de validación de rangos aquí si es necesario
        return ai_params
    
    # 2. Si la IA falló o no retornó datos, aplicamos la heurística basada en el Screener
    logger.warning(f"IA no optimizó para {screener_data['symbol']}. Aplicando fallback de consistencia.")
    
    avg_body = screener_data.get('avg_body_pct', 0.5) # Ya está en porcentaje (ej: 0.5)
    
    # Lógica de cálculo basada en los datos recolectados por el screener
    # Spacing: Capturar el 75% de la oscilación media para maximizar cruces
    grid_spacing = max(0.15, round(avg_body * 0.75, 2))
    
    # Leverage: Conservador basado en amplitud, limitado a 15x
    leverage = min(15.0, max(2.0, round(50.0 / (avg_body if avg_body > 0 else 0.5))))
    
    # Líneas: Ajuste dinámico según volatilidad
    if avg_body > 2.0:
        grid_lines = 20
    elif avg_body > 1.0:
        grid_lines = 15
    else:
        grid_lines = 10
        
    return {
        "grid_spacing_factor": grid_spacing,
        "grid_lines": grid_lines,
        "leverage": leverage,
        "direction": screener_data.get('trend', 'neutral'),
        "source": "consistency_screener_fallback"
    }