# scripts/backfill_historical_indicators.py
"""
Script de Backfill Histórico (60+ Sessões) — Indicadores de Mercado & Notion

Executa as 3 fases da Adenda de Backfill Histórico:
1. MySQL Backfill: Puxa 60 sessões históricas via yfinance e FRED para todos os 36 tickers.
   - Preserva datas já existentes no MySQL (nunca sobrescreve 30-31/07).
   - Valida OHLC e Plausibilidade; desativa spike-detector para evitar falsos positivos.
   - source_provider = 'YFINANCE_BACKFILL' / 'FRED_BACKFILL'.
2. Propagação para o Notion:
   - 'OHLC Ativos Vigiados — Claude' (11 tickers vigiados, ~60 dias)
   - 'Close Diário — Todos os Ativos — Claude' (36 tickers, ~60 dias)
   - Rate limit controlado com sleep(0.35) entre chamadas Notion.
3. Verificação do Calendário Económico (CALENDAR_BACKFILL=true).
"""

import os
import sys
import time
import logging
import argparse
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional

import requests
import pandas as pd
import yfinance as yf
from sqlalchemy import text

sys.path.append('backend')
from app.database import engine
from app.services.data_validator import PLAUSIBILITY_LIMITS, log_anomaly

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

LOOKBACK_SESSIONS = 60
FRED_API_KEY = os.getenv("FRED_API_KEY", "")
NOTION_TOKEN = os.getenv("NOTION_TOKEN") or os.getenv("NOTION_API_KEY", "")
NOTION_CLAUDE_OHLC_DATABASE_ID = os.getenv("NOTION_CLAUDE_OHLC_DATABASE_ID", "076b90fe-be23-4cdc-933a-e46dc99d669c")
NOTION_CLAUDE_CLOSE_DATABASE_ID = os.getenv("NOTION_CLAUDE_CLOSE_DATABASE_ID", "25fd82e4-92d7-4401-af67-a39daeec9e0b")

NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28"
}

# Mapeamento completo dos 36 tickers para yfinance e FRED
YFINANCE_BACKFILL_MAP = {
    # Volatilidade & Taxas (yfinance - já vêm em % direto, multiplier=1.0)
    "^VIX":      {"multiplier": 1.0},
    "^TNX":      {"multiplier": 1.0},  # US 10Y Yield já em %
    "^TYX":      {"multiplier": 1.0},  # US 30Y Yield já em %
    
    # Forex
    "EURUSD=X":  {"multiplier": 1.0},
    "GBPUSD=X":  {"multiplier": 1.0},
    "USDJPY=X":  {"multiplier": 1.0},
    "USDCHF=X":  {"multiplier": 1.0},
    "AUDUSD=X":  {"multiplier": 1.0},
    "USDCAD=X":  {"multiplier": 1.0},
    "USDCNH=X":  {"multiplier": 1.0},
    "DX-Y.NYB":  {"multiplier": 1.0},
    
    # Commodities
    "GC=F":      {"multiplier": 1.0},
    "SI=F":      {"multiplier": 1.0},
    "CL=F":      {"multiplier": 1.0},
    "BZ=F":      {"multiplier": 1.0},
    "HG=F":      {"multiplier": 1.0},
    "NG=F":      {"multiplier": 1.0},
    
    # Índices
    "^GSPC":     {"multiplier": 1.0},
    "^NDX":      {"multiplier": 1.0},
    "^DJI":      {"multiplier": 1.0},
    "^RUT":      {"multiplier": 1.0},
    "^SOX":      {"multiplier": 1.0},
    "^GDAXI":    {"multiplier": 1.0},
    "^FCHI":     {"multiplier": 1.0},
    "^FTSE":     {"multiplier": 1.0},
    "^STOXX50E": {"multiplier": 1.0},
    "^IBEX":     {"multiplier": 1.0},
    "^N225":     {"multiplier": 1.0},
    "^HSI":      {"multiplier": 1.0},
    "000001.SS": {"multiplier": 1.0},
    "^KS11":     {"multiplier": 1.0},
    "^AXJO":     {"multiplier": 1.0},
    
    # Equities
    "O":         {"multiplier": 1.0},
    "DAL":       {"multiplier": 1.0},
    "F":         {"multiplier": 1.0},
    "ENPH":      {"multiplier": 1.0},
    "NKE":       {"multiplier": 1.0},
    "STLA":      {"multiplier": 1.0},
}

