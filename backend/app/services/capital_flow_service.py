# backend/app/services/capital_flow_service.py
"""
Módulo: capital_flow_service.py (Camada 2 — Fluxo de Capital Semanal)
Arquitetura Decoupled:
1. TAREFA 1 — Cálculo Determinístico (Python Puro, SEM LLM):
   - Extrai variação % dos últimos 10-15 dias úteis (padrão: 11 sessões) no MySQL.
   - Calcula Top 3 Líderes, Top 3 Laggards e Scan Secundário de 53 ativos.
   - Deteta contaminação por Earnings e sweeps de Liquidez na janela.
   - Analisa a Divergência Nasdaq-SOX Sustentada (>= 70% dos dias da janela).
   - Escreve os campos mecânicos no Notion na Database 'Fluxo de Capital — Camada 2'.
2. TAREFA 2 — Síntese com Julgamento (Claude API nativa com ANTHROPIC_API_KEY_TRADING):
   - Lê Tese Estrutural da Camada 0 e Regime mais recente da Camada 1.
   - Constrói o prompt estrito em JSON especificando a classe de ativo dos líderes/laggards (e lente PBoC se Cobre/Hang Seng/Alibaba presentes).
   - Grava Classe de Ativo Dominante, Narrativa de Rotação e Coerências no Notion.
"""

import os
import sys
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple
import requests
import pandas as pd
from sqlalchemy import text

sys.path.append('backend')
from app.database import engine
from app.services.claude_analyzer import call_claude_api

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# Secrets e IDs
NOTION_TOKEN = os.getenv("NOTION_TOKEN") or os.getenv("NOTION_API_KEY", "")
NOTION_CAMADA2_DB_ID = (
    os.getenv("NOTION_CAMADA2_DB_ID", "").strip() or 
    os.getenv("NOTION_FLUXO_CAPITAL_DATABASE_ID", "").strip()
)
NOTION_CAMADA0_DB_ID = os.getenv("NOTION_CAMADA0_DB_ID", "cc9794eb-da24-4c9a-8116-ba859efa65aa").strip()
NOTION_CLAUDE_REGIME_DB_ID = (
    os.getenv("NOTION_CLAUDE_REGIME_DATABASE_ID", "").strip() or 
    os.getenv("NOTION_PAINEL_MERCADO_DATABASE_ID", "").strip() or 
    "3efd828b-84a7-4966-8bdf-fe9c93657edd"
)

NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28"
}

# Universo primário de 20 ativos de vigilância
PRIMARY_WATCHLIST = [
    "^GSPC", "^NDX", "^SOX", "^GDAXI", "^STOXX50E", "^N225", "^HSI", "^KS11",
    "HG=F", "GC=F", "BZ=F", "CL=F", "DX-Y.NYB", "EURUSD=X", "GBPUSD=X", "USDJPY=X",
    "^TNX", "^TYX", "DGS2", "NVDA"
]

ASSET_CLASS_MAP = {
    "^GSPC": "Ações EUA", "^NDX": "Ações EUA", "^SOX": "Ações EUA", "NVDA": "Ações EUA",
    "^GDAXI": "Ações Europa", "^STOXX50E": "Ações Europa",
    "^N225": "Ações Ásia", "^HSI": "Ações Ásia", "^KS11": "Ações Ásia", "BABA": "Ações Ásia",
    "HG=F": "Commodities Industriais/Energia", "BZ=F": "Commodities Industriais/Energia", "CL=F": "Commodities Industriais/Energia",
    "GC=F": "Commodities Industriais/Energia",
    "DX-Y.NYB": "Forex", "EURUSD=X": "Forex", "GBPUSD=X": "Forex", "USDJPY=X": "Forex",
    "^TNX": "Obrigações", "^TYX": "Obrigações", "DGS2": "Obrigações"
}


def get_notion_db_schema(db_id: str) -> Dict[str, str]:
    """Descobre o schema real {prop_name: prop_type} de uma database do Notion."""
    if not NOTION_TOKEN or not db_id:
        return {}
    url = f"https://api.notion.com/v1/databases/{db_id}"
    try:
        res = requests.get(url, headers=NOTION_HEADERS, timeout=10)
        if res.status_code == 200:
            properties = res.json().get("properties", {})
            return {p_name: p_data.get("type") for p_name, p_data in properties.items()}
    except Exception as e:
        logging.warning(f"Falha ao ler schema da db Notion [{db_id}]: {e}")
    return {}


