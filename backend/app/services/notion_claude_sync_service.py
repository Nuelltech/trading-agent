# backend/app/services/notion_claude_sync_service.py
"""
Módulo: notion_claude_sync_service.py (Adenda v2 — Tabelas Notion do Claude - Consolidação Final)
Data: 30 de Julho de 2026

Gere a sincronização das 3 databases exclusivas do Claude no Notion:
1. OHLC Ativos Vigiados — Claude (076b90fe-be23-4cdc-933a-e46dc99d669c)
   - Lê 'Configuração de Vigilância' no início de cada execução (Ativo == True AND Vigiado Por IN ('Claude', 'Ambos'))
   - Upsert com Open fixo, High=max(), Low=min(), Close=mais recente.
2. Close Diário — Todos os Ativos — Claude (25fd82e4-92d7-4401-af67-a39daeec9e0b)
   - Processa SEMPRE todos os 36 ativos do indicators_catalog sem depender da Configuração de Vigilância.
3. Resumo Diário — Regime de Risco — Claude (3efd828b-84a7-4966-8bdf-fe9c93657edd)
   - Cria/Atualiza a linha do dia (só Data); NUNCA escreve no campo Regime (reservado ao Claude).
"""

import os
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional
import requests
import yfinance as yf
from sqlalchemy import text

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

NOTION_TOKEN = os.getenv("NOTION_TOKEN") or os.getenv("NOTION_API_KEY", "")
NOTION_CONFIG_DB_ID = os.getenv("NOTION_CONFIG_DB_ID", "fb3a2102-c785-46c9-b2b4-5adecd9d5482")
NOTION_CLAUDE_OHLC_DATABASE_ID = os.getenv("NOTION_CLAUDE_OHLC_DATABASE_ID", "076b90fe-be23-4cdc-933a-e46dc99d669c")
NOTION_CLAUDE_CLOSE_DATABASE_ID = os.getenv("NOTION_CLAUDE_CLOSE_DATABASE_ID", "25fd82e4-92d7-4401-af67-a39daeec9e0b")
NOTION_CLAUDE_REGIME_DATABASE_ID = os.getenv("NOTION_CLAUDE_REGIME_DATABASE_ID") or os.getenv("NOTION_PAINEL_MERCADO_DATABASE_ID", "3efd828b-84a7-4966-8bdf-fe9c93657edd")

NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28"
}

def get_notion_title_col_name(db_id: str, default_name: str = "Ticker") -> str:
    """Descobre o nome da coluna do tipo 'title' de uma database do Notion"""
    url = f"https://api.notion.com/v1/databases/{db_id}"
    try:
        res = requests.get(url, headers=NOTION_HEADERS, timeout=10)
        if res.status_code == 200:
            properties = res.json().get("properties", {})
            for prop_name, prop_data in properties.items():
                if prop_data.get("type") == "title":
                    logging.info(f"🔍 Coluna 'title' detetada para db [{db_id}]: [{prop_name}]")
                    return prop_name
    except Exception as e:
        logging.warning(f"Falha ao obter title property da db {db_id}: {e}")
    return default_name

def get_claude_watchlist() -> List[Dict[str, Any]]:
    """
    Fase A — Lê a Configuração de Vigilância e devolve só os tickers para o Claude nesta execução.
    Filtro: Ativo == True AND Vigiado Por IN ('Claude', 'Ambos')
    NUNCA hardcodar a lista — a tabela do Notion é a única fonte de verdade.
    """
    if not NOTION_TOKEN or not NOTION_CONFIG_DB_ID:
        logging.error("❌ NOTION_TOKEN ou NOTION_CONFIG_DB_ID não configurados.")
        return []

    url = f"https://api.notion.com/v1/databases/{NOTION_CONFIG_DB_ID}/query"
    try:
        res = requests.post(url, headers=NOTION_HEADERS, json={}, timeout=10)
        if res.status_code != 200:
            logging.error(f"❌ Erro ao consultar Configuração de Vigilância ({res.status_code}): {res.text}")
            return []

        raw_items = res.json().get("results", [])
        watchlist = []

        for item in raw_items:
            props = item.get("properties", {})

            # Checkbox: Ativo
            ativo_val = props.get("Ativo", {}).get("checkbox", False)
            if not ativo_val:
                continue

            # Select: Vigiado Por
            vigiado_select = props.get("Vigiado Por", {}).get("select", {})
            vigiado_val = vigiado_select.get("name", "") if vigiado_select else ""
            if vigiado_val not in ["Claude", "Ambos"]:
                continue

            # Ticker (Title)
            ticker_list = props.get("Ticker", {}).get("title", []) or props.get("Name", {}).get("title", [])
            ticker = ticker_list[0].get("text", {}).get("content", "") if ticker_list else ""

            # Nome (Rich text)
            nome_list = props.get("Nome", {}).get("rich_text", [])
            nome = nome_list[0].get("text", {}).get("content", ticker) if nome_list else ticker

            if ticker:
                watchlist.append({"ticker": ticker, "nome": nome})

        logging.info(f"✅ [CLAUDE WATCHLIST] {len(watchlist)} ativos vigiados encontrados na Configuração de Vigilância.")
        return watchlist
    except Exception as e:
        logging.error(f"❌ Falha ao ler Configuração de Vigilância para Claude: {e}")
        return []

