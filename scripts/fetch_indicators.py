# scripts/fetch_indicators.py
"""
Script de ingestão de Indicadores e Ativos de Mercado
Executado periodicamente via GitHub Actions / Cron
Fontes: yfinance (Mercados Globais) + FRED API (Yields de Obrigações Soberanas & Volatilidade Europa)
"""

import os
import sys
import logging
from datetime import datetime
import pandas as pd
import yfinance as yf
import requests

sys.path.append('backend')
from app.database import engine
from sqlalchemy import text

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

FRED_API_KEY = os.getenv("FRED_API_KEY", "")

YFINANCE_MAP = {
    # Volatilidade
    "^VIX": {"name": "VIX", "multiplier": 1.0, "save_ticker": "^VIX"},
    "^V2TX": {"name": "VSTOXX Euro Volatility", "multiplier": 1.0, "save_ticker": "VSTOXX"},
    
    # Obrigações EUA
    "^TNX": {"name": "US 10Y Yield", "multiplier": 1.0},
    "^TYX": {"name": "US 30Y Yield", "multiplier": 1.0},
    "TLT":  {"name": "TLT ETF", "multiplier": 1.0},
    
    # Forex
    "DX-Y.NYB": {"name": "DXY Dollar Index", "multiplier": 1.0},
    "EURUSD=X": {"name": "EUR/USD", "multiplier": 1.0},
    "USDJPY=X": {"name": "USD/JPY", "multiplier": 1.0},
    "GBPUSD=X": {"name": "GBP/USD", "multiplier": 1.0},
    "USDCNH=X": {"name": "USD/CNH", "multiplier": 1.0},
    "USDCHF=X": {"name": "USD/CHF", "multiplier": 1.0},
    
    # Commodities
    "BZ=F": {"name": "Brent Crude", "multiplier": 1.0},
    "CL=F": {"name": "WTI Crude", "multiplier": 1.0},
    "GC=F": {"name": "Gold Futures", "multiplier": 1.0},
    "SI=F": {"name": "Silver Futures", "multiplier": 1.0},
    "HG=F": {"name": "Copper Futures", "multiplier": 1.0},
    "NG=F": {"name": "Natural Gas", "multiplier": 1.0},
    
    # Índices Américas
    "^GSPC": {"name": "S&P 500", "multiplier": 1.0},
    "^NDX":  {"name": "Nasdaq 100", "multiplier": 1.0},
    "^DJI":  {"name": "Dow Jones", "multiplier": 1.0},
    "^RUT":  {"name": "Russell 2000", "multiplier": 1.0},
    "^SOX":  {"name": "SOX Semiconductor", "multiplier": 1.0},
    
    # Índices Europa
    "^GDAXI":    {"name": "DAX 40", "multiplier": 1.0},
    "^FCHI":     {"name": "CAC 40", "multiplier": 1.0},
    "^FTSE":     {"name": "FTSE 100", "multiplier": 1.0},
    "^STOXX50E": {"name": "Euro Stoxx 50", "multiplier": 1.0},
    "^IBEX":     {"name": "IBEX 35", "multiplier": 1.0},
    
    # Índices Ásia
    "^N225":     {"name": "Nikkei 225", "multiplier": 1.0},
    "^HSI":      {"name": "Hang Seng", "multiplier": 1.0},
    "000001.SS": {"name": "Shanghai Composite", "multiplier": 1.0},
    "^KS11":     {"name": "Kospi", "multiplier": 1.0},
    "^AXJO":     {"name": "ASX 200", "multiplier": 1.0},
    
    # Ações Inventário
    "O":    {"name": "Realty Income", "multiplier": 1.0},
    "DAL":  {"name": "Delta Air Lines", "multiplier": 1.0},
    "F":    {"name": "Ford Motor", "multiplier": 1.0},
    "ENPH": {"name": "Enphase Energy", "multiplier": 1.0},
    "NKE":  {"name": "Nike", "multiplier": 1.0},
    "STLA": {"name": "Stellantis", "multiplier": 1.0},
}