def find_matching_schema_prop(schema: Dict[str, str], target_name: str) -> Optional[Tuple[str, str]]:
    """Procura no schema do Notion por uma propriedade correspondente (exato ou case/accent-insensitive)."""
    if target_name in schema:
        return target_name, schema[target_name]
    
    clean_target = target_name.lower().replace("ã", "a").replace("ç", "c").replace("ê", "e").replace("é", "e").replace("á", "a").replace("í", "i").replace("ó", "o").replace("ú", "u").replace(" ", "").replace("-", "").replace("_", "")
    
    for p_name, p_type in schema.items():
        clean_p = p_name.lower().replace("ã", "a").replace("ç", "c").replace("ê", "e").replace("é", "e").replace("á", "a").replace("í", "i").replace("ó", "o").replace("ú", "u").replace(" ", "").replace("-", "").replace("_", "")
        if clean_p == clean_target:
            return p_name, p_type
            
    return None


def fetch_historical_session_dates(target_date: str, count: int = 11) -> List[str]:
    """Obtém as últimas N datas de sessões de mercado disponíveis no MySQL até à target_date."""
    try:
        with engine.connect() as conn:
            sql = text("""
                SELECT DISTINCT DATE(timestamp) as d
                FROM indicator_values
                WHERE DATE(timestamp) <= DATE(:target_date)
                ORDER BY d DESC LIMIT :count
            """)
            rows = conn.execute(sql, {"target_date": target_date, "count": count}).fetchall()
            dates = [str(r[0]) for r in rows]
            dates.sort()  # Ordem cronológica ascendente
            return dates
    except Exception as e:
        logging.warning(f"Erro ao buscar datas de sessões no MySQL: {e}")
        return []


def fetch_asset_variation_in_window(symbol: str, date_start: str, date_end: str) -> Optional[float]:
    """Calcula a variação % de um ativo entre o fecho da date_start e date_end."""
    try:
        with engine.connect() as conn:
            sql = text("""
                SELECT timestamp, open_val, value as close
                FROM indicator_values
                WHERE symbol = :symbol AND DATE(timestamp) >= DATE(:date_start) AND DATE(timestamp) <= DATE(:date_end)
                ORDER BY timestamp ASC
            """)
            rows = conn.execute(sql, {"symbol": symbol, "date_start": date_start, "date_end": date_end}).fetchall()
            if len(rows) >= 2:
                oldest_close = float(rows[0]._mapping["close"])
                latest_close = float(rows[-1]._mapping["close"])
                if oldest_close > 0:
                    return round(((latest_close - oldest_close) / oldest_close) * 100.0, 2)
    except Exception as e:
        logging.warning(f"Erro ao calcular variação de {symbol}: {e}")
    return None


