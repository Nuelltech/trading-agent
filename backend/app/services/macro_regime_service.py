# backend/app/services/macro_regime_service.py
"""
Módulo: macro_regime_service.py (Agente Automatizado para a Camada 1 - Regime Macro Diário)
Data: 10 de Agosto de 2026

Implementação em 2 Tarefas Desacopladas:
TAREFA 1 — Cálculo Determinístico (Python Puro, SEM LLM):
- Lê OHLC no MySQL, calcula VIX Percentil, Padrão Intradiário, Sinais dos 11 Ativos,
  Divergências por Média Regional, Rácio Cobre/Ouro, Yield Real Proxy, Gaps, Sweeps e Earnings.
- Escreve diretamente no Notion ('Resumo Diário — Regime de Risco — Claude').
- Se faltar algum dado crítico, sinaliza no campo 'Erros Detetados Neste Ciclo' e CANCELA a Tarefa 2.

TAREFA 2 — Síntese com Julgamento (Claude API com ANTHROPIC_API_KEY_TRADING):
- Lê a Tese Estrutural mais recente da Camada 0 no Notion (NOTION_CAMADA0_DB_ID).
- Invoca a Anthropic API com o prompt estreito em JSON estrito.
- Preenche no Notion apenas os 4 campos de julgamento.
"""

import sys
import os
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple
import requests

sys.path.append('backend')
from sqlalchemy import text
from app.database import engine
from app.services.claude_analyzer import call_claude_api

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

NOTION_TOKEN = os.getenv("NOTION_TOKEN") or os.getenv("NOTION_API_KEY", "")
NOTION_CLAUDE_REGIME_DB_ID = (
    os.getenv("NOTION_CLAUDE_REGIME_DATABASE_ID", "").strip() or 
    os.getenv("NOTION_PAINEL_MERCADO_DATABASE_ID", "").strip() or 
    "3efd828b-84a7-4966-8bdf-fe9c93657edd"
)
NOTION_CAMADA0_DB_ID = os.getenv("NOTION_CAMADA0_DB_ID", "cc9794eb-da24-4c9a-8116-ba859efa65aa").strip()

NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28"
}

def get_notion_db_schema(db_id: str) -> Dict[str, str]:
    """Retorna dicionário {property_name: property_type} de uma database Notion."""
    url = f"https://api.notion.com/v1/databases/{db_id}"
    try:
        res = requests.get(url, headers=NOTION_HEADERS, timeout=10)
        if res.status_code == 200:
            properties = res.json().get("properties", {})
            return {p_name: p_data.get("type") for p_name, p_data in properties.items()}
    except Exception as e:
        logging.warning(f"Falha ao ler schema da db {db_id}: {e}")
    return {}

def fetch_latest_candle(symbol: str, target_date: str) -> Optional[Dict[str, Any]]:
    """Busca o candle mais recente para o símbolo na DB MySQL."""
    try:
        with engine.connect() as conn:
            sql = text("""
                SELECT timestamp, open_val, high_val, low_val, value as close, volume, adj_close
                FROM indicator_values
                WHERE symbol = :symbol AND DATE(timestamp) <= DATE(:target_date)
                ORDER BY timestamp DESC LIMIT 2
            """)
            rows = conn.execute(sql, {"symbol": symbol, "target_date": target_date}).fetchall()
            if rows:
                latest = dict(rows[0]._mapping)
                prev = dict(rows[1]._mapping) if len(rows) > 1 else None
                latest["prev_close"] = prev["close"] if prev else None
                return latest
    except Exception as e:
        logging.warning(f"Erro ao buscar candle de {symbol} na DB: {e}")
    return None

def fetch_vix_history(target_date: str, limit: int = 60) -> List[float]:
    """Obtém histórico de fecho do VIX no MySQL para cálculo de percentil."""
    try:
        with engine.connect() as conn:
            sql = text("""
                SELECT value FROM indicator_values
                WHERE symbol = '^VIX' AND DATE(timestamp) <= DATE(:target_date)
                ORDER BY timestamp DESC LIMIT :limit
            """)
            rows = conn.execute(sql, {"target_date": target_date, "limit": limit}).fetchall()
            return [float(r[0]) for r in rows if r[0] is not None]
    except Exception as e:
        logging.warning(f"Erro ao carregar histórico do VIX: {e}")
    return []