FRED_MAP = {
    "DGS2":            {"name": "US 2Y Treasury Yield"},
    "IRLTLT01DEM156N": {"name": "Bund Alemão 10Y Yield"},
    "IRLTLT01GBM156N": {"name": "Gilt UK 10Y Yield"},
    "IRLTLT01JPM156N": {"name": "JGB Japonês 10Y Yield"},
}

def fetch_yfinance_data():
    logging.info("Buscando cotações via yfinance...")
    tickers_list = list(YFINANCE_MAP.keys())
    
    data = yf.download(tickers=tickers_list, period="5d", interval="1d", group_by="ticker", progress=False)
    
    records = []
    for ticker, info in YFINANCE_MAP.items():
        try:
            df = data[ticker].dropna() if len(tickers_list) > 1 else data.dropna()
            if not df.empty:
                latest = df.iloc[-1]
                close_val = float(latest["Close"]) * info["multiplier"]
                open_val = float(latest["Open"]) * info["multiplier"] if "Open" in latest else None
                high_val = float(latest["High"]) * info["multiplier"] if "High" in latest else None
                low_val = float(latest["Low"]) * info["multiplier"] if "Low" in latest else None
                volume_val = int(latest["Volume"]) if "Volume" in latest and not pd.isna(latest["Volume"]) else 0
                
                # Sanitização Matemática de OHLC (Impede anomalias em Futuros como GC=F, CL=F)
                if open_val is not None and high_val is not None and low_val is not None:
                    high_val = max(high_val, open_val, close_val)
                    low_val = min(low_val, open_val, close_val)

                save_symbol = info.get("save_ticker", ticker)
                records.append({
                    "symbol": save_symbol,
                    "name": info["name"],
                    "timestamp": latest.name.strftime("%Y-%m-%d %H:%M:%S"),
                    "value": round(close_val, 6),
                    "open_val": round(open_val, 6) if open_val else None,
                    "high_val": round(high_val, 6) if high_val else None,
                    "low_val": round(low_val, 6) if low_val else None,
                    "volume": volume_val,
                    "provider": "YFINANCE"
                })
        except Exception as e:
            logging.error(f"Erro ao processar {ticker}: {e}")
            
    return records

def fetch_fred_data():
    if not FRED_API_KEY:
        logging.warning("FRED_API_KEY não definida. Saltando busca via FRED API.")
        return []
        
    logging.info("Buscando Yields Soberanas via FRED API...")
    records = []
    
    for series_id, info in FRED_MAP.items():
        try:
            url = f"https://api.stlouisfed.org/fred/series/observations?series_id={series_id}&api_key={FRED_API_KEY}&file_type=json&sort_order=desc&limit=1"
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                obs = response.json().get("observations", [])
                if obs and obs[0]["value"] != ".":
                    val = float(obs[0]["value"])
                    dt = obs[0]["date"]
                    records.append({
                        "symbol": series_id,
                        "name": info["name"],
                        "timestamp": f"{dt} 00:00:00",
                        "value": val,
                        "open_val": None,
                        "high_val": None,
                        "low_val": None,
                        "volume": 0,
                        "provider": "FRED_API"
                    })
        except Exception as e:
            logging.error(f"Erro ao buscar FRED series {series_id}: {e}")
            
    return records

