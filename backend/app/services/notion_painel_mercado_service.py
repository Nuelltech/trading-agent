# backend/app/services/notion_painel_mercado_service.py
"""
Módulo: notion_painel_mercado_service.py (Integração com a Database Notion 'Painel de Mercado / Matriz de Risco')
Publica/Atualiza (UPSERT) os valores mais recentes dos indicadores de mercado validados para a tabela Painel de Mercado do Notion.
Se a linha da Sessão de Hoje já existir, ATUALIZA em tempo real. Se for um novo dia, CRIA uma linha nova.
"""

import os
import logging
from datetime import datetime
from typing import Dict, Any, Optional
import requests
import yfinance as yf
from sqlalchemy import text

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

NOTION_API_KEY = os.getenv("NOTION_API_KEY", "")

def fetch_latest_painel_indicators() -> Dict[str, float]:
    """Lê os valores mais recentes no MySQL ou faz fallback dinâmico para yfinance se o MySQL estiver offline"""
    tickers = {
        "BZ=F": "brent",
        "GC=F": "ouro",
        "HG=F": "cobre",
        "EURUSD=X": "eurusd",
        "DX-Y.NYB": "dxy",
        "^GSPC": "sp500",
        "^NDX": "nasdaq",
        "^TNX": "us10y",
        "^VIX": "vix",
        "IRLTLT01DEM156N": "bund10y"
    }
    results = {}
    
    # 1. Tentar ler do MySQL
    try:
        from app.database import engine
        with engine.connect() as conn:
            for ticker, key in tickers.items():
                sql = text("SELECT value FROM indicator_values WHERE symbol = :ticker ORDER BY timestamp DESC LIMIT 1")
                val = conn.execute(sql, {"ticker": ticker}).scalar()
                if val is not None:
                    results[key] = float(val)
    except Exception as e:
        logging.warning(f"⚠️ Ligação ao MySQL indisponível ({e}). Ativando fallback yfinance...")

    # 2. Fallback via yfinance para indicadores em falta
    yf_mapping = {
        "brent": "BZ=F",
        "ouro": "GC=F",
        "cobre": "HG=F",
        "eurusd": "EURUSD=X",
        "dxy": "DX-Y.NYB",
        "sp500": "^GSPC",
        "nasdaq": "^NDX",
        "us10y": "^TNX",
        "vix": "^VIX"
    }
    
    missing_keys = [k for k in yf_mapping if k not in results or results[k] == 0.0]
    if missing_keys:
        for k in missing_keys:
            try:
                sym = yf_mapping[k]
                df = yf.Ticker(sym).history(period="5d")
                if not df.empty and "Close" in df.columns:
                    results[k] = float(df["Close"].iloc[-1])
            except Exception as ex:
                logging.warning(f"Falha ao buscar fallback yfinance para {k}: {ex}")

    return results

def get_notion_title_property_name(db_id: str, headers: Dict[str, str]) -> str:
    """Descobre dinamicamente o nome da coluna do tipo 'title' na database do Notion"""
    url = f"https://api.notion.com/v1/databases/{db_id}"
    try:
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            properties = res.json().get("properties", {})
            for prop_name, prop_data in properties.items():
                if prop_data.get("type") == "title":
                    logging.info(f"🔍 Coluna 'title' detetada no Notion: [{prop_name}]")
                    return prop_name
    except Exception as e:
        logging.warning(f"Não foi possível obter a estrutura do Notion ({e}). Usando 'Name' como fallback.")
    return "Name"

def find_existing_today_page_id(db_id: str, title_col_name: str, session_title: str, headers: Dict[str, str]) -> Optional[str]:
    """Procura se já existe uma linha criada na database do Notion para a sessão de hoje"""
    url = f"https://api.notion.com/v1/databases/{db_id}/query"
    query_payload = {
        "filter": {
            "property": title_col_name,
            "title": {
                "equals": session_title
            }
        }
    }
    try:
        res = requests.post(url, json=query_payload, headers=headers, timeout=10)
        if res.status_code == 200:
            results = res.json().get("results", [])
            if results:
                page_id = results[0].get("id")
                logging.info(f"🔍 Encontrada linha existente para a sessão de hoje no Notion (ID: {page_id})")
                return page_id
    except Exception as e:
        logging.warning(f"Falha ao consultar existência de linha no Notion: {e}")
    return None