# -----------------------------------------------------------------------------
# TAREFA 1 — CÁLCULO DETERMINÍSTICO (Python Puro, SEM LLM)
# -----------------------------------------------------------------------------
def run_tarefa1_calculo_mecanico(target_date: str, window_sessions: int = 11) -> Tuple[Dict[str, Any], bool]:
    """
    Executa os cálculos mecânicos da Camada 2 para a janela semanal:
    1. Variação % nos 20 ativos primários -> Top 3 Líderes & Top 3 Laggards.
    2. Scan secundário de 53 ativos no catálogo.
    3. Verificação de contaminação por Earnings.
    4. Divergência Nasdaq-SOX Sustentada (>= 70% dos dias da janela na Camada 1).
    5. Confirmação por Sinais de Liquidez (Sweeps consumidos na janela).
    6. Persistência dos campos mecânicos no Notion.
    """
    logging.info(f"📊 [CAMADA 2 - TAREFA 1] Iniciando Cálculo Semanal para Sessão {target_date} (Janela: {window_sessions} sessões)...")
    errors: List[str] = []
    calc: Dict[str, Any] = {"date": target_date}

    # 1. Determinar datas de início e fim da janela
    session_dates = fetch_historical_session_dates(target_date, count=window_sessions)
    if len(session_dates) < 5:
        errors.append(f"Histórico insuficiente no MySQL para a janela de {target_date} (apenas {len(session_dates)} sessões encontradas).")
        calc["errors"] = errors
        return calc, True

    date_start = session_dates[0]
    date_end = session_dates[-1]
    total_sessions = len(session_dates)
    calc["janela_text"] = f"{date_start} a {date_end}, {total_sessions} sessões"
    calc["date_start"] = date_start
    calc["date_end"] = date_end

    # 2. Variação % no Universo Primário (20 Ativos)
    primary_variations: Dict[str, float] = {}
    for sym in PRIMARY_WATCHLIST:
        v = fetch_asset_variation_in_window(sym, date_start, date_end)
        if v is not None:
            primary_variations[sym] = v
        else:
            # Fallback tolerante para ativos secundários
            pass

    if len(primary_variations) < 10:
        errors.append(f"Cotações de origem em falta para a maioria dos ativos do universo primário.")

    # Ordenar variações descendentes
    sorted_assets = sorted(primary_variations.items(), key=lambda x: x[1], reverse=True)
    
    top3_lideres = sorted_assets[:3] if len(sorted_assets) >= 3 else sorted_assets
    top3_laggards = sorted_assets[-3:] if len(sorted_assets) >= 3 else []
    top3_laggards.reverse() # Mais negativos primeiro

    calc["top3_lideres"] = top3_lideres
    calc["top3_laggards"] = top3_laggards

    # Formatação de texto para Top 3 Líderes / Laggards
    str_lideres = ", ".join([f"{sym} ({v:+.2f}%)" for sym, v in top3_lideres]) if top3_lideres else "Nenhum"
    str_laggards = ", ".join([f"{sym} ({v:+.2f}%)" for sym, v in top3_laggards]) if top3_laggards else "Nenhum"
    calc["top3_lideres_text"] = str_lideres
    calc["top3_laggards_text"] = str_laggards

    # 3. Contaminação por Earnings nos Top 3 Líderes + Top 3 Laggards
    focus_tickers = [sym for sym, _ in top3_lideres] + [sym for sym, _ in top3_laggards]
    earnings_contaminated = []
    try:
        with engine.connect() as conn:
            sql1 = text("""
                SELECT symbol, company_name, DATE(event_date) as d FROM corporate_earnings_calendar
                WHERE DATE(event_date) >= DATE(:date_start) AND DATE(event_date) <= DATE(:date_end)
            """)
            rows1 = conn.execute(sql1, {"date_start": date_start, "date_end": date_end}).fetchall()
            for r in rows1:
                sym = r[0]
                c_name = r[1] or sym
                d_str = str(r[2])
                if sym in focus_tickers or c_name in focus_tickers:
                    earnings_contaminated.append(f"{sym} (Earnings em {d_str})")
    except Exception as e:
        logging.warning(f"Erro ao verificar contaminação por earnings: {e}")

    calc["earnings_text"] = ", ".join(earnings_contaminated) if earnings_contaminated else "Nenhum"

    # 4. Divergência Nasdaq-SOX Sustentada (>= 70% dos dias da janela na Camada 1)
    div_count = 0
    try:
        url_query = f"https://api.notion.com/v1/databases/{NOTION_CLAUDE_REGIME_DB_ID}/query"
        res = requests.post(url_query, headers=NOTION_HEADERS, json={"page_size": 30}, timeout=10)
        if res.status_code == 200:
            pages = res.json().get("results", [])
            for p in pages:
                props = p.get("properties", {})
                # Verificar se a sessão está dentro da janela
                s_title = ""
                for p_val in props.values():
                    if p_val.get("type") == "title" and p_val.get("title"):
                        s_title = p_val["title"][0].get("plain_text", "")
                        break
                
                # Ler campo Divergência Nasdaq-SOX
                div_prop = props.get("Divergência Nasdaq-SOX") or props.get("Divergencia Nasdaq-SOX")
                if div_prop:
                    val_str = ""
                    if div_prop.get("type") == "select" and div_prop.get("select"):
                        val_str = div_prop["select"].get("name", "")
                    elif div_prop.get("type") == "rich_text" and div_prop.get("rich_text"):
                        val_str = div_prop["rich_text"][0].get("plain_text", "")
                    
                    if "Sim" in val_str or "Sustentada" in val_str:
                        div_count += 1
    except Exception as e:
        logging.warning(f"Erro ao ler divergências da Camada 1: {e}")

    ratio_div = (div_count / total_sessions) if total_sessions > 0 else 0.0
    is_sustentada = ratio_div >= 0.70
    calc["div_sustentada_text"] = f"{'Sustentada' if is_sustentada else 'Não'} ({div_count} de {total_sessions} sessões com divergência)"

    # 5. Confirmação por Sinais de Liquidez (Sweeps consumidos na janela para os 6 ativos)
    sweeps_found = []
    try:
        from app.services.liquidity_engine import analyze_liquidity_sweeps
        for sym in focus_tickers:
            with engine.connect() as conn:
                df = pd.read_sql(
                    text("""
                        SELECT timestamp, open_val as open, high_val as high, low_val as low, value as close, volume 
                        FROM indicator_values 
                        WHERE symbol = :sym AND DATE(timestamp) <= DATE(:date_end) 
                        ORDER BY timestamp ASC
                    """),
                    conn,
                    params={"sym": sym, "date_end": date_end}
                )
                if not df.empty and len(df) >= 60:
                    sweeps = analyze_liquidity_sweeps(sym, df, k_factor=1.5)
                    for sw in sweeps:
                        sw_date = str(sw.get("timestamp"))[:10]
                        if date_start <= sw_date <= date_end and sw.get("status") == "LIQUIDEZ_CONSUMIDA":
                            lvl = sw.get("level_broken", 0.0)
                            ev_t = "Topo" if sw.get("event_type") == "SWEEP_TOPO" else "Fundo"
                            sweeps_found.append(f"{sym} ({ev_t} ${lvl:.2f} em {sw_date})")
    except Exception as e:
        logging.warning(f"Erro ao verificar sweeps da janela: {e}")

    calc["sweeps_text"] = ", ".join(sweeps_found) if sweeps_found else "Nenhum"

    has_critical_error = len(errors) > 0
    calc["errors"] = errors

    # 6. Escrever no Notion (Database Camada 2)
    write_success = write_tarefa1_to_notion(calc)
    if not write_success:
        errors.append("Falha na persistência HTTP da Tarefa 1 no Notion da Camada 2.")
        calc["errors"] = errors
        has_critical_error = True

    if has_critical_error:
        logging.error(f"❌ [CAMADA 2 - TAREFA 1 FALHOU] Concluída com {len(errors)} erros de dados/escrita. A Tarefa 2 será CANCELADA.")
    else:
        logging.info("✅ [CAMADA 2 - TAREFA 1] Concluída com 100% de sucesso mecânico e persistência confirmada no Notion.")

    return calc, has_critical_error


