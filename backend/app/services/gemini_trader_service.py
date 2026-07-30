# backend/app/services/gemini_trader_service.py
"""
Módulo: gemini_trader_service.py (Camada 2 - Síntese Estratégica & Briefing Pré-Trade com Gemini)
Especificação Técnica — Fase de Enriquecimento Analítico (Python / Pandas):
1. Extração da 'Configuração de Vigilância' no Notion.
2. Split categórico em memória (Lista_Macro vs Lista_Operavel).
3. Consulta de histórico de 50 sessões no MySQL e cálculo de métricas em Pandas (SMA20/50, Z-Score 20D, ATR_14D, Var 5D).
4. Consulta do Calendário Económico para as próximas 48h.
5. Injeção desnormalizada na tabela 'Painel de Mercado Diario - Gemini' (estado [Em processamento]).
6. Chamada à API do Google Gemini (REST API nativa + SDK fallback) e atualização via PATCH (Veredito, Viés, Status [Concluído], Eventos 48h).
"""

import os
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional
import requests
import pandas as pd
import numpy as np
import yfinance as yf
from sqlalchemy import text

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# Credenciais e IDs de ambiente (com suporte a aliases)
NOTION_TOKEN = os.getenv("NOTION_TOKEN") or os.getenv("NOTION_API_KEY", "")
NOTION_CONFIG_DB_ID = os.getenv("NOTION_CONFIG_DB_ID", "fb3a2102-c785-46c9-b2b4-5adecd9d5482")
NOTION_GEMINI_DB_ID = os.getenv("NOTION_GEMINI_DB_ID", "3efd828b-84a7-4966-8bdf-fe9c93657edd")
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

def get_notion_db_schema_properties(db_id: str) -> Dict[str, str]:
    """Retorna um dicionário {property_name: property_type} da database do Notion"""
    url = f"https://api.notion.com/v1/databases/{db_id}"
    try:
        res = requests.get(url, headers=NOTION_HEADERS, timeout=10)
        if res.status_code == 200:
            properties = res.json().get("properties", {})
            schema = {p_name: p_data.get("type") for p_name, p_data in properties.items()}
            logging.info(f"🔍 Schema detetado para Gemini db [{db_id}]: {schema}")
            return schema
    except Exception as e:
        logging.warning(f"Falha ao ler propriedades da db {db_id}: {e}")
    return {}

def get_notion_title_col_name(db_id: str, default_name: str = "Ativo") -> str:
    """Descobre o nome da coluna do tipo 'title' de uma database do Notion"""
    schema = get_notion_db_schema_properties(db_id)
    for prop_name, prop_type in schema.items():
        if prop_type == "title":
            return prop_name
    return default_name

