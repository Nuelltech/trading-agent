# backend/app/services/notion_painel_mercado_service.py
"""
Módulo: notion_painel_mercado_service.py (Integração com a Database Notion 'Painel de Mercado / Matriz de Risco')
Publica os valores mais recentes dos indicadores de mercado validados da BD MySQL para a tabela Painel de Mercado do Notion.
"""

import os
import logging
from datetime import datetime
from typing import Dict, Any, Optional
import requests
from sqlalchemy import text
from app.database import engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

NOTION_API_KEY = os.getenv("NOTION_API_KEY", "")

def fetch_latest_painel_indicators() -> Dict[str, float]:
    """Lê os valores mais recentes de cada indicador de mercado validado na tabela indicator_values"""
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
    try:
        with engine.connect() as conn:
            for ticker, key in tickers.items():
                sql = text("SELECT value FROM indicator_values WHERE symbol = :ticker ORDER BY timestamp DESC LIMIT 1")
                val = conn.execute(sql, {"ticker": ticker}).scalar()
                if val is not None:
                    results[key] = float(val)
                else:
                    results[key] = 0.0
    except Exception as e:
        logging.error(f"Erro ao buscar indicadores no MySQL: {e}")
    return results

def fetch_today_catalyst() -> str:
    """Procura o evento macro de maior impacto agendado para hoje ou o mais relevante recente"""
    today_str = datetime.utcnow().strftime("%Y-%m-%d")
    try:
        with engine.connect() as conn:
            sql = text("""
                SELECT event_name, forecast_val, previous_val 
                FROM economic_calendar 
                WHERE impact_level = 'HIGH' 
                ORDER BY ABS(DATEDIFF(event_timestamp, NOW())) ASC 
                LIMIT 1
            """)
            row = conn.execute(sql).fetchone()
            if row:
                name, forecast, previous = row
                f_str = f" (Previsto: {forecast})" if forecast else ""
                return f"{name}{f_str}"
    except Exception as e:
        logging.error(f"Erro ao buscar catalisador do dia: {e}")
    return "Sem eventos HIGH impact agendados para hoje"

def publish_painel_mercado_to_notion(database_id: Optional[str] = None) -> bool:
    """Escreve um novo registo no Painel de Mercado do Notion com todas as colunas mapeadas"""
    db_id = database_id or os.getenv("NOTION_PAINEL_MERCADO_DATABASE_ID", "") or os.getenv("NOTION_DATABASE_ID", "")
    
    if not NOTION_API_KEY or not db_id:
        logging.warning("⚠️ [NOTION PAINEL] NOTION_API_KEY ou NOTION_PAINEL_MERCADO_DATABASE_ID não configuradas nas variáveis de ambiente.")
        return False
        
    indicators = fetch_latest_painel_indicators()
    catalyst = fetch_today_catalyst()
    today_date = datetime.utcnow().strftime("%Y-%m-%d")
    
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

    url = "https://api.notion.com/v1/pages"
    headers = {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28"
    }

    # Estrutura exata das colunas mostradas no Notion do utilizador
    payload = {
        "parent": {"database_id": db_id},
        "properties": {
            "Data / Sessão": {
                "title": [
                    {"text": {"content": f"Sessão {today_date}"}}
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
    }

    try:
        res = requests.post(url, json=payload, headers=headers, timeout=10)
        if res.status_code in [200, 201]:
            logging.info(f"✅ [NOTION PAINEL] Registo publicado com sucesso no Painel de Mercado do Notion para {today_date}!")
            return True
        else:
            logging.error(f"❌ [NOTION PAINEL] Erro na API do Notion ({res.status_code}): {res.text}")
            return False
    except Exception as e:
        logging.error(f"❌ [NOTION PAINEL] Falha na ligação à API do Notion: {e}")
        return False
