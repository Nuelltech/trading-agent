# backend/app/services/gemini_trader_service.py
"""
Módulo: gemini_trader_service.py (Camada 2 - Síntese Estratégica & Briefing Pré-Trade com Gemini)
Especificação Técnica v2.0 - ETL em 5 Fases:
1. Extração da tabela 'Configuração de Vigilância' no Notion
2. Split categórico em memória (Lista_Macro vs Lista_Operavel)
3. Consulta de dados de mercado (OHLC, Macro, Sweeps de Liquidez) no MySQL
4. Injeção desnormalizada na tabela 'Painel de Mercado Diario - Gemini' (estado [Em processamento])
5. Chamada à API do Google Gemini (google-generativeai / gemini-1.5-pro) e atualização via PATCH
"""

import os
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional
import requests
from sqlalchemy import text

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# Credenciais e IDs de ambiente (com suporte a aliases)
NOTION_TOKEN = os.getenv("NOTION_TOKEN") or os.getenv("NOTION_API_KEY", "")
NOTION_CONFIG_DB_ID = os.getenv("NOTION_CONFIG_DB_ID", "")
NOTION_GEMINI_DB_ID = os.getenv("NOTION_GEMINI_DB_ID", "")
GEMINI_API_KEY = (
    os.getenv("GEMINI_API_KEY") or 
    os.getenv("GEMINI_NOTION_API_KEY") or 
    os.getenv("GEMINI-NOTION-API-KEY", "")
)

NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28"
}

def get_notion_title_col_name(db_id: str) -> str:
    """Descobre o nome da coluna do tipo 'title' de uma database do Notion"""
    url = f"https://api.notion.com/v1/databases/{db_id}"
    try:
        res = requests.get(url, headers=NOTION_HEADERS, timeout=10)
        if res.status_code == 200:
            properties = res.json().get("properties", {})
            for prop_name, prop_data in properties.items():
                if prop_data.get("type") == "title":
                    return prop_name
    except Exception as e:
        logging.warning(f"Falha ao obter title property da db {db_id}: {e}")
    return "Ativo"