def phase1_extract_vigilance_config() -> List[Dict[str, Any]]:
    """
    Fase 1: Ler 'Configuração de Vigilância' no Notion.
    Filtro: Ativo == true AND Vigiado Por IN ('Gemini', 'Ambos')
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

            ativo_val = props.get("Ativo", {}).get("checkbox", False)
            if not ativo_val:
                continue

            vigiado_select = props.get("Vigiado Por", {}).get("select", {})
            vigiado_val = vigiado_select.get("name", "") if vigiado_select else ""
            if vigiado_val not in ["Gemini", "Ambos"]:
                continue

            ticker_title_list = props.get("Ticker", {}).get("title", []) or props.get("Name", {}).get("title", []) or props.get("Ativo", {}).get("title", [])
            ticker = ticker_title_list[0].get("text", {}).get("content", "") if ticker_title_list else ""

            nome_rt_list = props.get("Nome", {}).get("rich_text", [])
            nome = nome_rt_list[0].get("text", {}).get("content", ticker) if nome_rt_list else ticker

            cat_select = props.get("Categoria", {}).get("select", {})
            categoria = cat_select.get("name", "Operável") if cat_select else "Operável"

            if ticker:
                filtered_items.append({
                    "ticker": ticker,
                    "nome": nome,
                    "categoria": categoria
                })

        logging.info(f"✅ [FASE 1] {len(filtered_items)} ativos lidos da Configuração de Vigilância para o Gemini.")
        return filtered_items
    except Exception as e:
        logging.error(f"❌ Falha na Fase 1 Gemini: {e}")
        return []

def phase2_split_lists(vigilance_items: List[Dict[str, Any]]):
    """Fase 2: Divide itens em Lista_Macro e Lista_Operavel"""
    macro = [x for x in vigilance_items if x["categoria"] == "Contexto Macro"]
    operavel = [x for x in vigilance_items if x["categoria"] != "Contexto Macro"]
    logging.info(f"✅ [FASE 2] {len(macro)} Ativos Macro | {len(operavel)} Ativos Operáveis.")
    return macro, operavel

phase2_split_categorical = phase2_split_lists

def extract_50_sessions_dataframe(ticker: str) -> pd.DataFrame:
    """Extrai até 50 sessões históricas ordenadas por data do MySQL (ou fallback yfinance)"""
    try:
        from app.database import engine
        with engine.connect() as conn:
            sql = text("""
                SELECT timestamp, open_val, high_val, low_val, value AS close_val, volume
                FROM indicator_values
                WHERE symbol = :ticker
                ORDER BY timestamp DESC
                LIMIT 50
            """)
            rows = conn.execute(sql, {"ticker": ticker}).fetchall()
            if rows and len(rows) > 0:
                df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
                df["timestamp"] = pd.to_datetime(df["timestamp"])
                df = df.sort_values("timestamp").reset_index(drop=True)
                for col in ["open", "high", "low", "close", "volume"]:
                    df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
                return df
    except Exception as e:
        logging.warning(f"⚠️ Erro MySQL para [{ticker}] (50 sessões): {e}. Usando yfinance...")

    try:
        df_yf = yf.Ticker(ticker).history(period="3m")
        if not df_yf.empty:
            df_yf = df_yf.reset_index()
            cols_map = {"Date": "timestamp", "Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"}
            df_yf = df_yf.rename(columns=cols_map)
            df_yf = df_yf.sort_values("timestamp").tail(50).reset_index(drop=True)
            return df_yf
    except Exception as ex:
        logging.warning(f"Falha yfinance para [{ticker}]: {ex}")

    return pd.DataFrame()

def compute_pandas_enrichment_metrics(df: pd.DataFrame) -> Dict[str, Any]:
    """Calcula estatísticas vetoriais em memória usando Pandas (SMA20, SMA50, Z-Score 20D, ATR_14D, Var_5D)"""
    metrics = {
        "variacao_5d": 0.0,
        "sma20": 0.0,
        "sma50": 0.0,
        "distancia_sma20": 0.0,
        "distancia_sma50": 0.0,
        "desvio_padrao_20d": 0.0,
        "z_score_20d": 0.0,
        "atr_14d": 0.0,
        "amplitude_vs_atr": "1.0x"
    }

    if df.empty or len(df) < 2:
        return metrics

    close = df["close"]
    high = df["high"]
    low = df["low"]

    close_hoje = close.iloc[-1]
    high_hoje = high.iloc[-1]
    low_hoje = low.iloc[-1]
    amplitude_hoje = high_hoje - low_hoje

    # 1. Variacao_5D
    if len(close) >= 6 and close.iloc[-6] != 0.0:
        metrics["variacao_5d"] = float((close_hoje / close.iloc[-6]) - 1.0)
    elif len(close) >= 2 and close.iloc[0] != 0.0:
        metrics["variacao_5d"] = float((close_hoje / close.iloc[0]) - 1.0)

    # 2. SMA20 & SMA50
    metrics["sma20"] = float(close.tail(20).mean())
    metrics["sma50"] = float(close.tail(50).mean())

    if metrics["sma20"] > 0:
        metrics["distancia_sma20"] = float((close_hoje / metrics["sma20"]) - 1.0)
    if metrics["sma50"] > 0:
        metrics["distancia_sma50"] = float((close_hoje / metrics["sma50"]) - 1.0)

    # 3. Z-Score 20D
    std_20 = float(close.tail(20).std(ddof=0))
    metrics["desvio_padrao_20d"] = std_20
    if std_20 > 0:
        metrics["z_score_20d"] = float((close_hoje - metrics["sma20"]) / std_20)

    # 4. ATR_14D
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.tail(14).mean()
    metrics["atr_14d"] = float(atr) if not pd.isna(atr) else 0.0

    if metrics["atr_14d"] > 0:
        ratio = amplitude_hoje / metrics["atr_14d"]
        metrics["amplitude_vs_atr"] = f"{ratio:.1f}x"

    return metrics

def get_radar_eventos_48h() -> str:
    """Consulta os eventos económicos Tier 1 / HIGH das próximas 48h no MySQL"""
    events_str = "Sem eventos Tier 1 previstos para as próximas 48h."
    try:
        from app.database import engine
        with engine.connect() as conn:
            sql = text("""
                SELECT event_name, country, event_timestamp 
                FROM economic_calendar 
                WHERE impact_level IN ('HIGH', 'MEDIUM')
                  AND event_timestamp >= NOW() 
                  AND event_timestamp <= DATE_ADD(NOW(), INTERVAL 48 HOUR)
                ORDER BY event_timestamp ASC
                LIMIT 5
            """)
            rows = conn.execute(sql).fetchall()
            if rows:
                formatted_list = []
                for r in rows:
                    t_str = r[2].strftime("%d/%m %H:%M") if hasattr(r[2], "strftime") else str(r[2])
                    formatted_list.append(f"{r[0]} ({r[1]} - {t_str})")
                events_str = " | ".join(formatted_list)
    except Exception as e:
        logging.warning(f"⚠️ Erro ao consultar calendário 48h: {e}")

    return events_str

def phase3_query_market_data(macro_list: List[Dict[str, Any]], operavel_list: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Fase 3: Extrai 50 sessões com Pandas para cada ativo operável e macro.
    Calcula métricas quantitativas e constrói o pacote desnormalizado para o Gemini.
    """
    operavel_packages = []
    radar_48h = get_radar_eventos_48h()

    # Pre-fetch Macro Indicators (VIX, US10Y, US02Y)
    df_vix = extract_50_sessions_dataframe("^VIX")
    metrics_vix = compute_pandas_enrichment_metrics(df_vix) if not df_vix.empty else {}
    vix_close = float(df_vix.iloc[-1]["close"]) if not df_vix.empty else 0.0

    df_tnx = extract_50_sessions_dataframe("^TNX")
    tnx_close = float(df_tnx.iloc[-1]["close"]) if not df_tnx.empty else 0.0

    df_dgs2 = extract_50_sessions_dataframe("DGS2")
    dgs2_close = float(df_dgs2.iloc[-1]["close"]) if not df_dgs2.empty else 0.0

    spread_10y_2y = tnx_close - dgs2_close

    for item in operavel_list:
        ticker = item["ticker"]
        nome = item["nome"]
        df = extract_50_sessions_dataframe(ticker)

        if df.empty:
            continue

        metrics = compute_pandas_enrichment_metrics(df)
        last_row = df.iloc[-1]

        package = {
            "ticker": ticker,
            "nome": nome,
            "Acao_Preco_Diaria": {
                "Open": float(last_row.get("open", 0.0)),
                "High": float(last_row.get("high", 0.0)),
                "Low": float(last_row.get("low", 0.0)),
                "Close": float(last_row.get("close", 0.0)),
                "Volume": float(last_row.get("volume", 0.0))
            },
            "Regime_Matematico": {
                "Variacao_5D_Pct": round(metrics["variacao_5d"] * 100, 2),
                "SMA20": round(metrics["sma20"], 4),
                "SMA50": round(metrics["sma50"], 4),
                "Distancia_SMA20_Pct": round(metrics["distancia_sma20"] * 100, 2),
                "Distancia_SMA50_Pct": round(metrics["distancia_sma50"] * 100, 2),
                "Z_Score_20D": round(metrics["z_score_20d"], 2),
                "ATR_14D": round(metrics["atr_14d"], 4),
                "Amplitude_vs_ATR": metrics["amplitude_vs_atr"]
            },
            "Stress_Macro": {
                "VIX_Close": vix_close,
                "VIX_Variacao_5D_Pct": round(metrics_vix.get("variacao_5d", 0.0) * 100, 2),
                "TNX_Close": tnx_close,
                "DGS2_Close": dgs2_close,
                "Spread_10Y_2Y": round(spread_10y_2y, 3)
            },
            "Calendario_Choque": {
                "Radar_Eventos_48h": radar_48h
            }
        }
        operavel_packages.append(package)

    logging.info(f"✅ [FASE 3] {len(operavel_packages)} pacotes quantitativos enriquecidos com Pandas construídos.")
    return {"packages": operavel_packages, "radar_48h": radar_48h}