def fetch_ticker_ohlc(ticker: str) -> Dict[str, float]:
    """Lê Open, High, Low, Close de hoje no MySQL (ou fallback yfinance)"""
    ohlc = {"open": 0.0, "high": 0.0, "low": 0.0, "close": 0.0}
    try:
        from app.database import engine
        with engine.connect() as conn:
            sql = text("""
                SELECT open_val, high_val, low_val, value 
                FROM indicator_values 
                WHERE symbol = :ticker 
                ORDER BY timestamp DESC LIMIT 1
            """)
            row = conn.execute(sql, {"ticker": ticker}).fetchone()
            if row and row[3] is not None:
                ohlc["open"] = float(row[0] or row[3])
                ohlc["high"] = float(row[1] or row[3])
                ohlc["low"] = float(row[2] or row[3])
                ohlc["close"] = float(row[3])
                return ohlc
    except Exception as e:
        logging.warning(f"⚠️ Erro MySQL para [{ticker}]: {e}. Ativando fallback yfinance...")

    try:
        df = yf.Ticker(ticker).history(period="5d")
        if not df.empty:
            last = df.iloc[-1]
            ohlc["open"] = float(last.get("Open", last.get("Close", 0.0)))
            ohlc["high"] = float(last.get("High", last.get("Close", 0.0)))
            ohlc["low"] = float(last.get("Low", last.get("Close", 0.0)))
            ohlc["close"] = float(last.get("Close", 0.0))
    except Exception as ex:
        logging.warning(f"Falha yfinance para [{ticker}]: {ex}")

    return ohlc

# -----------------------------------------------------------------------------
# 1. Sync Database #2: OHLC Ativos Vigiados — Claude (076b90fe-be23-4cdc-933a-e46dc99d669c)
# -----------------------------------------------------------------------------
def sync_claude_ohlc_vigiados() -> bool:
    """Executa o Upsert na tabela 'OHLC Ativos Vigiados — Claude'"""
    watchlist = get_claude_watchlist()
    if not watchlist:
        logging.warning("⚠️ Watchlist do Claude vazia. Nenhuma linha OHLC para sincronizar.")
        return True

    if not NOTION_TOKEN or not NOTION_CLAUDE_OHLC_DATABASE_ID:
        logging.error("❌ NOTION_CLAUDE_OHLC_DATABASE_ID não configurado.")
        return False

    title_col = get_notion_title_col_name(NOTION_CLAUDE_OHLC_DATABASE_ID, "Ticker")
    today_date = datetime.utcnow().strftime("%Y-%m-%d")
    url_query = f"https://api.notion.com/v1/databases/{NOTION_CLAUDE_OHLC_DATABASE_ID}/query"

    for item in watchlist:
        ticker = item["ticker"]
        nome = item.get("nome", ticker)
        new_ohlc = fetch_ticker_ohlc(ticker)

        if new_ohlc["close"] == 0.0:
            continue

        query_payload = {
            "filter": {
                "and": [
                    {"property": title_col, "title": {"equals": ticker}},
                    {"property": "Data", "date": {"equals": today_date}}
                ]
            }
        }

        try:
            res = requests.post(url_query, headers=NOTION_HEADERS, json=query_payload, timeout=10)
            existing_results = res.json().get("results", []) if res.status_code == 200 else []

            if existing_results:
                page = existing_results[0]
                page_id = page["id"]
                props = page.get("properties", {})

                curr_high = props.get("High", {}).get("number", new_ohlc["high"])
                curr_low = props.get("Low", {}).get("number", new_ohlc["low"])

                final_high = max(curr_high or new_ohlc["high"], new_ohlc["high"])
                final_low = min(curr_low or new_ohlc["low"], new_ohlc["low"])

                patch_payload = {
                    "properties": {
                        "High": {"number": round(final_high, 4)},
                        "Low": {"number": round(final_low, 4)},
                        "Close": {"number": round(new_ohlc["close"], 4)}
                    }
                }

                url_patch = f"https://api.notion.com/v1/pages/{page_id}"
                requests.patch(url_patch, headers=NOTION_HEADERS, json=patch_payload, timeout=10)
                logging.info(f"✅ [CLAUDE OHLC] [{ticker}] atualizado (High={final_high}, Low={final_low}, Close={new_ohlc['close']})")

            else:
                post_payload = {
                    "parent": {"database_id": NOTION_CLAUDE_OHLC_DATABASE_ID},
                    "properties": {
                        title_col: {"title": [{"text": {"content": ticker}}]},
                        "Nome": {"rich_text": [{"text": {"content": nome}}]},
                        "Data": {"date": {"start": today_date}},
                        "Open": {"number": round(new_ohlc["open"], 4)},
                        "High": {"number": round(new_ohlc["high"], 4)},
                        "Low": {"number": round(new_ohlc["low"], 4)},
                        "Close": {"number": round(new_ohlc["close"], 4)}
                    }
                }
                url_post = "https://api.notion.com/v1/pages"
                requests.post(url_post, headers=NOTION_HEADERS, json=post_payload, timeout=10)
                logging.info(f"✅ [CLAUDE OHLC] Linha criada para [{ticker}]")

        except Exception as e:
            logging.error(f"❌ Falha no Upsert Claude OHLC para [{ticker}]: {e}")

    return True