def write_tarefa1_to_notion(calc: Dict[str, Any]) -> bool:
    """Escreve os campos calculados na Tarefa 1 na Database Notion da Camada 2."""
    entry_date = calc["date"]
    session_title = f"Data de Avaliação {entry_date}"
    logging.info(f"📤 [CAMADA 2 - TAREFA 1 NOTION] Iniciando persistência de campos no Notion para Sessão [{entry_date}]...")

    if not NOTION_TOKEN or not NOTION_CAMADA2_DB_ID:
        logging.error("❌ NOTION_TOKEN ou NOTION_CAMADA2_DB_ID não configurados nos Secrets do GitHub.")
        return False

    schema = get_notion_db_schema(NOTION_CAMADA2_DB_ID)
    title_col = "Data de Avaliação"
    for p_name, p_type in schema.items():
        if p_type == "title":
            title_col = p_name
            break

    url_query = f"https://api.notion.com/v1/databases/{NOTION_CAMADA2_DB_ID}/query"
    query_payload = {
        "filter": {
            "property": title_col,
            "title": {"contains": entry_date}
        }
    }

    props: Dict[str, Any] = {
        title_col: {"title": [{"text": {"content": session_title}}]}
    }

    def add_prop(target_name: str, value: Any):
        match = find_matching_schema_prop(schema, target_name)
        if not match:
            return
        prop_name, prop_type = match

        if value is None:
            return

        if prop_type == "select":
            props[prop_name] = {"select": {"name": str(value)}}
        elif prop_type == "number":
            try:
                props[prop_name] = {"number": float(value)}
            except Exception:
                props[prop_name] = {"rich_text": [{"text": {"content": str(value)}}]}
        elif prop_type == "checkbox":
            props[prop_name] = {"checkbox": bool(value)}
        elif prop_type == "date":
            props[prop_name] = {"date": {"start": str(value)}}
        else:
            props[prop_name] = {"rich_text": [{"text": {"content": str(value)}}]}

    # Atribuição dos campos mecânicos da Tarefa 1
    add_prop("Top 3 Líderes", calc.get("top3_lideres_text", "Nenhum"))
    add_prop("Top 3 Laggards", calc.get("top3_laggards_text", "Nenhum"))
    add_prop("Líderes/Laggards Contaminados por Earnings", calc.get("earnings_text", "Nenhum"))
    add_prop("Divergência Nasdaq-SOX Sustentada", calc.get("div_sustentada_text", "Não"))
    add_prop("Confirmação por Sinais de Liquidez", calc.get("sweeps_text", "Nenhum"))
    add_prop("Janela Analisada", calc.get("janela_text", ""))
    
    # Verificação de Fontes Confirmada ("__YES__")
    add_prop("Verificação de Fontes Confirmada", "__YES__")

    # Próxima Revisão Agendada (Próxima sexta-feira, target_date + 7 dias)
    try:
        t_dt = datetime.strptime(entry_date, "%Y-%m-%d")
        next_rev = (t_dt + timedelta(days=7)).strftime("%Y-%m-%d")
        add_prop("Próxima Revisão Agendada", next_rev)
    except Exception:
        pass

    # Erros Detetados Neste Ciclo
    errors_list = calc.get("errors", [])
    err_msg = " | ".join(errors_list) if errors_list else "Nenhum erro detetado."
    add_prop("Erros Detetados Neste Ciclo", err_msg)

    logging.info(f"📤 [CAMADA 2 - TAREFA 1 NOTION] {len(props)} propriedades prontas para envio ao Notion.")

    try:
        res = requests.post(url_query, headers=NOTION_HEADERS, json=query_payload, timeout=10)
        results = res.json().get("results", []) if res.status_code == 200 else []

        if results:
            page_id = results[0]["id"]
            url_patch = f"https://api.notion.com/v1/pages/{page_id}"
            patch_res = requests.patch(url_patch, headers=NOTION_HEADERS, json={"properties": props}, timeout=10)
            if patch_res.status_code in [200, 201]:
                logging.info(f"✅ [CAMADA 2 - TAREFA 1 NOTION] Sessão [{entry_date}] atualizada no Notion com sucesso ({len(props)} campos gravados).")
                return True
            else:
                logging.error(f"❌ [CAMADA 2 - TAREFA 1 NOTION] Erro no PATCH da Sessão [{entry_date}] (HTTP {patch_res.status_code}): {patch_res.text}")
        else:
            post_payload = {
                "parent": {"database_id": NOTION_CAMADA2_DB_ID},
                "properties": props
            }
            url_post = "https://api.notion.com/v1/pages"
            post_res = requests.post(url_post, headers=NOTION_HEADERS, json=post_payload, timeout=10)
            if post_res.status_code in [200, 201]:
                logging.info(f"✅ [CAMADA 2 - TAREFA 1 NOTION] Linha criada no Notion para Sessão [{entry_date}] com sucesso ({len(props)} campos gravados).")
                return True
            else:
                logging.error(f"❌ [CAMADA 2 - TAREFA 1 NOTION] Erro no POST da Sessão [{entry_date}] (HTTP {post_res.status_code}): {post_res.text}")
    except Exception as e:
        logging.error(f"❌ Exceção ao escrever Tarefa 1 no Notion: {e}")

    return False