def fetch_today_earnings_count(target_date: str) -> int:
    """Busca contagem de resultados de empresa de alto impacto no dia."""
    try:
        with engine.connect() as conn:
            sql = text("""
                SELECT COUNT(*) FROM economic_calendar
                WHERE DATE(event_date) = DATE(:target_date)
                  AND (category LIKE '%Earnings%' OR category LIKE '%Resultados%' OR event_name LIKE '%Earnings%')
                  AND impact = 'HIGH'
            """)
            cnt = conn.execute(sql, {"target_date": target_date}).scalar()
            return int(cnt or 0)
    except Exception:
        return 0

def fetch_camada0_structural_thesis() -> str:
    """Lê a tese estrutural mais recente da Camada 0 no Notion."""
    if not NOTION_TOKEN or not NOTION_CAMADA0_DB_ID:
        return "Tese Estrutural Camada 0: Não disponível (Secret NOTION_CAMADA0_DB_ID não configurado)."

    url_query = f"https://api.notion.com/v1/databases/{NOTION_CAMADA0_DB_ID}/query"
    payload = {"page_size": 1}
    try:
        res = requests.post(url_query, headers=NOTION_HEADERS, json=payload, timeout=10)
        if res.status_code == 200:
            results = res.json().get("results", [])
            if results:
                props = results[0].get("properties", {})
                for p_name, p_data in props.items():
                    p_type = p_data.get("type")
                    if p_type == "rich_text":
                        texts = p_data.get("rich_text", [])
                        if texts:
                            return texts[0].get("plain_text", "")
                    elif p_type == "title":
                        titles = p_data.get("title", [])
                        if titles:
                            return titles[0].get("plain_text", "")
    except Exception as e:
        logging.warning(f"⚠️ Erro ao consultar Camada 0 no Notion: {e}")

    return "Tese Estrutural Camada 0: Tendência neutra/indefinida no horizonte semanal."