def map_pkg_to_notion_props(pkg: Dict[str, Any], radar_48h: str, db_schema: Dict[str, str], title_col: str, today_date: str, status_str: str = "[Em processamento]") -> Dict[str, Any]:
    """Mapeia dinamicamente o pacote quantitativo para o dicionário de propriedades do Notion"""
    props = {
        title_col: {"title": [{"text": {"content": pkg["ticker"]}}]}
    }

    # Data da Sessão (date)
    for date_key in ["Data da Sessão", "Data", "Date"]:
        if date_key in db_schema:
            props[date_key] = {"date": {"start": today_date}}
            break

    # Nome (rich_text ou select)
    for name_key in ["Nome", "Name"]:
        if name_key in db_schema:
            p_type = db_schema[name_key]
            if p_type == "select":
                props[name_key] = {"select": {"name": pkg["nome"]}}
            else:
                props[name_key] = {"rich_text": [{"text": {"content": pkg["nome"]}}]}
            break

    # Abertura / High / Low / Close
    for open_key in ["Abertura", "Open"]:
        if open_key in db_schema:
            props[open_key] = {"number": round(pkg["Acao_Preco_Diaria"]["Open"], 4)}
            break
    for high_key in ["Máximo", "High"]:
        if high_key in db_schema:
            props[high_key] = {"number": round(pkg["Acao_Preco_Diaria"]["High"], 4)}
            break
    for low_key in ["Mínimo", "Low"]:
        if low_key in db_schema:
            props[low_key] = {"number": round(pkg["Acao_Preco_Diaria"]["Low"], 4)}
            break
    for close_key in ["Fecho", "Nível de Preço", "Preço Fecho", "Close", "Preço"]:
        if close_key in db_schema:
            props[close_key] = {"number": round(pkg["Acao_Preco_Diaria"]["Close"], 4)}
            break

    # Eventos 48h
    for ev_key in ["Eventos 48h", "Radar Eventos", "Eventos"]:
        if ev_key in db_schema:
            props[ev_key] = {"rich_text": [{"text": {"content": radar_48h}}]}
            break

    # US02Y
    for u2_key in ["US02Y", "US 2Y", "DGS2"]:
        if u2_key in db_schema:
            props[u2_key] = {"number": round(pkg["Stress_Macro"].get("DGS2_Close", 4.31), 4)}
            break

    # US10Y
    for u10_key in ["US10Y", "US 10Y", "TNX"]:
        if u10_key in db_schema:
            props[u10_key] = {"number": round(pkg["Stress_Macro"].get("TNX_Close", 4.68), 4)}
            break

    # VIX
    for vix_key in ["VIX", "VIX Fecho"]:
        if vix_key in db_schema:
            props[vix_key] = {"number": round(pkg["Stress_Macro"]["VIX_Close"], 4)}
            break

    # Spread 10Y-2Y
    for sp_key in ["Spread 10Y-2Y", "Spread 10Y-2Y Yield"]:
        if sp_key in db_schema:
            props[sp_key] = {"number": round(pkg["Stress_Macro"]["Spread_10Y_2Y"], 4)}
            break

    # Z-Score 20D
    for z_key in ["Z-Score 20D", "Z-Score"]:
        if z_key in db_schema:
            p_type = db_schema[z_key]
            z_val = pkg["Regime_Matematico"]["Z_Score_20D"]
            if p_type == "number":
                props[z_key] = {"number": round(z_val, 2)}
            else:
                props[z_key] = {"rich_text": [{"text": {"content": str(z_val)}}]}
            break

    # Multiplicador ATR
    for atr_key in ["Multiplicador ATR", "ATR Multiplicador"]:
        if atr_key in db_schema:
            p_type = db_schema[atr_key]
            mult_val = pkg["Regime_Matematico"]["Amplitude_vs_ATR"]
            if p_type == "rich_text":
                props[atr_key] = {"rich_text": [{"text": {"content": mult_val}}]}
            elif p_type == "number":
                try:
                    num_mult = float(mult_val.replace("x", ""))
                    props[atr_key] = {"number": round(num_mult, 2)}
                except:
                    pass
            break

    # Status (select)
    if "Status" in db_schema:
        props["Status"] = {"select": {"name": status_str}}

    # Veredito Tático (rich_text) - Inicialmente [Em processamento]
    for ver_key in ["Veredito Tático", "Veredito"]:
        if ver_key in db_schema and "Status" not in db_schema:
            props[ver_key] = {"rich_text": [{"text": {"content": status_str}}]}
            break

    return props