def save_records_to_db(records):
    if not records:
        logging.warning("Nenhum registro para salvar na base de dados.")
        return

    logging.info(f"💾 Salvando {len(records)} registros brutos na Staging...")
    
    # Import do Validador de Data Quality
    from app.services.data_validator import validate_ohlc_record
    
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            # 1. Escrever na tabela Staging
            for rec in records:
                sql_staging = text("""
                    INSERT INTO staging_indicator_values (symbol, timestamp, open_val, high_val, low_val, value, volume)
                    VALUES (:symbol, :timestamp, :open_val, :high_val, :low_val, :value, :volume)
                """)
                conn.execute(sql_staging, {
                    "symbol": rec["symbol"],
                    "timestamp": rec["timestamp"],
                    "open_val": rec["open_val"],
                    "high_val": rec["high_val"],
                    "low_val": rec["low_val"],
                    "value": rec["value"],
                    "volume": rec["volume"]
                })
            trans.commit()
            logging.info(f"✅ {len(records)} cotações salvas na tabela 'staging_indicator_values'.")
        except Exception as e:
            trans.rollback()
            logging.error(f"❌ Erro ao salvar na Staging: {e}")

    # 2. Executar Data Quality Engine & Mover para Produção (indicator_values)
    logging.info("🛡️ Executando Data Quality Engine & Validação em Produção...")
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            catalog_rows = conn.execute(text("SELECT id, ticker FROM indicators_catalog")).fetchall()
            catalog_map = {row[1]: row[0] for row in catalog_rows}
            
            saved_count = 0
            quarantined_count = 0
            
            for rec in records:
                is_valid, sanitized_rec, err_msg = validate_ohlc_record(rec)
                if not is_valid:
                    quarantined_count += 1
                    continue
                    
                indicator_id = catalog_map.get(sanitized_rec["symbol"])
                if not indicator_id:
                    logging.warning(f"Ticker {sanitized_rec['symbol']} não encontrado no catálogo.")
                    continue
                    
                sql_prod = text("""
                    INSERT INTO indicator_values 
                    (indicator_id, symbol, timestamp, value, open_val, high_val, low_val, volume)
                    VALUES (:indicator_id, :symbol, :timestamp, :value, :open_val, :high_val, :low_val, :volume)
                    ON DUPLICATE KEY UPDATE 
                        value = VALUES(value),
                        open_val = VALUES(open_val),
                        high_val = VALUES(high_val),
                        low_val = VALUES(low_val),
                        volume = VALUES(volume);
                """)
                
                conn.execute(sql_prod, {
                    "indicator_id": indicator_id,
                    "symbol": sanitized_rec["symbol"],
                    "timestamp": sanitized_rec["timestamp"],
                    "value": sanitized_rec["value"],
                    "open_val": sanitized_rec["open_val"],
                    "high_val": sanitized_rec["high_val"],
                    "low_val": sanitized_rec["low_val"],
                    "volume": sanitized_rec["volume"]
                })
                saved_count += 1
                
            trans.commit()
            logging.info(f"🎉 Data Quality Concluído: {saved_count} cotações salvas em 'indicator_values' | {quarantined_count} em quarentena.")
        except Exception as e:
            trans.rollback()
            logging.error(f"❌ Erro ao processar validação em Produção: {e}")

def main():
    yf_records = fetch_yfinance_data()
    fred_records = fetch_fred_data()
    
    total_records = yf_records + fred_records
    logging.info(f"Total de {len(total_records)}/42 indicadores recolhidos com sucesso!")
    
    # Identificar tickers ausentes do catálogo e reportar no log
    fetched_symbols = {r["symbol"] for r in total_records}
    try:
        with engine.connect() as conn:
            catalog_rows = conn.execute(text("SELECT ticker FROM indicators_catalog")).fetchall()
            catalog_symbols = {row[0] for row in catalog_rows}
            missing = catalog_symbols - fetched_symbols
            if missing:
                logging.info(f"ℹ️ {len(missing)} tickers não foram recolhidos nesta corrida: {sorted(list(missing))}")
                if not FRED_API_KEY:
                    logging.info("  └─ Razão principal: FRED_API_KEY não configurada nos Secrets do GitHub (afeta DGS2, Bund 10Y, Gilt 10Y, JGB 10Y).")
                if "VSTOXX" in missing:
                    logging.info("  └─ Razão VSTOXX: Ticker de volatilidade europeu requer mapeamento de símbolo alternativo (^V2TX).")
    except Exception as e:
        logging.debug(f"Não foi possível verificar catálogo: {e}")

    # Salvar registros no banco de dados MySQL
    save_records_to_db(total_records)

if __name__ == "__main__":
    main()