# -----------------------------------------------------------------------------
# 2. Sync Database #3: Close Diário — Todos os Ativos — Claude (25fd82e4-92d7-4401-af67-a39daeec9e0b)
# -----------------------------------------------------------------------------
def sync_claude_close_todos_ativos() -> bool:
    """
    Processa SEMPRE todos os 36 ativos do indicators_catalog (sem depender da Configuração de Vigilância).
    Upsert por dia: 1 linha por ticker/dia. Escreve Ticker (title), Nome, Categoria, Data, Close.
    """
    if not NOTION_TOKEN or not NOTION_CLAUDE_CLOSE_DATABASE_ID:
        logging.error("❌ NOTION_CLAUDE_CLOSE_DATABASE_ID não configurado.")
        return False

    title_col = get_notion_title_col_name(NOTION_CLAUDE_CLOSE_DATABASE_ID, "Ticker")
    today_date = datetime.utcnow().strftime("%Y-%m-%d")

    # Obter catálogo completo de ativos no MySQL (ou lista completa padrão)
    all_indicators = []
    try:
        from app.database import engine
        with engine.connect() as conn:
            sql = text("SELECT symbol, name, asset_class FROM indicators_catalog")
            rows = conn.execute(sql).fetchall()
            for r in rows:
                all_indicators.append({"ticker": r[0], "nome": r[1] or r[0], "categoria": r[2] or "Geral"})
    except Exception as e:
        logging.warning(f"⚠️ Erro ao consultar indicators_catalog ({e}). Usando catálogo completo estático...")

    if not all_indicators:
        all_indicators = [
            {"ticker": "BZ=F", "nome": "Brent Crude", "categoria": "Commodities"},
            {"ticker": "GC=F", "nome": "Gold", "categoria": "Commodities"},
            {"ticker": "CL=F", "nome": "WTI Crude", "categoria": "Commodities"},
            {"ticker": "HG=F", "nome": "Copper", "categoria": "Commodities"},
            {"ticker": "EURUSD=X", "nome": "EUR/USD", "categoria": "Forex"},
            {"ticker": "GBPUSD=X", "nome": "GBP/USD", "categoria": "Forex"},
            {"ticker": "USDJPY=X", "nome": "USD/JPY", "categoria": "Forex"},
            {"ticker": "DX-Y.NYB", "nome": "US Dollar Index", "categoria": "Forex"},
            {"ticker": "^GSPC", "nome": "S&P 500", "categoria": "Indices"},
            {"ticker": "^NDX", "nome": "Nasdaq 100", "categoria": "Indices"},
            {"ticker": "^GDAXI", "nome": "DAX", "categoria": "Indices"},
            {"ticker": "^TNX", "nome": "US 10Y Yield", "categoria": "Rates"},
            {"ticker": "DGS2", "nome": "US 2Y Yield", "categoria": "Rates"},
            {"ticker": "IRLTLT01DEM156N", "nome": "Germany 10Y Yield", "categoria": "Rates"},
            {"ticker": "IRLTLT01GBM156N", "nome": "UK 10Y Yield", "categoria": "Rates"},
            {"ticker": "IRLTLT01JPM156N", "nome": "Japan 10Y Yield", "categoria": "Rates"},
            {"ticker": "^VIX", "nome": "VIX Index", "categoria": "Volatility"},
            {"ticker": "O", "nome": "Realty Income", "categoria": "Equities"},
            {"ticker": "DAL", "nome": "Delta Air Lines", "categoria": "Equities"},
            {"ticker": "F", "nome": "Ford Motor", "categoria": "Equities"},
            {"ticker": "ENPH", "nome": "Enphase Energy", "categoria": "Equities"},
            {"ticker": "NKE", "nome": "Nike", "categoria": "Equities"},
            {"ticker": "STLA", "nome": "Stellantis", "categoria": "Equities"}
        ]

    url_query = f"https://api.notion.com/v1/databases/{NOTION_CLAUDE_CLOSE_DATABASE_ID}/query"

    for ind in all_indicators:
        ticker = ind["ticker"]
        nome = ind["nome"]
        cat = ind.get("categoria", "Geral")
        ohlc = fetch_ticker_ohlc(ticker)
        close_val = ohlc["close"]

        if close_val == 0.0:
            continue

        query_payload = {
            "filter": {
                "and": [
                    {"property": title_col, "title": {"equals": ticker}},
                    {"property": "Data", "date": {"equals": today_date}}
                ]
            }
        }

        try:
            res = requests.post(url_query, headers=NOTION_HEADERS, json=query_payload, timeout=10)
            results = res.json().get("results", []) if res.status_code == 200 else []

            if results:
                page_id = results[0]["id"]
                url_patch = f"https://api.notion.com/v1/pages/{page_id}"
                patch_payload = {
                    "properties": {
                        "Close": {"number": round(close_val, 4)}
                    }
                }
                requests.patch(url_patch, headers=NOTION_HEADERS, json=patch_payload, timeout=10)
                logging.info(f"✅ [CLAUDE CLOSE] [{ticker}] atualizado no Notion (Close={close_val})")
            else:
                post_payload = {
                    "parent": {"database_id": NOTION_CLAUDE_CLOSE_DATABASE_ID},
                    "properties": {
                        title_col: {"title": [{"text": {"content": ticker}}]},
                        "Nome": {"rich_text": [{"text": {"content": nome}}]},
                        "Categoria": {"select": {"name": cat}},
                        "Data": {"date": {"start": today_date}},
                        "Close": {"number": round(close_val, 4)}
                    }
                }
                url_post = "https://api.notion.com/v1/pages"
                post_res = requests.post(url_post, headers=NOTION_HEADERS, json=post_payload, timeout=10)
                logging.info(f"✅ [CLAUDE CLOSE] Linha criada para [{ticker}] (Status: {post_res.status_code})")

        except Exception as e:
            logging.error(f"❌ Erro no Close Diário Claude para [{ticker}]: {e}")

    return True