def publish_painel_mercado_to_notion(database_id: Optional[str] = None) -> bool:
    """Publica ou Atualiza (UPSERT) a linha da Sessão de Hoje no Painel de Mercado do Notion"""
    db_id = database_id or os.getenv("NOTION_PAINEL_MERCADO_DATABASE_ID", "") or os.getenv("NOTION_DATABASE_ID", "")
    
    if not NOTION_API_KEY or not db_id:
        logging.warning("⚠️ [NOTION PAINEL] NOTION_API_KEY ou NOTION_PAINEL_MERCADO_DATABASE_ID não configuradas nas variáveis de ambiente.")
        return False
        
    headers = {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28"
    }

    # Descobrir o nome da coluna de título dinamicamente
    title_col_name = get_notion_title_property_name(db_id, headers)

    indicators = fetch_latest_painel_indicators()
    today_date = datetime.utcnow().strftime("%Y-%m-%d")
    session_title = f"Sessão {today_date}"
    
    brent = indicators.get("brent", 0.0)
    ouro = indicators.get("ouro", 0.0)
    cobre = indicators.get("cobre", 0.0)
    eurusd = indicators.get("eurusd", 0.0)
    dxy = indicators.get("dxy", 0.0)
    sp500 = indicators.get("sp500", 0.0)
    nasdaq = indicators.get("nasdaq", 0.0)
    us10y = indicators.get("us10y", 0.0)
    vix = indicators.get("vix", 0.0)
    bund10y = indicators.get("bund10y", 0.0)
    
    ratio_cobre_ouro = round(cobre / ouro, 6) if ouro > 0 and cobre > 0 else 0.0
    regime = "Risk-Off" if vix >= 25.0 else ("Risk-On" if vix <= 15.0 else "Neutro / Monitorização")
    catalyst = fetch_today_catalyst() if 'fetch_today_catalyst' in globals() else ""

    properties_payload = {
        title_col_name: {
            "title": [
                {"text": {"content": session_title}}
            ]
        },
        "Brent": {"number": round(brent, 2)},
        "Bund 10Y": {"number": round(bund10y, 3)},
        "Catalisador do Dia": {
            "rich_text": [
                {"text": {"content": catalyst}}
            ]
        },
        "DXY": {"number": round(dxy, 2)},
        "EUR/USD": {"number": round(eurusd, 4)},
        "Nasdaq 100": {"number": round(nasdaq, 2)},
        "Ouro": {"number": round(ouro, 2)},
        "Regime": {"select": {"name": regime}},
        "Rácio Cobre/Ouro": {"number": ratio_cobre_ouro},
        "S&P 500": {"number": round(sp500, 2)},
        "US 10Y": {"number": round(us10y, 3)},
        "VIX": {"number": round(vix, 2)}
    }

    # Verificar se a linha de hoje já existe para ATUALIZAR (PATCH) ou CRIAR (POST)
    existing_page_id = find_existing_today_page_id(db_id, title_col_name, session_title, headers)

    if existing_page_id:
        url = f"https://api.notion.com/v1/pages/{existing_page_id}"
        payload = {"properties": properties_payload}
        action_verb = "atualizada"
        http_method = requests.patch
    else:
        url = "https://api.notion.com/v1/pages"
        payload = {
            "parent": {"database_id": db_id},
            "properties": properties_payload
        }
        action_verb = "criada"
        http_method = requests.post

    try:
        res = http_method(url, json=payload, headers=headers, timeout=10)
        if res.status_code in [200, 201]:
            logging.info(f"✅ [NOTION PAINEL] Linha da [{session_title}] {action_verb} com sucesso no Notion!")
            return True
        else:
            logging.error(f"❌ [NOTION PAINEL] Erro na API do Notion ({res.status_code}): {res.text}")
            return False
    except Exception as e:
        logging.error(f"❌ [NOTION PAINEL] Falha na ligação à API do Notion: {e}")
        return False
