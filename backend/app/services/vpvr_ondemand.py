# backend/app/services/vpvr_ondemand.py
"""
Módulo: vpvr_ondemand.py (Volume Profile - Sob Pedido)
Especificação Técnica v1.0 - Secção 4

REGRAS RÍGIDAS:
1. Nunca corre automaticamente no cron diário (custo computacional elevado).
2. Só é invocado manualmente para ativos selecionados após alerta de sweep.
3. Bloqueado estritamente para ativos Forex (EURUSD=X, USDJPY=X, etc.) por falta de volume real (OTC).
"""

import logging
from typing import Dict, Any, List, Optional
import pandas as pd
import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

FOREX_TICKERS = {
    "EURUSD=X", "GBPUSD=X", "USDJPY=X", "USDCHF=X", 
    "AUDUSD=X", "USDCAD=X", "EURGBP=X", "EURJPY=X"
}

def calculate_vpvr(symbol: str, df: pd.DataFrame, num_bins: int = 50) -> Optional[Dict[str, Any]]:
    """
    Calcula o Volume Profile (POC, HVN, LVN) sob pedido para um ativo com volume real.
    Retorna None se o ativo for Forex.
    """
    if symbol in FOREX_TICKERS:
        logging.warning(f"🚫 [VPVR] Cálculo de Volume Profile BLOQUEADO para {symbol} (Ativo Forex sem volume real OTC).")
        return None
        
    if df is None or df.empty or 'volume' not in df.columns or df['volume'].sum() == 0:
        logging.warning(f"⚠️ [VPVR] Dados de volume ausentes ou nulos para {symbol}.")
        return None
        
    df = df.copy()
    df.columns = [c.lower() for c in df.columns]
    
    price_min = df['low'].min()
    price_max = df['high'].max()
    
    if price_min == price_max:
        return None
        
    # Criar faixas de preço (price bins)
    bins = np.linspace(price_min, price_max, num_bins + 1)
    bin_volumes = np.zeros(num_bins)
    
    # Distribuir o volume proporcionalmente pela amplitude da vela (High-Low)
    for _, row in df.iterrows():
        candle_low = row['low']
        candle_high = row['high']
        candle_vol = row['volume']
        
        if candle_high == candle_low:
            idx = np.clip(np.digitize(candle_low, bins) - 1, 0, num_bins - 1)
            bin_volumes[idx] += candle_vol
        else:
            # Sobreposição proporcional
            for i in range(num_bins):
                bin_bottom = bins[i]
                bin_top = bins[i+1]
                overlap = max(0, min(candle_high, bin_top) - max(candle_low, bin_bottom))
                if overlap > 0:
                    fraction = overlap / (candle_high - candle_low)
                    bin_volumes[i] += candle_vol * fraction

    # Identificar POC (Point of Control - Nível de maior volume)
    poc_idx = np.argmax(bin_volumes)
    poc_price = (bins[poc_idx] + bins[poc_idx+1]) / 2.0
    
    # Identificar HVNs (High Volume Nodes - Top 15% maiores volumes) e LVNs (Low Volume Nodes)
    volume_threshold_hvn = np.percentile(bin_volumes, 85)
    volume_threshold_lvn = np.percentile(bin_volumes, 15)
    
    hvn_nodes = []
    lvn_nodes = []
    
    for i in range(num_bins):
        mid_price = (bins[i] + bins[i+1]) / 2.0
        vol = bin_volumes[i]
        if vol >= volume_threshold_hvn:
            hvn_nodes.append(round(mid_price, 4))
        elif vol <= volume_threshold_lvn and vol > 0:
            lvn_nodes.append(round(mid_price, 4))
            
    return {
        "symbol": symbol,
        "num_bins": num_bins,
        "poc_price": round(poc_price, 4),
        "poc_volume": float(bin_volumes[poc_idx]),
        "hvn_nodes": hvn_nodes,
        "lvn_nodes": lvn_nodes,
        "total_volume": float(df['volume'].sum()),
        "status": "VPVR_CALCULATED"
    }