def phase4_inject_notion_initial(packages: List[Dict[str, Any]], radar_48h: str) -> List[Dict[str, Any]]:
    """
    Fase 4: Escreve/Atualiza as linhas no Notion com todas as colunas quantitativas + estado [Em processamento].
    """
    if not NOTION_TOKEN or not NOTION_GEMINI_DB_ID:
        logging.error("❌ NOTION_GEMINI_DB_ID não configurado.")
        return packages

    db_schema = get_notion_db_schema_properties(NOTION_GEMINI_DB_ID)
    title_col = get_notion_title_col_name(NOTION_GEMINI_DB_ID, "Ativo")
    date_col = "Data da Sessão" if "Data da Sessão" in db_schema else ("Data" if "Data" in db_schema else "Date")
    today_date = datetime.utcnow().strftime("%Y-%m-%d")
    url_query = f"https://api.notion.com/v1/databases/{NOTION_GEMINI_DB_ID}/query"

    updated_packages = []

    for pkg in packages:
        ticker = pkg["ticker"]

        query_payload = {
            "filter": {
                "and": [
                    {"property": title_col, "title": {"contains": ticker}},
                    {"property": date_col, "date": {"equals": today_date}}
                ]
            }
        }

        try:
            res = requests.post(url_query, headers=NOTION_HEADERS, json=query_payload, timeout=10)
            existing = res.json().get("results", []) if res.status_code == 200 else []

            props = map_pkg_to_notion_props(pkg, radar_48h, db_schema, title_col, today_date, "[Em processamento]")

            if existing:
                page_id = existing[0]["id"]
                url_patch = f"https://api.notion.com/v1/pages/{page_id}"
                patch_res = requests.patch(url_patch, headers=NOTION_HEADERS, json={"properties": props}, timeout=10)
                if patch_res.status_code in [200, 201]:
                    pkg["notion_page_id"] = page_id
                    logging.info(f"✅ [FASE 4] [{ticker}] atualizado no Notion com métricas quantitativas ([Em processamento])")
                else:
                    logging.error(f"❌ [FASE 4] PATCH falhou para [{ticker}] ({patch_res.status_code}): {patch_res.text}")
            else:
                post_payload = {
                    "parent": {"database_id": NOTION_GEMINI_DB_ID},
                    "properties": props
                }
                url_post = "https://api.notion.com/v1/pages"
                post_res = requests.post(url_post, headers=NOTION_HEADERS, json=post_payload, timeout=10)
                if post_res.status_code in [200, 201]:
                    page_id = post_res.json().get("id")
                    pkg["notion_page_id"] = page_id
                    logging.info(f"✅ [FASE 4] Linha criada para [{ticker}] no Notion com métricas quantitativas ([Em processamento])")
                else:
                    logging.error(f"❌ [FASE 4] POST falhou para [{ticker}] ({post_res.status_code}): {post_res.text}")

            updated_packages.append(pkg)
        except Exception as e:
            logging.error(f"❌ Erro na Fase 4 Notion para [{ticker}]: {e}")
            updated_packages.append(pkg)

    return updated_packages

