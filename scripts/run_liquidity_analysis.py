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
from app.services.alert_service import (
    format_sweep_alert,
    send_alert_notification,
    send_digest_email_alert,
    check_quarantine_sla_violations
)
from app.services.notion_sync_service import publish_liquidity_signal_to_notion
from app.services.notion_anomalies_sync_service import sync_anomalies_quarantine_to_notion

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

def get_watchlist_for_analysis() -> list:
    """Obtém dinamicamente todos os ativos OHLCV ativos do indicators_catalog no MySQL com fallback.
    
    Exclui séries FRED_API (ex: T10YIE, IRLTLT01ITM156N, VSTOXX) que não possuem
    dados OHLCV — o motor de sweep necessita de pavio (high/low/open/close) para operar.
    """
    try:
        from app.database import engine
        with engine.connect() as conn:
            sql = text("""
                SELECT ticker FROM indicators_catalog
                WHERE is_active = TRUE
                AND data_provider != 'FRED_API'
            """)
            rows = conn.execute(sql).fetchall()
            if rows:
                tickers = [r[0] for r in rows if r[0]]
                logging.info(f"✅ Watchlist do Motor de Liquidez carregada da DB ({len(tickers)} ativos com OHLCV — séries FRED_API excluídas).")
                return tickers
    except Exception as e:
        logging.warning(f"⚠️ Erro ao consultar indicators_catalog na DB ({e}). Usando lista fallback...")
    
    return [
        "BZ=F", "GC=F", "CL=F", "HG=F", "CC=F", "EURUSD=X", "GBPUSD=X", "USDJPY=X", 
        "^GSPC", "^NDX", "^SOX", "^GDAXI", "DX-Y.NYB", "TLT", "^TNX", "^TYX", "DGS2",
        "NVDA", "TSM", "ASML", "BABA", "BBVA", "JPM", "MU", "O", "DAL", "F", "ENPH", "NKE", "STLA"
    ]

def run_liquidity_analysis_pipeline():
    logging.info("🎯 Executando Módulo de Análise de Liquidez e Sweeps (Produção Validada)...")
    watchlist = get_watchlist_for_analysis()
    alerts_triggered = 0
    recent_email_alerts = []

    for symbol in watchlist:
        try:
            # Buscar histórico de produção validada no MySQL
            try:
                with engine.connect() as conn:
                    df = pd.read_sql(
                        text("""
                            SELECT timestamp, open_val as open, high_val as high, low_val as low, value as close, volume
                            FROM indicator_values
                            WHERE symbol = :symbol
                            ORDER BY timestamp ASC
                        """),
                        conn,
                        params={"symbol": symbol}
                    )
            except Exception as sql_err:
                logging.warning(f"⚠️ Falha ao ler MySQL para [{symbol}]: {sql_err}. Tentando yfinance...")
                df = pd.DataFrame()

            # Fallback para yfinance se o MySQL tiver menos de 60 sessões
            if df.empty or len(df) < 60:
                df = yf.download(symbol, period="3mo", interval="1d", progress=False)
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                df = df.reset_index()
                df.columns = [str(c).lower() for c in df.columns]
                if 'date' in df.columns:
                    df.rename(columns={'date': 'timestamp'}, inplace=True)

            if df.empty or len(df) < 60:
                logging.info(f"⏭️ [{symbol}] Ignorado por possuir apenas {len(df)} sessões (mínimo 60 para ATR_60).")
                continue

            # 1. Analisar Sweeps no Liquidity Engine
            sweeps = analyze_liquidity_sweeps(symbol, df, k_factor=1.5)
            
            for sweep in sweeps:
                if sweep.get("status") == "LIQUIDEZ_CONSUMIDA":
                    # Disparar alerta por e-mail apenas se o sweep ocorreu nas últimas 3 sessões (evita re-enviar sweeps antigos de meses atrás)
                    sweep_idx = sweep.get("sweep_index", len(df) - 1)
                    is_recent = sweep_idx >= (len(df) - 3)
                    
                    if is_recent:
                        alert_msg = format_sweep_alert(sweep)
                        recent_email_alerts.append(alert_msg)
                        
                        # Notificar Discord/Telegram sem disparar emails individuais múltiplos
                        send_alert_notification(alert_msg, send_email=False)
                        alerts_triggered += 1
                    else:
                        logging.info(f"ℹ️ [HISTÓRICO SIMULADO] Sweep antigo de {sweep.get('timestamp')} para {symbol} ignorado para alertas de e-mail.")
                    
                    # Publicar no Notion (Database 'Sinais de Liquidez' - possui desduplicação própria)
                    publish_liquidity_signal_to_notion(sweep)
                    
                    # 2. Se o ativo tiver volume real (não Forex), calcular VPVR On-Demand
                    if not sweep.get("is_forex", False):
                        vpvr_res = calculate_vpvr(symbol, df)
                        if vpvr_res:
                            logging.info(f"📊 VPVR On-Demand [{symbol}]: POC=${vpvr_res['poc_price']} | HVNs={vpvr_res['hvn_nodes'][:3]}")

        except Exception as e:
            logging.error(f"Erro ao analisar liquidez para {symbol}: {e}")

    # 3. Monitor de SLA de Quarentena (Alerta de dados em falta > 12h)
    sla_violations = check_quarantine_sla_violations(hours_threshold=12)

    # 4. Sync da Quarentena de Anomalias para o Notion (Database 'Quarentena de Anomalias — Claude')
    try:
        sync_anomalies_quarantine_to_notion()
    except Exception as sync_err:
        logging.error(f"⚠️ Erro ao sincronizar anomalias para o Notion: {sync_err}")

    # 4. REGRA DE AGRUPAMENTO: Disparar EXATAMENTE 1 E-MAIL CONSOLIDADO ao final da execução se houverem alertas
    if recent_email_alerts:
        digest_title = f"Liquidity & Market Alert Report ({len(recent_email_alerts)} Events)"
        logging.info(f"📤 Enviando 1 ÚNICO e-mail de resumo consolidado com {len(recent_email_alerts)} eventos...")
        send_digest_email_alert(digest_title, recent_email_alerts)

    if sla_violations >= 0:
        logging.info(f"🎉 Pipeline de Liquidez Concluído: {alerts_triggered} Alertas Analíticos Encontrados | {sla_violations} Violações SLA Quarentena.")
    else:
        logging.warning(f"⚠️ Pipeline de Liquidez Concluído: {alerts_triggered} Alertas Analíticos Encontrados | SLA Quarentena NÃO VERIFICADO (Erro DB).")




if __name__ == "__main__":
    try:
        run_liquidity_analysis_pipeline()
    finally:
        try:
            engine.dispose()
        except Exception:
            pass