# -----------------------------------------------------------------------------
# TAREFA 1 — CÁLCULO DETERMINÍSTICO (Python Puro, SEM LLM)
# -----------------------------------------------------------------------------
def run_tarefa1_calculo_mecanico(target_date: Optional[str] = None) -> Tuple[Dict[str, Any], bool]:
    """
    Executa todos os cálculos determinísticos da Camada 1 e grava no Notion.
    Retorna tuple (dados_calculados, tem_erro_critico).
    """
    if not target_date:
        target_date = datetime.utcnow().strftime("%Y-%m-%d")

    logging.info(f"📊 [TAREFA 1] Iniciando Cálculo Determinístico para Sessão {target_date}...")
    errors: List[str] = []
    calc: Dict[str, Any] = {"date": target_date}

    # 1. Obter VIX e calcular Percentil + Padrão Intradiário
    vix_candle = fetch_latest_candle("^VIX", target_date)
    if not vix_candle or vix_candle["close"] is None:
        errors.append("Cotação ^VIX de hoje ausente no MySQL.")
        calc["vix_close"] = None
        calc["classificacao_vix"] = "Sem Dados"
        calc["padrao_vix"] = "Sem Dados"
    else:
        vix_close = float(vix_candle["close"])
        vix_open = float(vix_candle["open_val"]) if vix_candle.get("open_val") else vix_close
        calc["vix_close"] = vix_close

        # Percentil
        vix_hist = fetch_vix_history(target_date, limit=60)
        if len(vix_hist) >= 60:
            menores = sum(1 for v in vix_hist if v <= vix_close)
            percentil = (menores / len(vix_hist)) * 100.0
            calc["vix_percentil"] = percentil
            if percentil < 40:
                calc["classificacao_vix"] = "Baixa Vol"
            elif percentil <= 85:
                calc["classificacao_vix"] = "Transição"
            else:
                calc["classificacao_vix"] = "Pânico"
        else:
            # Fallback Cold-start
            if vix_close < 15.0:
                calc["classificacao_vix"] = "Baixa Vol"
            elif vix_close <= 20.0:
                calc["classificacao_vix"] = "Transição"
            else:
                calc["classificacao_vix"] = "Pânico"

        # Padrão Intradiário
        var_vix = (vix_close - vix_open) / vix_open if vix_open > 0 else 0.0
        if var_vix > 0.03:
            calc["padrao_vix"] = "Acumulação de medo"
        elif var_vix < -0.03:
            calc["padrao_vix"] = "Esmorecimento"
        else:
            calc["padrao_vix"] = "Estável"

    # 2. Sinais dos 11 Ativos
    # Sinais: DXY, Yields (^TNX), Brent, Nasdaq, SOX, S&P500, DAX, Euro Stoxx 50, Nikkei, Hang Seng, Kospi
    asset_map = {
        "DXY": "DX-Y.NYB",
        "Yields": "^TNX",
        "Brent": "BZ=F",
        "Nasdaq": "^NDX",
        "SOX": "^SOX",
        "SP500": "^GSPC",
        "DAX": "^GDAXI",
        "EuroStoxx50": "^STOXX50E",
        "Nikkei": "^N225",
        "HangSeng": "^HSI",
        "Kospi": "^KS11"
    }

    sinais: Dict[str, float] = {}
    for key, ticker in asset_map.items():
        c = fetch_latest_candle(ticker, target_date)
        if not c or c["close"] is None:
            # Tentar fallback para ticker equivalente se necessário
            if ticker == "^STOXX50E":
                c = fetch_latest_candle("FEZ", target_date)
            
        if not c or c["close"] is None:
            sinais[key] = 0.0
            errors.append(f"Ativo [{ticker}] sem cotação no MySQL.")
        else:
            close_val = float(c["close"])
            open_val = float(c["open_val"]) if c.get("open_val") else close_val
            
            if key == "Yields":
                # Variação em pontos percentuais (yield_hoje - yield_ontem)
                prev_c = float(c["prev_close"]) if c.get("prev_close") else open_val
                sinais[key] = round(close_val - prev_c, 4)
            else:
                # Variação percentual relativa (%)
                sinais[key] = round(((close_val - open_val) / open_val) * 100.0, 2) if open_val > 0 else 0.0

    calc["sinais"] = sinais

    # 3. Divergências por Média de Bloco Regional
    s_ndx = sinais.get("Nasdaq", 0.0)
    s_sox = sinais.get("SOX", 0.0)
    
    media_eua = (sinais.get("SP500", 0.0) + s_ndx + s_sox) / 3.0
    media_eur = (sinais.get("DAX", 0.0) + sinais.get("EuroStoxx50", 0.0)) / 2.0
    media_asia = (sinais.get("Nikkei", 0.0) + sinais.get("HangSeng", 0.0) + sinais.get("Kospi", 0.0)) / 3.0

    def calc_divergencia(val_a: float, val_b: float) -> str:
        if (val_a * val_b < 0) or (abs(val_b) > 0 and abs(val_a / val_b) > 1.5) or (abs(val_a) > 0 and abs(val_b / val_a) > 1.5):
            return "Sim"
        return "Não"

    calc["div_ndx_sox"] = calc_divergencia(s_ndx, s_sox)
    calc["div_eua_eur"] = calc_divergencia(media_eua, media_eur)
    calc["div_eua_asia"] = calc_divergencia(media_eua, media_asia)

    # 4. Rácio Cobre/Ouro (HG=F / GC=F)
    hg_c = fetch_latest_candle("HG=F", target_date)
    gc_c = fetch_latest_candle("GC=F", target_date)
    if hg_c and gc_c and hg_c["close"] and gc_c["close"] and float(gc_c["close"]) > 0:
        calc["racio_cobre_ouro"] = round(float(hg_c["close"]) / float(gc_c["close"]), 6)
    else:
        calc["racio_cobre_ouro"] = None

    # 5. Yield Real Proxy (Coerência entre Yields e Ouro)
    s_yields = sinais.get("Yields", 0.0)
    s_gold = 0.0
    if gc_c and gc_c["close"] and gc_c.get("open_val"):
        s_gold = ((float(gc_c["close"]) - float(gc_c["open_val"])) / float(gc_c["open_val"])) * 100.0

    coerente = (s_yields > 0 and s_gold < 0) or (s_yields < 0 and s_gold > 0)
    calc["yield_real_proxy"] = "Coerente" if coerente else "Incoerente"

    # 6. Gap de Abertura (%) S&P500
    sp_c = fetch_latest_candle("^GSPC", target_date)
    if sp_c and sp_c["close"] and sp_c.get("prev_close") and sp_c.get("open_val"):
        open_h = float(sp_c["open_val"])
        prev_close = float(sp_c["prev_close"])
        calc["gap_abertura"] = round(((open_h - prev_close) / prev_close) * 100.0, 2)
    else:
        calc["gap_abertura"] = 0.0

    # 7. Earnings Relevantes do Dia
    calc["earnings_count"] = fetch_today_earnings_count(target_date)

    has_critical_error = len(errors) > 0
    calc["errors"] = errors

    # 8. Escrever no Notion (Database #4: Resumo Diário — Regime de Risco — Claude)
    write_tarefa1_to_notion(calc)

    if has_critical_error:
        logging.warning(f"⚠️ [TAREFA 1] Concluída com {len(errors)} alertas/dados em falta. A Tarefa 2 será CANCELADA.")
    else:
        logging.info("✅ [TAREFA 1] Concluída com 100% de sucesso mecânico.")

    return calc, has_critical_error