def _parse_retry_delay(error_body: dict) -> float:
    """Extrai o retryDelay da resposta 429 da API do Google e converte para segundos."""
    try:
        details = error_body.get("error", {}).get("details", [])
        for d in details:
            if d.get("@type") == "type.googleapis.com/google.rpc.RetryInfo":
                delay_str = d.get("retryDelay", "60s")
                # Suporta '45s', '45.5s', '0s', '2.763307ms'
                if delay_str.endswith("ms"):
                    return max(1.0, float(delay_str[:-2]) / 1000.0)
                elif delay_str.endswith("s"):
                    return max(5.0, float(delay_str[:-1]))
    except Exception:
        pass
    return 60.0  # default seguro

def call_gemini_api(prompt: str, api_key: str) -> str:
    """
    Chama a API do Google Gemini com retry inteligente baseado no retryDelay da resposta 429.
    Sequência: SDK google.genai (v2) → REST HTTP nativa.
    Apenas tenta modelos disponíveis na chave (gemini-2.0-flash).
    """
    import time
    AVAILABLE_MODELS = ["gemini-2.0-flash"]  # Modelos confirmados disponíveis no free tier

    # ─── ABORDAGEM 1: Novo SDK google.genai (recomendado pela Google) ───────────
    try:
        from google import genai
        client = genai.Client(api_key=api_key)
        for model_name in AVAILABLE_MODELS:
            try:
                logging.info(f"🤖 [GEMINI SDK v2] Tentando [{model_name}]...")
                response = client.models.generate_content(model=model_name, contents=prompt)
                if response and response.text:
                    text = response.text.strip()
                    if len(text) > 50:
                        logging.info(f"✨ [GEMINI SDK v2] Veredito via [{model_name}]! ({len(text)} chars)")
                        return text
            except Exception as sdk_err:
                err_str = str(sdk_err)
                if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                    # Extrair retryDelay do erro para esperar o tempo certo
                    try:
                        import json, re
                        json_match = re.search(r'\{.*\}', err_str, re.DOTALL)
                        if json_match:
                            err_body = json.loads(json_match.group())
                            wait_s = _parse_retry_delay(err_body)
                        else:
                            wait_s = 65.0
                    except Exception:
                        wait_s = 65.0
                    logging.warning(f"⚠️ [GEMINI SDK v2] Quota 429 em [{model_name}]. Aguardando {wait_s:.0f}s (retryDelay da API)...")
                    time.sleep(wait_s)
                    # Tentar de novo após esperar o tempo indicado pela API
                    try:
                        response = client.models.generate_content(model=model_name, contents=prompt)
                        if response and response.text and len(response.text.strip()) > 50:
                            logging.info(f"✨ [GEMINI SDK v2] Veredito (retry) via [{model_name}]! ({len(response.text.strip())} chars)")
                            return response.text.strip()
                    except Exception as retry_err:
                        logging.warning(f"⚠️ [GEMINI SDK v2] Retry também falhou para [{model_name}]: {retry_err}")
                else:
                    logging.warning(f"⚠️ [GEMINI SDK v2] [{model_name}] falhou: {err_str[:150]}")
                continue
    except ImportError:
        logging.warning("⚠️ [GEMINI SDK v2] google-genai não instalado. Tentando REST...")
    except Exception as e:
        logging.warning(f"⚠️ [GEMINI SDK v2] Falha geral: {e}")

    # ─── ABORDAGEM 2: REST HTTP nativa ─────────────────────────────────────────
    for model_name in AVAILABLE_MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
        try:
            logging.info(f"🤖 [GEMINI REST] Tentando [{model_name}]...")
            res = requests.post(url, headers={"Content-Type": "application/json"},
                                json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=20)
            if res.status_code == 200:
                candidates = res.json().get("candidates", [])
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    if parts:
                        text = parts[0].get("text", "").strip()
                        if text and len(text) > 50:
                            logging.info(f"✨ [GEMINI REST] Veredito via [{model_name}]! ({len(text)} chars)")
                            return text
            elif res.status_code == 429:
                wait_s = _parse_retry_delay(res.json())
                logging.warning(f"⚠️ [GEMINI REST] 429 em [{model_name}]. Aguardando {wait_s:.0f}s (retryDelay da API)...")
                time.sleep(wait_s)
                # Retry único após espera
                try:
                    res2 = requests.post(url, headers={"Content-Type": "application/json"},
                                         json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=20)
                    if res2.status_code == 200:
                        candidates2 = res2.json().get("candidates", [])
                        if candidates2:
                            parts2 = candidates2[0].get("content", {}).get("parts", [])
                            if parts2:
                                text2 = parts2[0].get("text", "").strip()
                                if text2 and len(text2) > 50:
                                    logging.info(f"✨ [GEMINI REST] Veredito (retry) via [{model_name}]! ({len(text2)} chars)")
                                    return text2
                except Exception:
                    pass
            else:
                logging.warning(f"⚠️ [GEMINI REST] [{model_name}] HTTP {res.status_code}")
        except Exception as e:
            logging.warning(f"⚠️ [GEMINI REST] Erro em [{model_name}]: {e}")

    logging.error("❌ Todas as abordagens Gemini falharam (quota esgotada ou modelos indisponíveis).")
    return ""

