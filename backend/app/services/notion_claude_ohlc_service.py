# backend/app/services/notion_claude_ohlc_service.py
"""
Módulo: notion_claude_ohlc_service.py (Adenda — Painel Diário Claude OHLC)
Lê a tabela 'Configuração de Vigilância' no Notion no início de cada execução,
calcula o OHLC dos ativos vigiados por Claude/Ambos a partir de indicator_values no MySQL,
e faz UPSERT inteligente na tabela 'OHLC Ativos Vigiados — Claude'.

REGRAS RÍGIDAS DE UPSERT:
- Open só é escrito na criação da linha do dia — NUNCA sobrescrito depois.
- High/Low atualizados por max() / min() face ao valor já gravado no Notion.
- Close é sempre o valor mais recente lido.
- Nenhuma dependência do campo Categoria (todos os tickers recebem linha OHLC normal).
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

NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28"
}

def get_claude_watchlist() -> List[Dict[str, Any]]:
    """
    Fase A — Ler a Configuração de Vigilância
    Devolve só os tickers ativos e marcados como Vigiado Por ('Claude', 'Ambos').
    Nunca hardcodar a lista — a tabela do Notion é a única fonte de verdade.
    """
    if not NOTION_TOKEN or not NOTION_CONFIG_DB_ID:
        logging.error("❌ NOTION_TOKEN ou NOTION_CONFIG_DB_ID não configurados para watchlist do Claude.")
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
            ticker_list = props.get("Ticker", {}).get("title", [])
            ticker = ticker_list[0].get("text", {}).get("content", "") if ticker_list else ""

            # Nome (Rich text)
            nome_list = props.get("Nome", {}).get("rich_text", [])
            nome = nome_list[0].get("text", {}).get("content", ticker) if nome_list else ticker

            if ticker:
                watchlist.append({"ticker": ticker, "nome": nome})

        logging.info(f"✅ [CLAUDE WATCHLIST] {len(watchlist)} ativos vigiados por Claude/Ambos encontrados.")
        return watchlist
    except Exception as e:
        logging.error(f"❌ Falha ao ler Configuração de Vigilância para Claude: {e}")
        return []

def fetch_ticker_ohlc_today(ticker: str) -> Dict[str, float]:
    """Lê Open, High, Low, Close do dia para um ticker no MySQL (ou fallback yfinance)"""
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
        logging.warning(f"⚠️ Erro no MySQL para [{ticker}]: {e}. Ativando fallback yfinance...")

    # Fallback via yfinance
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

def sync_claude_ohlc_to_notion() -> bool:
    """
    Fase B — Executa o Upsert na tabela 'OHLC Ativos Vigiados — Claude'
    """
    watchlist = get_claude_watchlist()
    if not watchlist:
        logging.warning("⚠️ Watchlist do Claude vazia. Nenhuma linha OHLC para sincronizar.")
        return True

    if not NOTION_TOKEN or not NOTION_CLAUDE_OHLC_DATABASE_ID:
        logging.error("❌ NOTION_TOKEN ou NOTION_CLAUDE_OHLC_DATABASE_ID não configurados.")
        return False

    today_date = datetime.utcnow().strftime("%Y-%m-%d")
    url_query = f"https://api.notion.com/v1/databases/{NOTION_CLAUDE_OHLC_DATABASE_ID}/query"

    for item in watchlist:
        ticker = item["ticker"]
        nome = item.get("nome", ticker)
        new_ohlc = fetch_ticker_ohlc_today(ticker)

        if new_ohlc["close"] == 0.0:
            logging.warning(f"⚠️ Cotação zerada para [{ticker}]. Ignorando escrita no Claude OHLC.")
            continue

        # Consultar se já existe linha para este ticker no dia de hoje
        query_payload = {
            "filter": {
                "and": [
                    {"property": "Ticker", "title": {"equals": ticker}},
                    {"property": "Data", "date": {"equals": today_date}}
                ]
            }
        }

        try:
            res = requests.post(url_query, headers=NOTION_HEADERS, json=query_payload, timeout=10)
            existing_results = res.json().get("results", []) if res.status_code == 200 else []

            if existing_results:
                # Linha Existente: Atualizar com regras rígidas de max()/min()
                page = existing_results[0]
                page_id = page["id"]
                props = page.get("properties", {})

                curr_high = props.get("High", {}).get("number", new_ohlc["high"])
                curr_low = props.get("Low", {}).get("number", new_ohlc["low"])

                final_high = max(curr_high or new_ohlc["high"], new_ohlc["high"])
                final_low = min(curr_low or new_ohlc["low"], new_ohlc["low"])

                # Open, Data, Ticker, Nome NUNCA são sobrescritos no PATCH
                patch_payload = {
                    "properties": {
                        "High": {"number": round(final_high, 4)},
                        "Low": {"number": round(final_low, 4)},
                        "Close": {"number": round(new_ohlc["close"], 4)}
                    }
                }

                url_patch = f"https://api.notion.com/v1/pages/{page_id}"
                patch_res = requests.patch(url_patch, headers=NOTION_HEADERS, json=patch_payload, timeout=10)
                if patch_res.status_code in [200, 201]:
                    logging.info(f"✅ [CLAUDE OHLC] Linha de [{ticker}] atualizada no Notion (High={final_high}, Low={final_low}, Close={new_ohlc['close']})")
                else:
                    logging.error(f"❌ Erro no PATCH Claude OHLC para [{ticker}]: {patch_res.text}")

            else:
                # Linha Nova: Criar com Open inicial
                post_payload = {
                    "parent": {"database_id": NOTION_CLAUDE_OHLC_DATABASE_ID},
                    "properties": {
                        "Ticker": {
                            "title": [
                                {"text": {"content": ticker}}
                            ]
                        },
                        "Nome": {
                            "rich_text": [
                                {"text": {"content": nome}}
                            ]
                        },
                        "Data": {
                            "date": {"start": today_date}
                        },
                        "Open": {"number": round(new_ohlc["open"], 4)},
                        "High": {"number": round(new_ohlc["high"], 4)},
                        "Low": {"number": round(new_ohlc["low"], 4)},
                        "Close": {"number": round(new_ohlc["close"], 4)}
                    }
                }

                url_post = "https://api.notion.com/v1/pages"
                post_res = requests.post(url_post, headers=NOTION_HEADERS, json=post_payload, timeout=10)
                if post_res.status_code in [200, 201]:
                    logging.info(f"✅ [CLAUDE OHLC] Linha de [{ticker}] criada com sucesso no Notion!")
                else:
                    logging.error(f"❌ Erro no POST Claude OHLC para [{ticker}]: {post_res.text}")

        except Exception as e:
            logging.error(f"❌ Falha no Upsert Claude OHLC para [{ticker}]: {e}")

    return True