# -----------------------------------------------------------------------------
# 3. Sync Database #4: Resumo Diário — Regime de Risco — Claude (3efd828b-84a7-4966-8bdf-fe9c93657edd)
# -----------------------------------------------------------------------------
def sync_claude_resumo_regime() -> bool:
    """
    Cria ou verifica 1 única linha por dia com 'Data = YYYY-MM-DD' (ou Sessão YYYY-MM-DD).
    REGRA RÍGIDA: O cron NUNCA escreve o campo 'Regime' — este é exclusivo do Claude (Camada 2).
    """
    if not NOTION_TOKEN or not NOTION_CLAUDE_REGIME_DATABASE_ID:
        logging.error("❌ NOTION_CLAUDE_REGIME_DATABASE_ID não configurado.")
        return False

    today_date = datetime.utcnow().strftime("%Y-%m-%d")
    title_col = get_notion_title_col_name(NOTION_CLAUDE_REGIME_DATABASE_ID, "Data")
    url_query = f"https://api.notion.com/v1/databases/{NOTION_CLAUDE_REGIME_DATABASE_ID}/query"

    # Procurar se já existe linha para a data de hoje (por ex: "2026-07-30" ou "Sessão 2026-07-30")
    session_title = today_date

    query_payload = {
        "filter": {
            "property": title_col,
            "title": {
                "contains": today_date
            }
        }
    }

    try:
        res = requests.post(url_query, headers=NOTION_HEADERS, json=query_payload, timeout=10)
        results = res.json().get("results", []) if res.status_code == 200 else []

        if results:
            logging.info(f"ℹ️ [CLAUDE REGIME] Linha do dia [{today_date}] já existe no Notion. O cron não altera o campo Regime.")
        else:
            # Criar linha só com a propriedade Title (Data)
            post_payload = {
                "parent": {"database_id": NOTION_CLAUDE_REGIME_DATABASE_ID},
                "properties": {
                    title_col: {
                        "title": [
                            {"text": {"content": f"Sessão {today_date}"}}
                        ]
                    }
                }
            }
            url_post = "https://api.notion.com/v1/pages"
            post_res = requests.post(url_post, headers=NOTION_HEADERS, json=post_payload, timeout=10)
            if post_res.status_code in [200, 201]:
                logging.info(f"✅ [CLAUDE REGIME] Linha do dia [{today_date}] criada no Notion com o campo Regime preservado em branco.")
            else:
                logging.error(f"❌ Erro ao criar linha no Resumo de Regime ({post_res.status_code}): {post_res.text}")

        return True
    except Exception as e:
        logging.error(f"❌ Falha ao sincronizar Resumo de Regime Claude: {e}")
        return False