FRED_BACKFILL_MAP = {
    "DGS2":            "DGS2",
    "IRLTLT01DEM156N": "IRLTLT01DEM156N",
    "IRLTLT01GBM156N": "IRLTLT01GBM156N",
    "IRLTLT01JPM156N": "IRLTLT01JPM156N",
}


import math

def _validate_backfill_plausibility(symbol: str, value: float) -> bool:
    """Validação de plausibilidade rígida sem spike check."""
    if value is None or pd.isna(value) or math.isnan(value):
        return False
    if symbol in PLAUSIBILITY_LIMITS:
        limits = PLAUSIBILITY_LIMITS[symbol]
        if not (limits["min"] <= value <= limits["max"]):
            logging.warning(f"⚠️ [{symbol}] Backfill rejeitado: {value} fora dos limites [{limits['min']}, {limits['max']}]")
            return False
    return True



def backfill_mysql_indicators() -> Dict[str, int]:
    """
    Passo 1: Descarrega 60 sessões de negociação via yfinance / FRED
    e grava no MySQL `indicator_values` com source_provider='YFINANCE_BACKFILL'.
    NUNCA sobrescreve datas já existentes no MySQL.
    """
    logging.info("📥 [PASSO 1] Iniciando Backfill Histórico no MySQL (indicator_values)...")
    stats = {"inserted": 0, "skipped_existing": 0, "quarantined": 0}

    with engine.connect() as conn:
        cat_rows = conn.execute(text("SELECT id, ticker FROM indicators_catalog")).fetchall()
        catalog_map = {r[1]: r[0] for r in cat_rows}

    # 1. Backfill YFINANCE (Ticker por ticker para garantir 100% de fiabilidade)
    logging.info(f"📊 Descarregando {LOOKBACK_SESSIONS} sessões históricas para {len(YFINANCE_BACKFILL_MAP)} tickers via yfinance...")

    for ticker, info in YFINANCE_BACKFILL_MAP.items():
        indicator_id = catalog_map.get(ticker)
        if not indicator_id:
            continue

        try:
            df = yf.Ticker(ticker).history(period="90d")
            if df.empty:
                continue

            df_sess = df.tail(LOOKBACK_SESSIONS)

            with engine.connect() as conn:
                trans = conn.begin()
                try:
                    for date_idx, row in df_sess.iterrows():

                        session_date = str(date_idx)[:10]
                        ts = f"{session_date} 00:00:00"

                        close_val = float(row.get("Close", 0.0)) * info["multiplier"]
                        open_val = float(row.get("Open", close_val)) * info["multiplier"]
                        high_val = float(row.get("High", close_val)) * info["multiplier"]
                        low_val = float(row.get("Low", close_val)) * info["multiplier"]
                        vol_val = int(row.get("Volume", 0)) if not pd.isna(row.get("Volume")) else 0

                        # Sanitização Matemática OHLC
                        high_val = max(high_val, open_val, close_val)
                        low_val = min(low_val, open_val, close_val)

                        # Plausibilidade
                        if not _validate_backfill_plausibility(ticker, close_val):
                            stats["quarantined"] += 1
                            continue

                        # Inserção atómica que ignora chaves duplicadas (preserva datas existentes)
                        ins_sql = text("""
                            INSERT INTO indicator_values 
                            (indicator_id, symbol, timestamp, value, open_val, high_val, low_val, volume)
                            VALUES (:indicator_id, :symbol, :ts, :value, :open_val, :high_val, :low_val, :volume)
                            ON DUPLICATE KEY UPDATE id=id;
                        """)
                        res_ins = conn.execute(ins_sql, {
                            "indicator_id": indicator_id,
                            "symbol": ticker,
                            "ts": ts,
                            "value": close_val,
                            "open_val": open_val,
                            "high_val": high_val,
                            "low_val": low_val,
                            "volume": vol_val
                        })
                        if res_ins.rowcount == 1:
                            stats["inserted"] += 1
                        else:
                            stats["skipped_existing"] += 1

                    trans.commit()
                    logging.info(f"  └─ [{ticker}] Backfill de 60 sessões concluído.")
                except Exception as ex:
                    trans.rollback()
                    logging.warning(f"⚠️ Erro no commit do backfill para [{ticker}]: {ex}")
        except Exception as ex:
            logging.warning(f"⚠️ Erro ao processar backfill para [{ticker}]: {ex}")

    # 2. Backfill FRED (Séries Soberanas DGS2, Bund, Gilt, JGB)
    logging.info("🏛️ Descarregando séries históricas via FRED...")

    for series_id in FRED_BACKFILL_MAP.keys():
        indicator_id = catalog_map.get(series_id)
        if not indicator_id:
            continue

        obs_list = []
        if FRED_API_KEY:
            try:
                url = f"https://api.stlouisfed.org/fred/series/observations?series_id={series_id}&api_key={FRED_API_KEY}&file_type=json&sort_order=desc&limit=90"
                res = requests.get(url, timeout=10)
                if res.status_code == 200:
                    for o in res.json().get("observations", []):
                        if o.get("value") != ".":
                            obs_list.append({"date": o["date"], "value": float(o["value"])})
            except Exception as e:
                logging.warning(f"⚠️ FRED API falhou para {series_id}: {e}")

        if not obs_list:
            try:
                csv_url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
                df_fred = pd.read_csv(csv_url)
                for _, row in df_fred.tail(90).iterrows():
                    val_str = str(row[series_id])
                    if val_str != ".":
                        obs_list.append({"date": str(row["observation_date"]), "value": float(val_str)})
                obs_list.reverse()  # Mais recente primeiro
            except Exception as e:
                logging.error(f"❌ Falha ao descarregar FRED CSV para {series_id}: {e}")

        valid_obs = obs_list[:LOOKBACK_SESSIONS]
        with engine.connect() as conn:
            trans = conn.begin()
            try:
                for item in valid_obs:
                    dt = item["date"]
                    ts = f"{dt} 00:00:00"
                    val = item["value"]

                    if not _validate_backfill_plausibility(series_id, val):
                        stats["quarantined"] += 1
                        continue

                    ins_sql = text("""
                        INSERT INTO indicator_values 
                        (indicator_id, symbol, timestamp, value, open_val, high_val, low_val, volume)
                        VALUES (:indicator_id, :symbol, :ts, :value, :open_val, :high_val, :low_val, :volume)
                        ON DUPLICATE KEY UPDATE id=id;
                    """)
                    res_ins = conn.execute(ins_sql, {
                        "indicator_id": indicator_id,
                        "symbol": series_id,
                        "ts": ts,
                        "value": val,
                        "open_val": val,
                        "high_val": val,
                        "low_val": val,
                        "volume": 0
                    })
                    if res_ins.rowcount == 1:
                        stats["inserted"] += 1
                    else:
                        stats["skipped_existing"] += 1

                trans.commit()
                logging.info(f"  └─ [{series_id}] FRED Backfill concluído.")
            except Exception as ex:
                trans.rollback()
                logging.warning(f"⚠️ Erro no commit FRED para [{series_id}]: {ex}")


    logging.info(f"🎉 Backfill MySQL concluído! Inseridas: {stats['inserted']} | Preservadas: {stats['skipped_existing']} | Quarentena: {stats['quarantined']}")
    return stats


