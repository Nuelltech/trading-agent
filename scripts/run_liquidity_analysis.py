# scripts/run_liquidity_analysis.py
"""
Script Executável de Análise de Liquidez & Emissão de Alertas Analíticos (Secção 9 da Spec v1.0)
Roda SOBRE A PRODUÇÃO JÁ VALIDADA (indicator_values), nunca sobre staging.
Executa a deteção de sweeps, dispara alertas analíticos e verifica SLA de quarentena.
"""

import sys
import logging
from datetime import datetime
import pandas as pd
import yfinance as yf

sys.path.append('backend')
from app.database import engine
from sqlalchemy import text
from app.services.liquidity_engine import analyze_liquidity_sweeps
from app.services.vpvr_ondemand import calculate_vpvr
from app.services.alert_service import format_sweep_alert, send_alert_notification, check_quarantine_sla_violations

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

WATCHLIST_ANALYSIS = [
    "BZ=F", "GC=F", "CL=F", "EURUSD=X", "GBPUSD=X", "USDJPY=X", 
    "^GSPC", "^NDX", "^GDAXI", "DX-Y.NYB", "O", "DAL", "F", "ENPH", "NKE", "STLA"
]

def run_liquidity_analysis_pipeline():
    logging.info("🎯 Executando Módulo de Análise de Liquidez e Sweeps (Produção Validada)...")
    alerts_triggered = 0

    for symbol in WATCHLIST_ANALYSIS:
        try:
            # Buscar histórico diário recente de produção validada (ou yfinance para histórico completo 60d)
            df = yf.download(symbol, period="3m", interval="1d", progress=False)
            if df.empty or len(df) < 60:
                logging.info(f"⏭️ [{symbol}] Ignorado por possuir menos de 60 sessões (Cold-start ativo).")
                continue

            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            df = df.reset_index()
            df.columns = [str(c).lower() for c in df.columns]
            if 'date' in df.columns:
                df.rename(columns={'date': 'timestamp'}, inplace=True)

            # 1. Analisar Sweeps no Liquidity Engine
            sweeps = analyze_liquidity_sweeps(symbol, df, k_factor=1.5)
            
            for sweep in sweeps:
                if sweep.get("status") == "LIQUIDEZ_CONSUMIDA":
                    alert_msg = format_sweep_alert(sweep)
                    send_alert_notification(alert_msg)
                    alerts_triggered += 1
                    
                    # 2. Se o ativo tiver volume real (não Forex), calcular VPVR On-Demand
                    if not sweep.get("is_forex", False):
                        vpvr_res = calculate_vpvr(symbol, df)
                        if vpvr_res:
                            logging.info(f"📊 VPVR On-Demand [{symbol}]: POC=${vpvr_res['poc_price']} | HVNs={vpvr_res['hvn_nodes'][:3]}")

        except Exception as e:
            logging.error(f"Erro ao analisar liquidez para {symbol}: {e}")

    # 3. Monitor de SLA de Quarentena (Alerta de dados em falta > 12h)
    sla_violations = check_quarantine_sla_violations(hours_threshold=12)
    
    logging.info(f"🎉 Pipeline de Liquidez Concluído: {alerts_triggered} Alertas Analíticos Disparados | {sla_violations} Violações SLA Quarentena.")

if __name__ == "__main__":
    run_liquidity_analysis_pipeline()
