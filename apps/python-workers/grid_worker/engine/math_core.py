import pandas as pd
import numpy as np

def calculate_keltner_channels(df: pd.DataFrame, window=20, atr_window=10, multiplier=2.0):
    """
    Calcula Canales de Keltner
    Middle Line = EMA(Close, window)
    Upper/Lower = Middle +/- (ATR(atr_window) * multiplier)
    """
    if len(df) < window:
        return None, None, None
        
    df = df.copy()
    
    # Calcular True Range (TR)
    df['prev_close'] = df['close'].shift(1)
    df['tr1'] = df['high'] - df['low']
    df['tr2'] = abs(df['high'] - df['prev_close'])
    df['tr3'] = abs(df['low'] - df['prev_close'])
    df['tr'] = df[['tr1', 'tr2', 'tr3']].max(axis=1)
    
    # Average True Range (ATR)
    df['atr'] = df['tr'].ewm(span=atr_window, adjust=False).mean()
    
    # Middle Line (EMA)
    df['middle'] = df['close'].ewm(span=window, adjust=False).mean()
    
    # Upper y Lower
    df['upper'] = df['middle'] + (df['atr'] * multiplier)
    df['lower'] = df['middle'] - (df['atr'] * multiplier)
    
    last_row = df.iloc[-1]
    return last_row['upper'], last_row['middle'], last_row['lower']

def calculate_cv(df: pd.DataFrame, window=20):
    """
    Calcula el Coeficiente de Variación (CV = std / mean) del precio de cierre
    """
    if len(df) < window:
        return None
        
    recent_closes = df['close'].tail(window)
    mean = recent_closes.mean()
    std = recent_closes.std()
    
    if mean == 0:
        return 0
        
    cv = std / mean
    return cv