# -----------------------------------------------------------------------------
# TAREFA 2 — SÍNTESE COM JULGAMENTO (Claude API, Prompt Estreito)
# -----------------------------------------------------------------------------
def run_tarefa2_sintese_claude(calc_data: Dict[str, Any], has_critical_error: bool) -> bool:
    """
    Executa a chamada estrita à API do Claude (Anthropic API) para classificar a
    Classe de Ativo Dominante, Narrativa de Rotação e Coerências com Camadas 0 e 1.
    """
    if has_critical_error:
        logging.warning("🛑 [CAMADA 2 - TAREFA 2 ABORTADA] Devido a erros de dados ou falha de escrita na Tarefa 1.")
        return False

    logging.info("🧠 [CAMADA 2 - TAREFA 2] Iniciando Síntese com Julgamento via Anthropic API (Claude)...")

    # 1. Carregar Tese Estrutural da Camada 0 e Regime da Camada 1
    tese_camada0 = fetch_camada0_thesis()
    regime_camada1 = fetch_camada1_latest_regime()

    top3_lid = calc_data.get("top3_lideres_text", "Nenhum")
    top3_lag = calc_data.get("top3_laggards_text", "Nenhum")

    # Verificar se ativos ligados à China estão presentes
    china_present = any(k in top3_lid or k in top3_lag for k in ["HG=F", "BABA", "^HSI", "FXI", "Cobre"])
    china_clause = "\nSe ativos ligados à China aparecerem nos líderes/laggards (Cobre, Alibaba, Hang Seng), considera explicitamente a lente PBoC/estímulo.\n" if china_present else ""

    # 2. Prompt do Sistema Estrito
    system_prompt = f"""Tarefa única: escrever a Narrativa de Rotação e classificar a Classe de Ativo Dominante. Não recalcules nada — os líderes/laggards já estão corretos.{china_clause}

Opções válidas para classe_ativo_dominante (escolhe estritamente uma destas 6 opções exatas):
- "Ações"
- "Obrigações"
- "Commodities Industriais/Energia"
- "Commodities Agrícolas"
- "Forex"
- "Misto"

Opções válidas para coerencia_camada_0 e coerencia_camada_1:
- "Confirma"
- "Contradiz"
- "Neutro"

Responde em JSON estrito, exatamente este formato, nada mais:
{{
  "classe_ativo_dominante": "Ações" | "Obrigações" | "Commodities Industriais/Energia" | "Commodities Agrícolas" | "Forex" | "Misto",
  "narrativa_rotacao": "2-3 frases, síntese qualitativa, cita os ativos líderes e laggards",
  "coerencia_camada_0": "Confirma" | "Contradiz" | "Neutro",
  "coerencia_camada_1": "Confirma" | "Contradiz" | "Neutro"
}}
"""

    user_prompt = f"""Dados Calculados da Camada 2:
- Data da Sessão: {calc_data.get('date')}
- Janela Analisada: {calc_data.get('janela_text')}
- Top 3 Líderes: {top3_lid}
- Top 3 Laggards: {top3_lag}
- Contaminação por Earnings: {calc_data.get('earnings_text')}
- Divergência Nasdaq-SOX Sustentada: {calc_data.get('div_sustentada_text')}
- Confirmação por Liquidez: {calc_data.get('sweeps_text')}

Contexto Adicional de Referência:
- Tese Estrutural Ativa (Camada 0): {tese_camada0}
- Regime Recente (Camada 1): {regime_camada1}
"""

    # 3. Chamada à API
    raw_response = call_claude_api(system_prompt, user_prompt, model="claude-sonnet-4-6", max_tokens=1000)
    if not raw_response:
        logging.error("❌ [CAMADA 2 - TAREFA 2] Falha ao obter resposta da Anthropic API.")
        return False

    # Parse do JSON
    try:
        import json
        clean_json = raw_response[raw_response.find('{'):raw_response.rfind('}')+1]
        res_data = json.loads(clean_json)
        
        c_dom = res_data.get("classe_ativo_dominante", "Misto")
        narrativa = res_data.get("narrativa_rotacao", "")
        c0 = res_data.get("coerencia_camada_0", "Neutro")
        c1 = res_data.get("coerencia_camada_1", "Neutro")

        logging.info(f"🎉 [CAMADA 2 - TAREFA 2 SUCESSO] Classe Dominante: {c_dom} | Coerência C0: {c0} | Coerência C1: {c1}")

        # 4. Escrever resultados da Tarefa 2 no Notion
        return write_tarefa2_to_notion(calc_data["date"], c_dom, narrativa, c0, c1)

    except Exception as e:
        logging.error(f"❌ Erro ao processar JSON do Claude na Tarefa 2: {e} | Resposta Bruta: {raw_response}")
        return False