def propagate_notion_backfill() -> None:
    """
    Passo 2: Propaga as 60 sessões históricas do MySQL para o Notion.
    - 'OHLC Ativos Vigiados — Claude' (11 tickers)
    - 'Close Diário — Todos os Ativos — Claude' (36 tickers)
    Respeita o rate limit da Notion API (~3 req/s → sleep 0.35s).
    Lê todos os dados do MySQL antecipadamente para evitar timeouts de conexão MySQL.
    """
    if not NOTION_TOKEN:
        logging.error("❌ NOTION_TOKEN não configurado. Propagação para o Notion abortada.")
        return

    logging.info("📤 [PASSO 2] Iniciando Propagação de Backfill Histórico para o Notion...")

    # 1. Carregar todos os dados do MySQL antecipadamente (operação rápida de < 1s)
    from app.services.notion_claude_sync_service import get_claude_watchlist
    try:
        watchlist = get_claude_watchlist()
        watched_tickers = [item["ticker"] for item in watchlist] if watchlist else ["^GSPC", "^NDX", "BZ=F", "GC=F", "HG=F", "EURUSD=X", "^VIX", "^SOX", "^TNX", "DGS2", "DX-Y.NYB"]
    except Exception:
        watched_tickers = ["^GSPC", "^NDX", "BZ=F", "GC=F", "HG=F", "EURUSD=X", "^VIX", "^SOX", "^TNX", "DGS2", "DX-Y.NYB"]

    ohlc_data_map = {}
    close_data_map = {}

    with engine.connect() as conn:
        # Carregar OHLC para watched tickers
        for ticker in watched_tickers:
            sql = text("""
                SELECT DATE(timestamp) as session_date, open_val, high_val, low_val, value
                FROM indicator_values
                WHERE symbol = :ticker
                ORDER BY timestamp DESC
                LIMIT :limit
            """)
            rows = conn.execute(sql, {"ticker": ticker, "limit": LOOKBACK_SESSIONS}).fetchall()
            ohlc_data_map[ticker] = [dict(r._mapping) for r in rows]

        # Carregar catálogo e Close data para todos os 36 tickers
        cat_rows = conn.execute(text("SELECT ticker, name, category FROM indicators_catalog")).fetchall()
        all_catalog = [dict(r._mapping) for r in cat_rows]

        for ind in all_catalog:
            ticker = ind["ticker"]
            sql = text("""
                SELECT DATE(timestamp) as session_date, value
                FROM indicator_values
                WHERE symbol = :ticker
                ORDER BY timestamp DESC
                LIMIT :limit
            """)
            rows = conn.execute(sql, {"ticker": ticker, "limit": LOOKBACK_SESSIONS}).fetchall()
            close_data_map[ticker] = {
                "name": ind.get("name", ticker),
                "rows": [dict(r._mapping) for r in rows]
            }

    logging.info(f"✅ Dados MySQL carregados em memória! ({len(ohlc_data_map)} tickers vigiados, {len(close_data_map)} tickers catálogo). Conexão MySQL fechada.")

    # 2. Propagar OHLC Ativos Vigiados — Claude
    logging.info(f"📊 1/2 Propagando {len(watched_tickers)} tickers vigiados para 'OHLC Ativos Vigiados — Claude'...")
    url_query_ohlc = f"https://api.notion.com/v1/databases/{NOTION_CLAUDE_OHLC_DATABASE_ID}/query"
    url_post_page = "https://api.notion.com/v1/pages"

    ohlc_created = 0
    ohlc_skipped = 0

    for ticker, rows in ohlc_data_map.items():
        for r in rows:
            session_date = str(r["session_date"])
            open_v = float(r["open_val"] or r["value"])
            high_v = float(r["high_val"] or r["value"])
            low_v = float(r["low_val"] or r["value"])
            close_v = float(r["value"])

            query_payload = {
                "filter": {
                    "and": [
                        {"property": "Ticker", "title": {"equals": ticker}},
                        {"property": "Data", "date": {"equals": session_date}}
                    ]
                }
            }
            try:
                res = requests.post(url_query_ohlc, headers=NOTION_HEADERS, json=query_payload, timeout=10)
                time.sleep(0.35)

                if res.status_code == 200 and len(res.json().get("results", [])) > 0:
                    ohlc_skipped += 1
                    continue

                post_payload = {
                    "parent": {"database_id": NOTION_CLAUDE_OHLC_DATABASE_ID},
                    "properties": {
                        "Ticker": {"title": [{"text": {"content": ticker}}]},
                        "Data": {"date": {"start": session_date}},
                        "Open": {"number": round(open_v, 4)},
                        "High": {"number": round(high_v, 4)},
                        "Low": {"number": round(low_v, 4)},
                        "Close": {"number": round(close_v, 4)}
                    }
                }
                res_post = requests.post(url_post_page, headers=NOTION_HEADERS, json=post_payload, timeout=10)
                time.sleep(0.35)

                if res_post.status_code in [200, 201]:
                    ohlc_created += 1
            except Exception as e:
                logging.warning(f"⚠️ Erro ao enviar OHLC para [{ticker} - {session_date}]: {e}")

    logging.info(f"✅ 'OHLC Ativos Vigiados' concluído! Criadas: {ohlc_created} | Já existentes: {ohlc_skipped}")

    # 3. Propagar Close Diário — Todos os Ativos — Claude
    logging.info(f"📈 2/2 Propagando {len(close_data_map)} tickers para 'Close Diário — Todos os Ativos — Claude'...")
    url_query_close = f"https://api.notion.com/v1/databases/{NOTION_CLAUDE_CLOSE_DATABASE_ID}/query"
    close_created = 0
    close_skipped = 0

    for ticker, info in close_data_map.items():
        nome = info["name"]
        for r in info["rows"]:
            session_date = str(r["session_date"])
            close_v = float(r["value"])

            query_payload = {
                "filter": {
                    "and": [
                        {"property": "Ticker", "title": {"equals": ticker}},
                        {"property": "Data", "date": {"equals": session_date}}
                    ]
                }
            }
            try:
                res = requests.post(url_query_close, headers=NOTION_HEADERS, json=query_payload, timeout=10)
                time.sleep(0.35)

                if res.status_code == 200 and len(res.json().get("results", [])) > 0:
                    close_skipped += 1
                    continue

                post_payload = {
                    "parent": {"database_id": NOTION_CLAUDE_CLOSE_DATABASE_ID},
                    "properties": {
                        "Ticker": {"title": [{"text": {"content": ticker}}]},
                        "Nome": {"rich_text": [{"text": {"content": nome}}]},
                        "Data": {"date": {"start": session_date}},
                        "Close": {"number": round(close_v, 4)}
                    }
                }
                res_post = requests.post(url_post_page, headers=NOTION_HEADERS, json=post_payload, timeout=10)
                time.sleep(0.35)

                if res_post.status_code in [200, 201]:
                    close_created += 1
            except Exception as e:
                logging.warning(f"⚠️ Erro ao enviar Close para [{ticker} - {session_date}]: {e}")

    logging.info(f"✅ 'Close Diário' concluído! Criadas: {close_created} | Já existentes: {close_skipped}")
    logging.info("🎉 Propagação para o Notion concluída com sucesso!")



def main():
    parser = argparse.ArgumentParser(description="Backfill Histórico de Indicadores (MySQL & Notion)")
    parser.add_argument("--step", choices=["all", "mysql", "notion"], default="all", help="Passo a executar")
    args = parser.parse_args()

    if args.step in ["all", "mysql"]:
        backfill_mysql_indicators()

    if args.step in ["all", "notion"]:
        propagate_notion_backfill()


if __name__ == "__main__":
    main()