def phase5_invoke_gemini_and_update(packages: List[Dict[str, Any]]):
    """
    Fase 5: Invoca o modelo Google Gemini com o payload quantitativo estruturado.
    Substitui '[Em processamento]' pelo Veredito Tático real, Viés e Status [Concluído].
    """
    if not GEMINI_API_KEY:
        logging.warning("⚠️ GEMINI_API_KEY não encontrada. Invocação ao modelo ignorada.")
        return

    db_schema = get_notion_db_schema_properties(NOTION_GEMINI_DB_ID)

    for pkg in packages:
        page_id = pkg.get("notion_page_id")
        if not page_id:
            logging.warning(f"⚠️ [{pkg['ticker']}] sem notion_page_id. Invocação do Gemini ignorada para este item.")
            continue

        logging.info(f"🤖 [FASE 5 AUDIT] A iniciar chamada à API do Gemini para o ativo [{pkg['ticker']}] ({pkg['nome']})...")

        # Prompt comprimido (~90% menos tokens que enviar pkg completo)
        rm = pkg.get("Regime_Matematico", {})
        sm = pkg.get("Stress_Macro", {})
        ap = pkg.get("Acao_Preco_Diaria", {})
        ev = pkg.get("Calendario_Choque", {}).get("Radar_Eventos_48h", "N/A")
        prompt = (
            f"Analista Quant Sénior. Ativo: {pkg['nome']} ({pkg['ticker']}).\n"
            f"Sessão: Close={ap.get('Close',0):.4f} Open={ap.get('Open',0):.4f} H={ap.get('High',0):.4f} L={ap.get('Low',0):.4f}\n"
            f"Z-Score20D={rm.get('Z_Score_20D',0):.2f} | Var5D={rm.get('Variacao_5D_Pct',0):.2f}% | "
            f"Dist_SMA20={rm.get('Distancia_SMA20_Pct',0):.2f}% | ATR_mult={rm.get('Amplitude_vs_ATR','N/A')}\n"
            f"Macro: VIX={sm.get('VIX_Close',0):.2f} | US10Y={sm.get('TNX_Close',0):.3f} | "
            f"US2Y={sm.get('DGS2_Close',0):.3f} | Spread10Y2Y={sm.get('Spread_10Y_2Y',0):.3f}\n"
            f"Eventos48h: {str(ev)[:200]}\n\n"
            f"Responde apenas com:\n"
            f"1. Veredito (2-3 frases sobre momentum, Z-score e catalisadores)\n"
            f"2. Viés: Bullish | Bearish | Neutro"
        )

        try:
            verdict_text = call_gemini_api(prompt, GEMINI_API_KEY)
            if not verdict_text:
                logging.warning(f"⚠️ [{pkg['ticker']}] Gemini não retornou veredito. Linha ficará como '[Em processamento]'.")
                continue

            logging.info(f"✨ [FASE 5 AUDIT] Veredito do Gemini recebido para [{pkg['ticker']}] ({len(verdict_text)} caracteres).")

            vies = "Neutro"
            if "bullish" in verdict_text.lower():
                vies = "Bullish"
            elif "bearish" in verdict_text.lower():
                vies = "Bearish"

            props = {}

            if "Status" in db_schema:
                props["Status"] = {"select": {"name": "Concluído"}}

            for ver_key in ["Veredito Tático", "Veredito"]:
                if ver_key in db_schema:
                    props[ver_key] = {"rich_text": [{"text": {"content": verdict_text[:2000]}}]}
                    break

            for vies_key in ["Viés", "Bias"]:
                if vies_key in db_schema:
                    props[vies_key] = {"select": {"name": vies}}
                    break

            url_patch = f"https://api.notion.com/v1/pages/{page_id}"
            logging.info(f"📤 [FASE 5 AUDIT] Enviando PATCH ao Notion para substituir '[Em processamento]' na página ID [{page_id}]...")
            
            patch_res = requests.patch(url_patch, headers=NOTION_HEADERS, json={"properties": props}, timeout=10)
            if patch_res.status_code in [200, 201]:
                logging.info(f"🎉 [FASE 5 AUDIT] Notion PATCH bem-sucedido (HTTP {patch_res.status_code}) para [{pkg['ticker']}]. '[Em processamento]' substituído com sucesso!")
            else:
                logging.error(f"❌ [FASE 5 AUDIT] Erro ao gravar veredito Gemini no Notion para [{pkg['ticker']}] (HTTP {patch_res.status_code}): {patch_res.text}")

        except Exception as ex:
            logging.error(f"❌ [FASE 5 AUDIT] Falha de execução/API Gemini para [{pkg['ticker']}]: {ex}")
        finally:
            import time
            time.sleep(4)  # Anti-rate-limit: 4s entre chamadas (~15 pedidos/min = dentro do free tier)