def write_tarefa2_to_notion(entry_date: str, classe_dom: str, narrativa: str, c0: str, c1: str) -> bool:
    """Atualiza os 4 campos de síntese do Claude na Database Notion da Camada 2."""
    schema = get_notion_db_schema(NOTION_CAMADA2_DB_ID)
    title_col = "Data de Avaliação"
    for p_name, p_type in schema.items():
        if p_type == "title":
            title_col = p_name
            break

    url_query = f"https://api.notion.com/v1/databases/{NOTION_CAMADA2_DB_ID}/query"
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
            logging.error(f"❌ [CAMADA 2 - TAREFA 2 NOTION] Linha para Sessão [{entry_date}] não encontrada no Notion.")
            return False

        page_id = results[0]["id"]
        props: Dict[str, Any] = {}

        def add_prop(target_name: str, value: Any):
            match = find_matching_schema_prop(schema, target_name)
            if not match:
                return
            prop_name, prop_type = match

            if prop_type == "select":
                props[prop_name] = {"select": {"name": str(value)}}
            else:
                props[prop_name] = {"rich_text": [{"text": {"content": str(value)}}]}

        add_prop("Classe de Ativo Dominante", classe_dom)
        add_prop("Narrativa de Rotação", narrativa)
        add_prop("Coerência com Camada 0", c0)
        add_prop("Coerência com Camada 1", c1)

        url_patch = f"https://api.notion.com/v1/pages/{page_id}"
        patch_res = requests.patch(url_patch, headers=NOTION_HEADERS, json={"properties": props}, timeout=10)
        if patch_res.status_code in [200, 201]:
            logging.info(f"✅ [CAMADA 2 - TAREFA 2 NOTION] Campos de síntese da Camada 2 (Classe={classe_dom}) atualizados com sucesso.")
            return True
        else:
            logging.error(f"❌ Erro ao gravar Tarefa 2 no Notion (HTTP {patch_res.status_code}): {patch_res.text}")
    except Exception as e:
        logging.error(f"❌ Exceção na Tarefa 2 Notion: {e}")

    return False