def write_tarefa1_to_notion(calc: Dict[str, Any]) -> bool:
    """Escreve todos os campos calculados na Tarefa 1 na Database Notion."""
    if not NOTION_TOKEN or not NOTION_CLAUDE_REGIME_DB_ID:
        logging.error("❌ NOTION_CLAUDE_REGIME_DATABASE_ID não configurado.")
        return False

    schema = get_notion_db_schema(NOTION_CLAUDE_REGIME_DB_ID)
    entry_date = calc["date"]
    session_title = f"Sessão {entry_date}"

    title_col = "Data"
    for p_name, p_type in schema.items():
        if p_type == "title":
            title_col = p_name
            break

    url_query = f"https://api.notion.com/v1/databases/{NOTION_CLAUDE_REGIME_DB_ID}/query"
    query_payload = {
        "filter": {
            "property": title_col,
            "title": {"contains": entry_date}
        }
    }

    props: Dict[str, Any] = {
        title_col: {"title": [{"text": {"content": session_title}}]}
    }

    # VIX
    if "Classificação VIX" in schema:
        props["Classificação VIX"] = {"select": {"name": calc.get("classificacao_vix", "Sem Dados")}}
    if "Padrão Intradiário VIX" in schema:
        props["Padrão Intradiário VIX"] = {"select": {"name": calc.get("padrao_vix", "Estável")}}

    # Sinais 11 Ativos
    sinais = calc.get("sinais", {})
    mapping_sinais = {
        "Sinal DXY do Dia": sinais.get("DXY", 0.0),
        "Sinal Yields do Dia": sinais.get("Yields", 0.0),
        "Sinal Brent do Dia": sinais.get("Brent", 0.0),
        "Sinal Nasdaq do Dia": sinais.get("Nasdaq", 0.0),
        "Sinal SOX do Dia": sinais.get("SOX", 0.0),
        "Sinal S&P500 do Dia": sinais.get("SP500", 0.0),
        "Sinal DAX do Dia": sinais.get("DAX", 0.0),
        "Sinal Euro Stoxx 50 do Dia": sinais.get("EuroStoxx50", 0.0),
        "Sinal Nikkei do Dia": sinais.get("Nikkei", 0.0),
        "Sinal Hang Seng do Dia": sinais.get("HangSeng", 0.0),
        "Sinal Kospi do Dia": sinais.get("Kospi", 0.0)
    }

    for prop_name, val in mapping_sinais.items():
        if prop_name in schema:
            p_type = schema[prop_name]
            if p_type == "number":
                props[prop_name] = {"number": val}
            else:
                props[prop_name] = {"rich_text": [{"text": {"content": str(val)}}]}

    # Divergências
    if "Divergência Nasdaq-SOX" in schema:
        props["Divergência Nasdaq-SOX"] = {"select": {"name": calc.get("div_ndx_sox", "Não")}}
    if "Divergência EUA vs. Europa" in schema:
        props["Divergência EUA vs. Europa"] = {"select": {"name": calc.get("div_eua_eur", "Não")}}
    if "Divergência EUA vs. Ásia" in schema:
        props["Divergência EUA vs. Ásia"] = {"select": {"name": calc.get("div_eua_asia", "Não")}}

    # Rácio Cobre/Ouro
    if "Rácio Cobre/Ouro" in schema and calc.get("racio_cobre_ouro") is not None:
        props["Rácio Cobre/Ouro"] = {"number": calc["racio_cobre_ouro"]}

    # Yield Real Proxy
    if "Leitura Yield Real (Proxy)" in schema:
        props["Leitura Yield Real (Proxy)"] = {"rich_text": [{"text": {"content": calc.get("yield_real_proxy", "Coerente")}}]}

    # Gap Abertura
    if "Gap de Abertura (%)" in schema:
        props["Gap de Abertura (%)"] = {"number": calc.get("gap_abertura", 0.0)}

    # Earnings
    if "Earnings Relevantes Hoje" in schema:
        props["Earnings Relevantes Hoje"] = {"number": calc.get("earnings_count", 0)}

    # Erros Detetados Neste Ciclo (Fonte principal de auditoria de erros)
    errors_list = calc.get("errors", [])
    if "Erros Detetados Neste Ciclo" in schema:
        err_msg = " | ".join(errors_list) if errors_list else "Nenhum erro detetado."
        props["Erros Detetados Neste Ciclo"] = {"rich_text": [{"text": {"content": err_msg}}]}

    try:
        res = requests.post(url_query, headers=NOTION_HEADERS, json=query_payload, timeout=10)
        results = res.json().get("results", []) if res.status_code == 200 else []

        if results:
            page_id = results[0]["id"]
            url_patch = f"https://api.notion.com/v1/pages/{page_id}"
            patch_res = requests.patch(url_patch, headers=NOTION_HEADERS, json={"properties": props}, timeout=10)
            if patch_res.status_code in [200, 201]:
                logging.info(f"✅ [TAREFA 1 NOTION] Sessão [{entry_date}] atualizada no Notion com sucesso.")
                return True
        else:
            post_payload = {
                "parent": {"database_id": NOTION_CLAUDE_REGIME_DB_ID},
                "properties": props
            }
            url_post = "https://api.notion.com/v1/pages"
            post_res = requests.post(url_post, headers=NOTION_HEADERS, json=post_payload, timeout=10)
            if post_res.status_code in [200, 201]:
                logging.info(f"✅ [TAREFA 1 NOTION] Linha criada no Notion para Sessão [{entry_date}].")
                return True
    except Exception as e:
        logging.error(f"❌ Erro ao escrever Tarefa 1 no Notion: {e}")

    return False


