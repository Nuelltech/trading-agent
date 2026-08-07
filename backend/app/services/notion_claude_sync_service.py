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
import json
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional
import requests
import yfinance as yf
from sqlalchemy import text
from app.database import engine

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

def get_notion_db_schema_properties(db_id: str) -> Dict[str, str]:
    """Retorna um dicionário {property_name: property_type} da database do Notion"""
    url = f"https://api.notion.com/v1/databases/{db_id}"
    try:
        res = requests.get(url, headers=NOTION_HEADERS, timeout=10)
        if res.status_code == 200:
            properties = res.json().get("properties", {})
            schema = {p_name: p_data.get("type") for p_name, p_data in properties.items()}
            logging.info(f"🔍 Schema detetado para db [{db_id}]: {schema}")
            return schema
    except Exception as e:
        logging.warning(f"Falha ao ler propriedades da db {db_id}: {e}")
    return {}

def get_notion_title_col_name(db_id: str, default_name: str = "Ticker") -> str:
    """Descobre o nome da coluna do tipo 'title' de uma database do Notion"""
    schema = get_notion_db_schema_properties(db_id)
    for prop_name, prop_type in schema.items():
        if prop_type == "title":
            return prop_name
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

def fetch_ticker_ohlc(ticker: str, target_date: Optional[str] = None) -> Optional[Dict[str, float]]:
    """
    Lê Open, High, Low, Close para o ticker na data `target_date` (YYYY-MM-DD).
    Se target_date não for fornecido, assume a data UTC atual.
    
    REGRA RIGOROSA DE ROLLOVER:
    Se não existirem dados na MySQL ou no yFinance especificamente para a data `target_date`
    (ex: mercado ainda não abriu no novo dia civil UTC), a função retorna None.
    Isto IMPEDE que dados do dia anterior sejam propagados e rotulados como a nova data no Notion.
    """
