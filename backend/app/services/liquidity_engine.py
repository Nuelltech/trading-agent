# backend/app/services/liquidity_engine.py
"""
Módulo: liquidity_engine.py (Deteção de Zonas de Liquidez Institucional e Sweeps)
Especificação Técnica v1.0 - Secção 3

NOTA DE LATÊNCIA OBRIGATÓRIA:
Um swing fractal só é confirmado 3 sessões depois de ocorrer (necessita do fecho de t+3).
O sistema mapeia liquidez estrutural madura, não topos/fundos em formação em tempo real.
Esta é uma opção deliberada de qualidade para eliminar ruído intradiário.
"""

import logging
from typing import Dict, Any, List, Optional, Tuple
import pandas as pd
import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

FOREX_TICKERS = {
    "EURUSD=X", "GBPUSD=X", "USDJPY=X", "USDCHF=X", 
    "AUDUSD=X", "USDCAD=X", "EURGBP=X", "EURJPY=X"
}

def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Calcula o Average True Range (ATR) para um determinado período"""
    high = df['high']
    low = df['low']
    close = df['close'].shift(1)
    
    tr1 = high - low
    tr2 = (high - close).abs()
    tr3 = (low - close).abs()
    
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(window=period, min_periods=min(14, period)).mean()
    return atr

def is_swing_high(df: pd.DataFrame, t: int, n: int = 3) -> bool:
    """Deteção Fractal de Swing High (N=3 sessões de cada lado)"""
    if t < n or t + n >= len(df):
        return False
    window = df['high'].iloc[t-n:t+n+1]
    current = df['high'].iloc[t]
    prev_max = df['high'].iloc[t-n:t].max()
    next_max = df['high'].iloc[t+1:t+n+1].max()
    
    return (current == window.max()) and (current > prev_max) and (current > next_max)

def is_swing_low(df: pd.DataFrame, t: int, n: int = 3) -> bool:
    """Deteção Fractal de Swing Low (N=3 sessões de cada lado)"""
    if t < n or t + n >= len(df):
        return False
    window = df['low'].iloc[t-n:t+n+1]
    current = df['low'].iloc[t]
    prev_min = df['low'].iloc[t-n:t].min()
    next_min = df['low'].iloc[t+1:t+n+1].min()
    
    return (current == window.min()) and (current < prev_min) and (current < next_min)

def detect_swing_fractals(df: pd.DataFrame, n: int = 3) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Retorna listas de swing highs e swing lows mapeados no histórico"""
    swing_highs = []
    swing_lows = []
    
    for t in range(n, len(df) - n):
        row = df.iloc[t]
        date_str = str(row['timestamp'])
        
        if is_swing_high(df, t, n):
            swing_highs.append({
                "price": float(row['high']),
                "date": date_str,
                "type": "SWING_HIGH",
                "index": t,
                "status": "ATIVO"
            })
            
        if is_swing_low(df, t, n):
            swing_lows.append({
                "price": float(row['low']),
                "date": date_str,
                "type": "SWING_LOW",
                "index": t,
                "status": "ATIVO"
            })
            
    return swing_highs, swing_lows