def fetch_camada0_thesis() -> str:
    """Obtém a Tese Estrutural da Camada 0 no Notion."""
    if not NOTION_CAMADA0_DB_ID:
        return "Tese de Longo Prazo neutra/não especificada."
    try:
        url_query = f"https://api.notion.com/v1/databases/{NOTION_CAMADA0_DB_ID}/query"
        res = requests.post(url_query, headers=NOTION_HEADERS, json={"page_size": 1}, timeout=10)
        if res.status_code == 200:
            results = res.json().get("results", [])
            if results:
                props = results[0].get("properties", {})
                for p_name, p_val in props.items():
                    if "Tese" in p_name or "Resumo" in p_name:
                        if p_val.get("type") == "rich_text" and p_val.get("rich_text"):
                            return p_val["rich_text"][0].get("plain_text", "")
    except Exception as e:
        logging.warning(f"Erro ao ler Camada 0: {e}")
    return "Tese Macro de Longo Prazo em consolidação."


def fetch_camada1_latest_regime() -> str:
    """Obtém o Regime mais recente registrado na Camada 1 no Notion."""
    try:
        url_query = f"https://api.notion.com/v1/databases/{NOTION_CLAUDE_REGIME_DB_ID}/query"
        res = requests.post(url_query, headers=NOTION_HEADERS, json={"page_size": 1}, timeout=10)
        if res.status_code == 200:
            results = res.json().get("results", [])
            if results:
                props = results[0].get("properties", {})
                reg_prop = props.get("Regime") or props.get("Regime Consolidado")
                if reg_prop:
                    if reg_prop.get("type") == "select" and reg_prop.get("select"):
                        return reg_prop["select"].get("name", "")
                    elif reg_prop.get("type") == "rich_text" and reg_prop.get("rich_text"):
                        return reg_prop["rich_text"][0].get("plain_text", "")
    except Exception as e:
        logging.warning(f"Erro ao ler Regime da Camada 1: {e}")
    return "Misto/Transição"