def run_gemini_trader_pipeline(enable_gemini_api: bool = False):
    """Pipeline Principal do Gemini Trader (Fases 1 a 5).
    
    Args:
        enable_gemini_api: Se True, executa a Fase 5 (chamada à API do Gemini para veredito).
                           Se False (default), apenas faz ETL + injecta dados quantitativos no Notion.
    """
    logging.info("🚀 Iniciando Pipeline ETL & Gemini Trader (Adenda Enriquecimento Analítico Pandas)...")
    if enable_gemini_api:
        logging.info("🤖 [FASE 5 ATIVA] Modo Briefing Noturno: API Gemini será invocada.")
    else:
        logging.info("📊 [FASE 5 DESATIVADA] Modo ETL Diurno: apenas dados quantitativos. Gemini não será invocado.")
    
    # Fase 1 & 2
    vigilance_items = phase1_extract_vigilance_config()
    if not vigilance_items:
        logging.warning("⚠️ Nenhum ativo configurado para o Gemini na Configuração de Vigilância.")
        return

    macro_list, operavel_list = phase2_split_lists(vigilance_items)

    # Fase 3 (Pandas 50-sessions analytics)
    market_data = phase3_query_market_data(macro_list, operavel_list)
    packages = market_data["packages"]
    radar_48h = market_data["radar_48h"]

    if not packages:
        logging.warning("⚠️ Nenhum pacote operável construído na Fase 3.")
        return

    # Fase 4 (Notion Initial Status & Quantitative Data Injection)
    updated_packages = phase4_inject_notion_initial(packages, radar_48h)

    # Fase 5 (Gemini AI Inference & Verdict Update) — só corre no Briefing Noturno das 22h00
    if enable_gemini_api:
        phase5_invoke_gemini_and_update(updated_packages)
        logging.info("🎉 Pipeline completo (ETL + Briefing Gemini) concluído com sucesso!")
    else:
        logging.info("✅ Pipeline ETL concluído com sucesso! (Fase 5 reservada para corrida das 22h00)")