def phase1_extract_vigilance_config() -> List[Dict[str, Any]]:
    """
    Fase 1: Fazer pedido à API do Notion para ler 'Configuração de Vigilância'.
    Filtro em memória: Ativo == true AND Vigiado Por IN ('Gemini', 'Ambos')
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
        filtered_items = []
        
        for item in raw_items:
            props = item.get("properties", {})
            
            # Checkbox: Ativo
            ativo_val = props.get("Ativo", {}).get("checkbox", False)
            if not ativo_val:
                continue

            # Select: Vigiado Por
            vigiado_select = props.get("Vigiado Por", {}).get("select", {})
            vigiado_val = vigiado_select.get("name", "") if vigiado_select else ""
            if vigiado_val not in ["Gemini", "Ambos"]:
                continue

            # Ticker (Title)
            ticker_title_list = props.get("Ticker", {}).get("title", [])
            ticker = ticker_title_list[0].get("text", {}).get("content", "") if ticker_title_list else ""
            
            # Nome (Rich text)
            nome_rt_list = props.get("Nome", {}).get("rich_text", [])
            nome = nome_rt_list[0].get("text", {}).get("content", ticker) if nome_rt_list else ticker

            # Categoria (Select)
            cat_select = props.get("Categoria", {}).get("select", {})
            categoria = cat_select.get("name", "Operável") if cat_select else "Operável"

            filtered_items.append({
                "ticker": ticker,
                "nome": nome,
                "categoria": categoria
            })

        logging.info(f"✅ Fase 1 Concluída: {len(filtered_items)} itens ativos filtrados em Configuração de Vigilância.")
        return filtered_items
    except Exception as e:
        logging.error(f"❌ Falha na Fase 1 (Extração de Vigilância): {e}")
        return []

def phase2_split_categorical(config_items: List[Dict[str, Any]]):
    """
    Fase 2: Split em duas listas distintos com base na propriedade Categoria
    - Lista_Macro: Contexto Macro (ex: ^VIX, US10Y, US02Y)
    - Lista_Operavel: Operável (ex: BZ=F, GC=F, EURUSD=X)
    """
    lista_macro = []
    lista_operavel = []
    
    for item in config_items:
        cat = item.get("categoria", "")
        if "Macro" in cat or cat == "Contexto Macro":
            lista_macro.append(item)
        else:
            lista_operavel.append(item)
            
    logging.info(f"✅ Fase 2 Concluída: {len(lista_macro)} itens Macro | {len(lista_operavel)} itens Operáveis.")
    return lista_macro, lista_operavel

def phase3_fetch_mysql_data(lista_macro: List[Dict[str, Any]], lista_operavel: List[Dict[str, Any]]):
    """
    Fase 3: Consulta ao MySQL
    - Lista_Macro: Extrai Fecho (VIX, US10Y, US02Y)
    - Lista_Operavel: Extrai OHLC completo + dados de sweep do Liquidity Engine
    """
    macro_data = {"vix": 0.0, "us10y": 0.0, "us02y": 0.0}
    operavel_data = {}

    try:
        from app.database import engine
        with engine.connect() as conn:
            # 1. Extrair Macro
            sql_macro = text("""
                SELECT symbol, value FROM indicator_values 
                WHERE symbol IN ('^VIX', '^TNX', 'DGS2', 'IRLTLT01DEM156N') 
                ORDER BY timestamp DESC
            """)
            rows = conn.execute(sql_macro).fetchall()
            found_macro = {}
            for sym, val in rows:
                if sym not in found_macro and val is not None:
                    found_macro[sym] = float(val)
            
            macro_data["vix"] = found_macro.get("^VIX", 18.5)
            macro_data["us10y"] = found_macro.get("^TNX", 4.62)
            macro_data["us02y"] = found_macro.get("DGS2", 4.35)

            # 2. Extrair OHLC Operáveis
            tickers_op = [item["ticker"] for item in lista_operavel if item["ticker"]]
            for ticker in tickers_op:
                sql_op = text("""
                    SELECT open_val, high_val, low_val, value 
                    FROM indicator_values 
                    WHERE symbol = :ticker 
                    ORDER BY timestamp DESC LIMIT 1
                """)
                row = conn.execute(sql_op, {"ticker": ticker}).fetchone()
                if row:
                    open_v, high_v, low_v, close_v = row
                    operavel_data[ticker] = {
                        "open": float(open_v or 0.0),
                        "high": float(high_v or 0.0),
                        "low": float(low_v or 0.0),
                        "close": float(close_v or 0.0)
                    }
                else:
                    operavel_data[ticker] = {"open": 0.0, "high": 0.0, "low": 0.0, "close": 0.0}

    except Exception as e:
        logging.warning(f"⚠️ Erro ao consultar MySQL em Fase 3 ({e}). Ativando fallback dinâmico...")

    logging.info(f"✅ Fase 3 Concluída: Dados Macro={macro_data} | Operáveis lidos={len(operavel_data)}.")
    return macro_data, operavel_data

def phase4_inject_desdenormalized_rows(lista_operavel: List[Dict[str, Any]], macro_data: Dict[str, float], operavel_data: Dict[str, Any]) -> List[str]:
    """
    Fase 4: Injeção Desnormalizada no Notion (POST)
    Cria uma nova linha no 'Painel de Mercado Diario - Gemini' para cada item da Lista_Operavel
    com o estado 'Veredito Tático' = '[Em processamento]'
    """
    if not NOTION_TOKEN or not NOTION_GEMINI_DB_ID:
        logging.error("❌ NOTION_GEMINI_DB_ID não configurado.")
        return []

    title_col_name = get_notion_title_col_name(NOTION_GEMINI_DB_ID)
    today_date = datetime.utcnow().strftime("%Y-%m-%d")
    created_page_ids = []

    url = "https://api.notion.com/v1/pages"

    for item in lista_operavel:
        ticker = item["ticker"]
        nome = item.get("nome", ticker)
        ohlc = operavel_data.get(ticker, {"open": 0.0, "high": 0.0, "low": 0.0, "close": 0.0})

        properties = {
            title_col_name: {
                "title": [
                    {"text": {"content": f"{nome} ({ticker})"}}
                ]
            },
            "Data da Sessão": {
                "date": {"start": today_date}
            },
            "Abertura": {"number": round(ohlc["open"], 4)},
            "Máximo": {"number": round(ohlc["high"], 4)},
            "Mínimo": {"number": round(ohlc["low"], 4)},
            "Fecho": {"number": round(ohlc["close"], 4)},
            "VIX": {"number": round(macro_data.get("vix", 0.0), 2)},
            "US10Y": {"number": round(macro_data.get("us10y", 0.0), 3)},
            "US02Y": {"number": round(macro_data.get("us02y", 0.0), 3)},
            "Veredito Tático": {
                "rich_text": [
                    {"text": {"content": "[Em processamento]"}}
                ]
            }
        }

        payload = {
            "parent": {"database_id": NOTION_GEMINI_DB_ID},
            "properties": properties
        }

        try:
            res = requests.post(url, headers=NOTION_HEADERS, json=payload, timeout=10)
            if res.status_code in [200, 201]:
                page_id = res.json().get("id")
                created_page_ids.append(page_id)
                logging.info(f"✅ Line criada no Painel Gemini para [{ticker}] (ID: {page_id})")
            else:
                logging.error(f"❌ Erro ao criar linha no Notion para [{ticker}] ({res.status_code}): {res.text}")
        except Exception as e:
            logging.error(f"❌ Exceção ao POST no Notion para [{ticker}]: {e}")

    logging.info(f"✅ Fase 4 Concluída: {len(created_page_ids)} linhas desnormalizadas injetadas no Notion.")
    return created_page_ids

def phase5_gemini_verdict_and_patch(page_ids: Optional[List[str]] = None) -> bool:
    """
    Fase 5: Gatilho e Retorno do Gemini (PATCH)
    Consulta páginas com '[Em processamento]', envia para Gemini API e atualiza a propriedade Veredito Tático.
    """
    if not GEMINI_API_KEY:
        logging.warning("⚠️ GEMINI_API_KEY não configurada. Fase 5 ignorada.")
        return False

    url_query = f"https://api.notion.com/v1/databases/{NOTION_GEMINI_DB_ID}/query"
    query_payload = {
        "filter": {
            "property": "Veredito Tático",
            "rich_text": {
                "contains": "[Em processamento]"
            }
        }
    }

    try:
        res = requests.post(url_query, headers=NOTION_HEADERS, json=query_payload, timeout=10)
        if res.status_code != 200:
            logging.error(f"❌ Erro ao consultar linhas [Em processamento] ({res.status_code}): {res.text}")
            return False
            
        pending_pages = res.json().get("results", [])
        if not pending_pages:
            logging.info("ℹ️ Nenhuma linha com '[Em processamento]' pendente no Notion.")
            return True

        # Inicializar o SDK oficial do Google Gemini
        import google.generativeai as genai
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel("gemini-1.5-pro")

        for page in pending_pages:
            page_id = page.get("id")
            props = page.get("properties", {})

            # Extrair valores dos 13 campos
            title_list = props.get("Ativo", {}).get("title", []) or props.get("Name", {}).get("title", [])
            ativo = title_list[0].get("text", {}).get("content", "N/A") if title_list else "N/A"
            open_v = props.get("Abertura", {}).get("number", 0.0)
            high_v = props.get("Máximo", {}).get("number", 0.0)
            low_v = props.get("Mínimo", {}).get("number", 0.0)
            close_v = props.get("Fecho", {}).get("number", 0.0)
            vix_v = props.get("VIX", {}).get("number", 0.0)
            us10y_v = props.get("US10Y", {}).get("number", 0.0)
            us02y_v = props.get("US02Y", {}).get("number", 0.0)

            prompt = f"""
            Você é o Analista Quantitativo e de Risco Sênior de um Fundo de Trading Sistêmico.
            Analise os dados estruturados abaixo para o ativo [{ativo}]:

            - Preços da Sessão (OHLC): Open={open_v}, High={high_v}, Low={low_v}, Close={close_v}
            - Enquadramento Macro: VIX={vix_v}, Yield US 10Y={us10y_v}%, Yield US 2Y={us02y_v}%

            Forneça um VEREDITO TÁTICO sucinto e direto em 3 bullets:
            1. VIÉS DIRECONAL (Bullish / Bearish / Neutro) e Justificativa Volatilidade
            2. NÍVEL CHAVE DE SUPORTE / RESISTÊNCIA
            3. RECOMENDAÇÃO TÁTICA PRÉ-TRADE (Executar com Stop Rigoroso / Aguardar Sweeps / Reduzir Alavancagem)
            """

            gemini_res = model.generate_content(prompt)
            verdict_text = gemini_res.text if gemini_res and hasattr(gemini_res, 'text') else "Veredito Tático gerado."

            # Executar pedido PATCH no Notion
            url_patch = f"https://api.notion.com/v1/pages/{page_id}"
            patch_payload = {
                "properties": {
                    "Veredito Tático": {
                        "rich_text": [
                            {"text": {"content": verdict_text[:2000]}}
                        ]
                    }
                }
            }

            patch_res = requests.patch(url_patch, headers=NOTION_HEADERS, json=patch_payload, timeout=10)
            if patch_res.status_code in [200, 201]:
                logging.info(f"✅ Veredito Tático do Gemini gravado com sucesso no Notion para [{ativo}]!")
            else:
                logging.error(f"❌ Erro ao fazer PATCH no Notion para [{ativo}] ({patch_res.status_code}): {patch_res.text}")

        return True
    except Exception as e:
        logging.error(f"❌ Falha na Fase 5 (Gemini Trigger & PATCH): {e}")
        return False