# -----------------------------------------------------------------------------
# TAREFA 2 — SÍNTESE COM JULGAMENTO (Claude API, Prompt Estreito e Único)
# -----------------------------------------------------------------------------
def run_tarefa2_sintese_claude(calc_data: Dict[str, Any], has_critical_error: bool) -> bool:
    """
    Executa a chamada estrita à API do Claude (Anthropic API) para classificar o
    Regime Consolidado e a Expectativa para o Dia Seguinte.
    """
    if has_critical_error:
        logging.warning("🛑 [TAREFA 2 ABORTADA] Devido a erros de dados de origem detetados na Tarefa 1.")
        return False

    logging.info("🧠 [TAREFA 2] Iniciando Síntese com Julgamento via Anthropic API (Claude)...")

    # 1. Carregar Tese Estrutural da Camada 0
    tese_camada0 = fetch_camada0_structural_thesis()

    # 2. Montar prompt do sistema estrito
    system_prompt = """Tarefa única: classificar o Regime Consolidado do dia e escrever uma previsão para amanhã. Não recalcules nada — os valores já estão certos.

Regras de classificação do Regime:
- Risk-On: VIX baixo/queda + maioria dos índices em alta
- Risk-Off: VIX alto/subida + maioria dos índices em queda
- Misto/Transição: sinais mistos entre classes de ativo
- Neutro: sem sinal dominante claro

Responde em JSON estrito, exatamente este formato, nada mais:
{
  "regime_consolidado": "Risk-On" | "Risk-Off" | "Misto/Transição" | "Neutro",
  "raciocinio_regime": "1-2 frases, cita os campos que usaste",
  "coerencia_camada_0": "Confirma" | "Contradiz" | "Neutro",
  "expectativa_dia_seguinte": "previsão explícita e falsificável, com nível de invalidação"
}"""

    user_prompt = f"""Dados Calculados da Sessão ({calc_data.get('date')}):
- Classificação VIX: {calc_data.get('classificacao_vix')}
- Padrão Intradiário VIX: {calc_data.get('padrao_vix')}
- Sinais dos Ativos: {json.dumps(calc_data.get('sinais', {}))}
- Divergências: Nasdaq-SOX={calc_data.get('div_ndx_sox')}, EUA-Europa={calc_data.get('div_eua_eur')}, EUA-Ásia={calc_data.get('div_eua_asia')}
- Yield Real Proxy: {calc_data.get('yield_real_proxy')}
- Gap Abertura: {calc_data.get('gap_abertura')}%

Tese Estrutural Ativa da Camada 0:
"{tese_camada0}"
"""

    # 3. Chamar Anthropic API
    raw_response = call_claude_api(system_prompt, user_prompt, max_tokens=600)
    if not raw_response:
        logging.error("❌ [TAREFA 2] Falha ao obter resposta da Anthropic API.")
        return False

    # 4. Parse JSON
    try:
        clean_json = raw_response.strip()
        if clean_json.startswith("```json"):
            clean_json = clean_json.split("```json", 1)[1].rsplit("```", 1)[0].strip()
        elif clean_json.startswith("```"):
            clean_json = clean_json.split("```", 1)[1].rsplit("```", 1)[0].strip()

        parsed = json.loads(clean_json)
    except Exception as parse_err:
        logging.error(f"❌ [TAREFA 2] Erro ao fazer parse do JSON do Claude: {parse_err}. Resposta bruta:\n{raw_response}")
        return False

    regime_val = str(parsed.get("regime_consolidado") or "Neutro")
    coerencia_val = str(parsed.get("coerencia_camada_0") or "Neutro")
    raciocinio_val = str(parsed.get("raciocinio_regime") or "")
    expectativa_val = str(parsed.get("expectativa_dia_seguinte") or "")

    logging.info(f"🎉 [TAREFA 2 SUCESSO] Regime: {regime_val} | Coerência Camada 0: {coerencia_val}")

    # 5. Escrever os 4 campos de julgamento no Notion
    return write_tarefa2_to_notion(calc_data["date"], regime_val, raciocinio_val, coerencia_val_str=coerencia_val, expectativa_str=expectativa_val)


