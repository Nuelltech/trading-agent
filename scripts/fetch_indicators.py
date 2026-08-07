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

def load_indicators_catalog():
    """
    Carrega o catálogo de indicadores prioritariamente da base de dados MySQL (indicators_catalog).
    Se a DB estiver indisponível, utiliza o fallback em config/indicators_catalog_fallback.json.
    Retorna dois dicionários: (yf_map, fred_map).
    """
    catalog_items = []
    try:
        with engine.connect() as conn:
            sql = text("SELECT ticker, name, data_provider, value_multiplier FROM indicators_catalog WHERE is_active = TRUE")
            rows = conn.execute(sql).fetchall()
            for r in rows:
                catalog_items.append({
                    "ticker": r[0],
                    "name": r[1] or r[0],
                    "data_provider": r[2] or "YFINANCE",
                    "value_multiplier": float(r[3]) if r[3] is not None else 1.0
                })
        logging.info(f"✅ Catálogo de indicadores carregado da DB MySQL ({len(catalog_items)} ativos).")
    except Exception as e:
        logging.warning(f"⚠️ Erro ao consultar indicators_catalog na DB ({e}). Carregando fallback JSON...")
        fallback_path = os.path.join(os.path.dirname(__file__), "..", "config", "indicators_catalog_fallback.json")
        try:
            with open(fallback_path, "r", encoding="utf-8") as f:
                catalog_items = json.load(f)
            logging.info(f"✅ Fallback JSON carregado com sucesso ({len(catalog_items)} ativos).")
        except Exception as json_err:
            logging.error(f"❌ Erro ao ler fallback JSON ({json_err}).")
            catalog_items = []

    yf_map = {}
    fred_map = {}

    for item in catalog_items:
        ticker = item["ticker"]
        name = item.get("name", ticker)
        provider = str(item.get("data_provider", "YFINANCE")).upper()
        multiplier = float(item.get("value_multiplier", 1.0))

        if provider == "FRED_API":
            fred_map[ticker] = {"name": name}
        else:
            if ticker == "VSTOXX":
                yf_map["^V2TX"] = {"name": name, "multiplier": multiplier, "save_ticker": "VSTOXX"}
            else:
                yf_map[ticker] = {"name": name, "multiplier": multiplier}

    return yf_map, fred_map

def fetch_yfinance_data(yf_map: dict):
    logging.info("Buscando cotações via yfinance...")
    tickers_list = list(yf_map.keys())
    if not tickers_list:
        logging.warning("⚠️ Nenhum ticker yfinance configurado para busca.")
        return []
    
    data = yf.download(tickers=tickers_list, period="5d", interval="1d", group_by="ticker", progress=False)
    
    records = []
    for ticker, info in yf_map.items():
        try:
            df = data[ticker].dropna() if len(tickers_list) > 1 else data.dropna()
            if not df.empty:
                latest = df.iloc[-1]
                close_val = float(latest["Close"]) * info["multiplier"]
                adj_close_val = float(latest["Adj Close"]) * info["multiplier"] if "Adj Close" in latest and not pd.isna(latest["Adj Close"]) else close_val
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
                    "adj_close": round(adj_close_val, 6) if adj_close_val else None,
                    "open_val": round(open_val, 6) if open_val else None,
                    "high_val": round(high_val, 6) if high_val else None,
                    "low_val": round(low_val, 6) if low_val else None,
                    "volume": volume_val,
                    "provider": "YFINANCE"
                })
        except Exception as e:
            logging.error(f"Erro ao processar {ticker}: {e}")
            
    return records

def fetch_fred_data(fred_map: dict):
    if not FRED_API_KEY:
        logging.warning("FRED_API_KEY não definida. Saltando busca via FRED API.")
        return []
        
    logging.info("Buscando Yields Soberanas via FRED API...")
    records = []
    
    for series_id, info in fred_map.items():
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
                        "adj_close": val,
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
                    INSERT INTO staging_indicator_values (symbol, timestamp, open_val, high_val, low_val, value, adj_close, volume)
                    VALUES (:symbol, :timestamp, :open_val, :high_val, :low_val, :value, :adj_close, :volume)
                """)
                conn.execute(sql_staging, {
                    "symbol": rec["symbol"],
                    "timestamp": rec["timestamp"],
                    "open_val": rec["open_val"],
                    "high_val": rec["high_val"],
                    "low_val": rec["low_val"],
                    "value": rec["value"],
                    "adj_close": rec.get("adj_close"),
                    "volume": rec["volume"]
                })
            trans.commit()
            logging.info(f"✅ {len(records)} cotações salvas na tabela 'staging_indicator_values'.")
        except Exception as e:
            trans.rollback()
            logging.error(f"❌ Erro ao salvar na Staging: {e}")

    # 2. Executar Data Quality Engine & Mover para Produção (indicator_values)
    logging.info("🛡️ Executando Data Quality Engine & Validação em Produção...")
    try:
        from app.services.data_validator import promover_para_producao
        
        saved_count = 0
        quarantined_count = 0
        
        for rec in records:
            is_valid, sanitized_rec, err_msg = validate_ohlc_record(rec)
            if not is_valid:
                quarantined_count += 1
                continue
                
            res = promover_para_producao(
                ticker=sanitized_rec["symbol"],
                data=sanitized_rec["timestamp"],
                novo_open=sanitized_rec["open_val"],
                novo_high=sanitized_rec["high_val"],
                novo_low=sanitized_rec["low_val"],
                novo_close=sanitized_rec["value"],
                volume=sanitized_rec.get("volume", 0),
                novo_adj_close=sanitized_rec.get("adj_close")
            )
            if res.get("status") in ["inserted", "updated"]:
                saved_count += 1
            elif res.get("status") == "error":
                logging.warning(f"⚠️ Promoção de {sanitized_rec['symbol']} falhou: {res.get('message')}")
                
        logging.info(f"🎉 Data Quality Concluído: {saved_count} cotações promovidas para 'indicator_values' | {quarantined_count} em quarentena.")
    except Exception as e:
        logging.error(f"❌ Erro ao processar validação em Produção: {e}")

def main():
    yf_map, fred_map = load_indicators_catalog()
    yf_records = fetch_yfinance_data(yf_map)
    fred_records = fetch_fred_data(fred_map)
    
    total_records = yf_records + fred_records
    logging.info(f"Total de {len(total_records)} indicadores recolhidos com sucesso!")
    
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
    try:
        main()
    except Exception as e:
        logging.error(f"❌ Erro fatal no pipeline de indicadores: {e}")
        try:
            from app.services.alert_service import send_alert_notification
            send_alert_notification(f"🚨 [FALHA CRÍTICA INGESTÃO] Ingestão de Indicadores falhou: {type(e).__name__} - {e}")
        except Exception as alert_err:
            logging.warning(f"Não foi possível enviar e-mail de notificação de falha: {alert_err}")
        raise

    finally:
        # Fechar pool de conexões graciosamente para evitar ConnectionResetError
        # cosmético ao encerrar o processo (MySQL server has gone away)
        try:
            engine.dispose()
        except Exception:
            pass