def analyze_liquidity_sweeps(symbol: str, df: pd.DataFrame, k_factor: float = 1.5) -> List[Dict[str, Any]]:
    """
    Executa a análise completa de zonas de liquidez e deteção de sweeps simétricos (Topo e Fundo).
    Aplica as salvaguardas de ATR Cap e Cold-Start.
    """
    dias_disponiveis = len(df)
    
    # 1. Salvaguarda Obrigatoria de Cold-Start (< 60 sessões)
    if dias_disponiveis < 60:
        logging.warning(f"⚠️ [{symbol}] Cold-start ativado: apenas {dias_disponiveis} sessões (mínimo 60 para ATR_60). Sweeps ignorados.")
        return [{
            "symbol": symbol,
            "status": "ATR_60_INCOMPLETO",
            "status_atr60": "ATR_60_INCOMPLETO",
            "sweep_detected": False,
            "threshold": None,
            "message": f"Histórico insuficiente ({dias_disponiveis}/60 sessões)"
        }]
        
    # Garante nomes de colunas em minúsculas
    df = df.copy()
    df.columns = [c.lower() for c in df.columns]
    
    # 2. Cálculo do ATR Dinâmico com Teto (Cap)
    df['atr14'] = calculate_atr(df, 14)
    df['atr60'] = calculate_atr(df, 60)
    
    # Cap do ATR60: min(atr14, 1.5 * atr60)
    df['atr60_capped'] = df[['atr14', 'atr60']].apply(lambda r: min(r['atr14'], 1.5 * r['atr60']), axis=1)
    df['threshold'] = k_factor * df['atr60_capped']
    
    # 3. Deteção dos Fractais
    swing_highs, swing_lows = detect_swing_fractals(df, n=3)
    
    # Exceção Forex para VPVR
    is_forex = symbol in FOREX_TICKERS
    
    sweeps_detected = []
    consumed_highs = set()
    consumed_lows = set()
    
    # Avaliar as últimas sessões em busca de Sweeps
    for i in range(60, len(df)):
        current = df.iloc[i]
        timestamp = str(current['timestamp'])
        open_val = float(current['open'])
        high_val = float(current['high'])
        low_val = float(current['low'])
        close_val = float(current['close'])
        thresh = float(current['threshold'])
        atr14_val = float(current['atr14']) if not pd.isna(current['atr14']) else 0.0
        atr60_capped_val = float(current['atr60_capped']) if not pd.isna(current['atr60_capped']) else 0.0
        
        upper_wick = high_val - max(open_val, close_val)
        lower_wick = min(open_val, close_val) - low_val
        
        # A. Deteção de Sweep de Topo (Buy Stop Hunt / Exaustão Compradora)
        active_highs = [sh for sh in swing_highs if sh['index'] < i and sh['index'] not in consumed_highs]
        for sh in active_highs:
            prev_high = sh['price']
            if high_val > prev_high and close_val < prev_high and upper_wick >= thresh:
                consumed_highs.add(sh['index'])
                sweeps_detected.append({
                    "symbol": symbol,
                    "event_type": "SWEEP_TOPO",
                    "timestamp": timestamp,
                    "level_broken": round(prev_high, 4),
                    "wick_size": round(upper_wick, 4),
                    "threshold": round(thresh, 4),
                    "threshold_ratio": round(upper_wick / thresh, 2),
                    "k_factor": k_factor,
                    "atr14": round(atr14_val, 4),
                    "atr60_capped": round(atr60_capped_val, 4),
                    "is_forex": is_forex,
                    "status": "LIQUIDEZ_CONSUMIDA",
                    "status_atr60": "COMPLETO",
                    "sweep_detected": True,
                    "details": f"Sweep de Topo: Resistência {prev_high:.4f} perfurada com pavio {upper_wick:.4f} (>= {thresh:.4f})"
                })

        # B. Deteção de Sweep de Fundo (Sell Stop Hunt / Exaustão Vendedora)
        active_lows = [sl for sl in swing_lows if sl['index'] < i and sl['index'] not in consumed_lows]
        for sl in active_lows:
            prev_low = sl['price']
            if low_val < prev_low and close_val > prev_low and lower_wick >= thresh:
                consumed_lows.add(sl['index'])
                sweeps_detected.append({
                    "symbol": symbol,
                    "event_type": "SWEEP_FUNDO",
                    "timestamp": timestamp,
                    "level_broken": round(prev_low, 4),
                    "wick_size": round(lower_wick, 4),
                    "threshold": round(thresh, 4),
                    "threshold_ratio": round(lower_wick / thresh, 2),
                    "k_factor": k_factor,
                    "atr14": round(atr14_val, 4),
                    "atr60_capped": round(atr60_capped_val, 4),
                    "is_forex": is_forex,
                    "status": "LIQUIDEZ_CONSUMIDA",
                    "status_atr60": "COMPLETO",
                    "sweep_detected": True,
                    "details": f"Sweep de Fundo: Suporte {prev_low:.4f} perfurado com pavio {lower_wick:.4f} (>= {thresh:.4f})"
                })
                
    return sweeps_detected