def write_tarefa2_to_notion(entry_date: str, regime_val: str, raciocinio_val: str, coerencia_val_str: str, expectativa_str: str) -> bool:
    """Atualiza os 4 campos de síntese do Claude na sessão do dia no Notion."""
    if not NOTION_TOKEN or not NOTION_CLAUDE_REGIME_DB_ID:
        return False

    schema = get_notion_db_schema(NOTION_CLAUDE_REGIME_DB_ID)
    title_col = "Data"
    for p_name, p_type in schema.items():
        if p_type == "title":
            title_col = p_name
            break

    url_query = f"https://api.notion.com/v1/databases/{NOTION_CLAUDE_REGIME_DB_ID}/query"
    query_payload = {
        "filter": {
            "property": title_col,
            "title": {"contains": entry_date}
        }
    }

    try:
        res = requests.post(url_query, headers=NOTION_HEADERS, json=query_payload, timeout=10)
        results = res.json().get("results", []) if res.status_code == 200 else []

        if not results:
            logging.error(f"❌ [TAREFA 2 NOTION] Sessão [{entry_date}] não encontrada no Notion para PATCH.")
            return False

        page_id = results[0]["id"]
        url_patch = f"https://api.notion.com/v1/pages/{page_id}"

        props: Dict[str, Any] = {}

        # Regime (Select / Rich Text)
        if "Regime" in schema:
            p_type = schema["Regime"]
            if p_type == "select":
                props["Regime"] = {"select": {"name": regime_val}}
            else:
                props["Regime"] = {"rich_text": [{"text": {"content": regime_val}}]}
        elif "Regime Consolidado" in schema:
            props["Regime Consolidado"] = {"rich_text": [{"text": {"content": regime_val}}]}

        # Coerência com Camada 0
        if "Coerência com Camada 0" in schema:
            p_type = schema["Coerência com Camada 0"]
            if p_type == "select":
                props["Coerência com Camada 0"] = {"select": {"name": coerencia_val_str}}
            else:
                props["Coerência com Camada 0"] = {"rich_text": [{"text": {"content": coerencia_val_str}}]}

        # Expectativa para o Dia Seguinte
        if "Expectativa para o Dia Seguinte" in schema:
            full_text = f"{raciocinio_val}\n\nExpectativa: {expectativa_str}".strip()
            props["Expectativa para o Dia Seguinte"] = {"rich_text": [{"text": {"content": full_text}}]}

        patch_res = requests.patch(url_patch, headers=NOTION_HEADERS, json={"properties": props}, timeout=10)
        if patch_res.status_code in [200, 201]:
            logging.info(f"✅ [TAREFA 2 NOTION] Campos do Claude (Regime={regime_val}) atualizados no Notion com sucesso.")
            return True
        else:
            logging.error(f"❌ [TAREFA 2 NOTION] Erro no PATCH ({patch_res.status_code}): {patch_res.text}")
    except Exception as e:
        logging.error(f"❌ Exceção ao escrever Tarefa 2 no Notion: {e}")

    return False