def fetch_ticker_ohlc(ticker: str, target_date: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Recupera a cotação OHLC para o ticker a partir do MySQL (indicator_values).
    Tenta primeiro a data exata solicitada. Se não houver linha na DB para a data exata,
    retorna o registo mais recente disponível na DB com a sua data real.
    Garante que cotações existentes no MySQL sejam SEMPRE sincronizadas para o Notion.
    """
    strict_target = target_date is not None
    if not target_date:
        target_date = datetime.utcnow().strftime("%Y-%m-%d")

    ohlc = {"open": 0.0, "high": 0.0, "low": 0.0, "close": 0.0, "date": target_date}

    # 1. Tentar MySQL por data exata primeiro
    try:
        from app.database import engine
        with engine.connect() as conn:
            sql_exact = text("""
                SELECT open_val, high_val, low_val, value, DATE(timestamp) 
                FROM indicator_values 
                WHERE symbol = :ticker AND DATE(timestamp) = :target_date
                ORDER BY timestamp DESC LIMIT 1
            """)
            row = conn.execute(sql_exact, {"ticker": ticker, "target_date": target_date}).fetchone()
            if row and row[3] is not None:
                ohlc["open"] = float(row[0] or row[3])
                ohlc["high"] = float(row[1] or row[3])
                ohlc["low"] = float(row[2] or row[3])
                ohlc["close"] = float(row[3])
                ohlc["date"] = str(row[4])
                return ohlc

            # 1b. Se não há registo para a data exata, usar a última cotação registada na DB com a sua data real
            sql_latest = text("""
                SELECT open_val, high_val, low_val, value, DATE(timestamp) 
                FROM indicator_values 
                WHERE symbol = :ticker 
                ORDER BY timestamp DESC LIMIT 1
            """)
            latest_row = conn.execute(sql_latest, {"ticker": ticker}).fetchone()
            if latest_row and latest_row[3] is not None:
                ohlc["open"] = float(latest_row[0] or latest_row[3])
                ohlc["high"] = float(latest_row[1] or latest_row[3])
                ohlc["low"] = float(latest_row[2] or latest_row[3])
                ohlc["close"] = float(latest_row[3])
                ohlc["date"] = str(latest_row[4])
                return ohlc
    except Exception as e:
        logging.warning(f"⚠️ Erro no MySQL para [{ticker}]: {e}.")

    # 2. Fallback via yfinance se não existir registo na DB MySQL
    if ticker.startswith("IRLTLT01") or ticker in ["DGS2", "VSTOXX"]:
        return None

    try:
        df = yf.Ticker(ticker).history(period="5d")
        if not df.empty:
            last_date_str = str(df.index[-1])[:10]
            if strict_target and last_date_str != target_date:
                logging.info(f"ℹ️ [{ticker}] Sessão de {target_date} ainda não iniciada. Dados yfinance são de {last_date_str}.")
                return None

            last = df.iloc[-1]
            o_val = float(last.get("Open", last.get("Close", 0.0)))
            c_val = float(last.get("Close", 0.0))
            h_val = float(last.get("High", c_val))
            l_val = float(last.get("Low", c_val))

            ohlc["open"] = o_val
            ohlc["close"] = c_val
            ohlc["high"] = max(h_val, o_val, c_val)
            ohlc["low"] = min(l_val, o_val, c_val) if l_val > 0 else min(o_val, c_val)
            ohlc["date"] = last_date_str
            return ohlc
    except Exception as ex:
        logging.warning(f"Falha yfinance para [{ticker}]: {ex}")

    return None



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
        new_ohlc = fetch_ticker_ohlc(ticker, today_date)

        if not new_ohlc or new_ohlc["close"] == 0.0:
            logging.info(f"ℹ️ [{ticker}] Sem dados disponíveis para sincronizar no Notion.")
            continue

        entry_date = new_ohlc.get("date", today_date)

        query_payload = {
            "filter": {
                "and": [
                    {"property": title_col, "title": {"equals": ticker}},
                    {"property": "Data", "date": {"equals": entry_date}}
                ]
            }
        }

        try:
            res = requests.post(url_query, headers=NOTION_HEADERS, json=query_payload, timeout=10)
            existing_results = res.json().get("results", []) if res.status_code == 200 else []

            if existing_results:
                page = existing_results[0]
                page_id = page["id"]

                patch_payload = {
                    "properties": {
                        "Open": {"number": round(new_ohlc["open"], 4)},
                        "High": {"number": round(new_ohlc["high"], 4)},
                        "Low": {"number": round(new_ohlc["low"], 4)},
                        "Close": {"number": round(new_ohlc["close"], 4)}
                    }
                }

                url_patch = f"https://api.notion.com/v1/pages/{page_id}"
                requests.patch(url_patch, headers=NOTION_HEADERS, json=patch_payload, timeout=10)
                logging.info(f"✅ [CLAUDE OHLC] [{ticker}] atualizado para {entry_date} (Open={new_ohlc['open']}, High={new_ohlc['high']}, Low={new_ohlc['low']}, Close={new_ohlc['close']})")

            else:
                post_payload = {
                    "parent": {"database_id": NOTION_CLAUDE_OHLC_DATABASE_ID},
                    "properties": {
                        title_col: {"title": [{"text": {"content": ticker}}]},
                        "Nome": {"rich_text": [{"text": {"content": nome}}]},
                        "Data": {"date": {"start": entry_date}},
                        "Open": {"number": round(new_ohlc["open"], 4)},
                        "High": {"number": round(new_ohlc["high"], 4)},
                        "Low": {"number": round(new_ohlc["low"], 4)},
                        "Close": {"number": round(new_ohlc["close"], 4)}
                    }
                }
                url_post = "https://api.notion.com/v1/pages"
                requests.post(url_post, headers=NOTION_HEADERS, json=post_payload, timeout=10)
                logging.info(f"✅ [CLAUDE OHLC] Linha criada para [{ticker}] na data {entry_date}")

        except Exception as e:
            logging.error(f"❌ Falha no Upsert Claude OHLC para [{ticker}]: {e}")

    return True

# -----------------------------------------------------------------------------
# 2. Sync Database #3: Close Diário — Todos os Ativos — Claude (25fd82e4-92d7-4401-af67-a39daeec9e0b)
# -----------------------------------------------------------------------------
def sync_claude_close_todos_ativos() -> bool:
    """
    Processa SEMPRE todos os ativos do indicators_catalog (sem depender da Configuração de Vigilância).
    Upsert por dia: 1 linha por ticker/dia. Escreve Ticker (title), Nome, Categoria, Data, Close.
    """
    if not NOTION_TOKEN or not NOTION_CLAUDE_CLOSE_DATABASE_ID:
        logging.error("❌ NOTION_CLAUDE_CLOSE_DATABASE_ID não configurado.")
        return False

    db_schema = get_notion_db_schema_properties(NOTION_CLAUDE_CLOSE_DATABASE_ID)
    title_col = "Ticker"
    for p_name, p_type in db_schema.items():
        if p_type == "title":
            title_col = p_name
            break

    date_col = "Data" if "Data" in db_schema else ("Date" if "Date" in db_schema else "Data")
    close_col = "Close" if "Close" in db_schema else ("Fecho" if "Fecho" in db_schema else "Close")
    name_col = "Nome" if "Nome" in db_schema else ("Name" if "Name" in db_schema else "Nome")
    cat_col = "Categoria" if "Categoria" in db_schema else ("Category" if "Category" in db_schema else "Categoria")

    today_date = datetime.utcnow().strftime("%Y-%m-%d")

    # Obter catálogo completo de ativos no MySQL
    all_indicators = []
    try:
        from app.database import engine
        with engine.connect() as conn:
            sql = text("SELECT ticker, name, category FROM indicators_catalog WHERE is_active = TRUE")
            rows = conn.execute(sql).fetchall()
            for r in rows:
                all_indicators.append({"ticker": r[0], "nome": r[1] or r[0], "categoria": r[2] or "Geral"})
        logging.info(f"✅ [CLAUDE CLOSE] {len(all_indicators)} ativos carregados do indicators_catalog da DB.")
    except Exception as e:
        logging.warning(f"⚠️ Erro ao consultar indicators_catalog ({e}). Carregando catálogo fallback JSON...")
        fallback_path = os.path.join(os.path.dirname(__file__), "..", "..", "config", "indicators_catalog_fallback.json")
        try:
            with open(fallback_path, "r", encoding="utf-8") as f:
                items = json.load(f)
                all_indicators = [{"ticker": i["ticker"], "nome": i.get("name", i["ticker"]), "categoria": i.get("category", "Geral")} for i in items]
            logging.info(f"✅ [CLAUDE CLOSE] Fallback JSON carregado com sucesso ({len(all_indicators)} ativos).")
        except Exception as json_err:
            logging.error(f"❌ Erro ao ler fallback JSON para Notion Sync: {json_err}")

    url_query = f"https://api.notion.com/v1/databases/{NOTION_CLAUDE_CLOSE_DATABASE_ID}/query"

    for ind in all_indicators:
        ticker = ind["ticker"]
        nome = ind["nome"]
        cat = ind.get("categoria", "Geral")
        ohlc = fetch_ticker_ohlc(ticker, today_date)
        if not ohlc or ohlc["close"] == 0.0:
            logging.info(f"ℹ️ [{ticker}] Sem cotação disponível. Linha no Notion mantida inalterada.")
            continue
        close_val = ohlc["close"]
        entry_date = ohlc.get("date", today_date)

        query_payload = {
            "filter": {
                "and": [
                    {"property": title_col, "title": {"equals": ticker}},
                    {"property": date_col, "date": {"equals": entry_date}}
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
                        close_col: {"number": round(close_val, 4)}
                    }
                }
                patch_res = requests.patch(url_patch, headers=NOTION_HEADERS, json=patch_payload, timeout=10)
                if patch_res.status_code in [200, 201]:
                    logging.info(f"✅ [CLAUDE CLOSE] [{ticker}] atualizado no Notion para {entry_date} (Close={close_val})")
                else:
                    logging.error(f"❌ [CLAUDE CLOSE] Erro no PATCH [{ticker}] ({patch_res.status_code}): {patch_res.text}")
            else:
                # Construir propriedades dinamicamente de acordo com o schema retornado pelo Notion
                props = {
                    title_col: {"title": [{"text": {"content": ticker}}]},
                    close_col: {"number": round(close_val, 4)},
                    date_col: {"date": {"start": entry_date}}
                }

                if name_col in db_schema:
                    name_type = db_schema[name_col]
                    if name_type == "select":
                        props[name_col] = {"select": {"name": nome}}
                    elif name_type == "rich_text":
                        props[name_col] = {"rich_text": [{"text": {"content": nome}}]}

                if cat_col in db_schema:
                    cat_type = db_schema[cat_col]
                    if cat_type == "select":
                        props[cat_col] = {"select": {"name": cat}}
                    elif cat_type == "rich_text":
                        props[cat_col] = {"rich_text": [{"text": {"content": cat}}]}

                post_payload = {
                    "parent": {"database_id": NOTION_CLAUDE_CLOSE_DATABASE_ID},
                    "properties": props
                }

                url_post = "https://api.notion.com/v1/pages"
                post_res = requests.post(url_post, headers=NOTION_HEADERS, json=post_payload, timeout=10)
                if post_res.status_code in [200, 201]:
                    logging.info(f"✅ [CLAUDE CLOSE] Linha criada para [{ticker}] em {entry_date} (Close={close_val})")
                else:
                    logging.error(f"❌ [CLAUDE CLOSE] Erro ao criar linha [{ticker}] ({post_res.status_code}): {post_res.text}")

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

    # Procurar se já existe linha para a data de hoje
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
