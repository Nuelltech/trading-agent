# backend/app/services/catalysts_service.py
"""
Módulo: catalysts_service.py (Cron de Catalisadores — Leitura de Mapas de Transmissão)
Especificação Técnica: 11/08/2026

Princípio de Desenho — 3 Tarefas Estritamente Separadas:
- TAREFA 1: Identificar Candidatos (Mecânico, SEM LLM)
- TAREFA 2: Escolher Mecanismo e Escrever Previsão (Claude API — 1 EVENTO DE CADA VEZ)
- TAREFA 3: Validação Retrospetiva (Mecânico, SEM LLM, encadeada pós-evento)
"""

import sys
import os
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple
import requests
from sqlalchemy import text

sys.path.append('backend')
from app.database import engine
from app.services.notion_page_reader_service import fetch_notion_page_content
from app.services.claude_analyzer import call_claude_api

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

NOTION_TOKEN = os.getenv("NOTION_TOKEN") or os.getenv("NOTION_API_KEY", "")
NOTION_CALENDAR_DB_ID = os.getenv("NOTION_CALENDAR_DB_ID", "bb7305d4-8a74-4622-b981-6f9a34bb0f35").strip()
NOTION_CAMADA0_DB_ID = os.getenv("NOTION_CAMADA0_DB_ID", "cc9794eb-da24-4c9a-8116-ba859efa65aa").strip()

NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28"
}

# -----------------------------------------------------------------------------
# TABELA DE CORRESPONDÊNCIA FIXA E ESTRITA (EVENTO -> NOME DO MAPA)
# -----------------------------------------------------------------------------
EVENTO_PARA_MAPA: Dict[str, str] = {
    "Fed Interest Rate Decision": "Fed",
    "US Non-Farm Payrolls (NFP)": "NFP",
    "US CPI (YoY)": "CPI",
    "US Core CPI (MoM)": "CPI",
    "UK CPI (YoY)": "CPI",
    "Eurozone CPI (YoY)": "CPI",
    "ISM Manufacturing PMI": "PMI",
    "ISM Services PMI": "PMI",
    "China Caixin Manufacturing PMI": "PMI",
    "China Official NBS Manufacturing PMI": "PMI",
    "BoJ Interest Rate Decision": "BoJ",
    "ECB Interest Rate Decision": "ECB",
    "US Core PCE Price Index (MoM)": "PCE",
    "US GDP (QoQ) Advance": "GDP",
    "BoE Interest Rate Decision": "BoE",
    "PBoC LPR 1-Year Rate Decision": "PBoC",
    "US Retail Sales (MoM)": "RetailSales",
}


def load_mapas_transmissao_ids() -> Dict[str, str]:
    """
    Carrega o mapeamento {"Nome do Mapa": "page_id"} a partir do secret NOTION_MAPAS_TRANSMISSAO_IDS.
    Suporta JSON com aspas duplas, aspas simples, ou blocos ```json.
    """
    raw_secret = os.getenv("NOTION_MAPAS_TRANSMISSAO_IDS", "").strip()
    if not raw_secret:
        logging.warning("⚠️ Secret NOTION_MAPAS_TRANSMISSAO_IDS não configurado (está vazio).")
        return {}

    clean = raw_secret
    if clean.startswith("```json"):
        clean = clean.split("```json", 1)[1].rsplit("```", 1)[0].strip()
    elif clean.startswith("```"):
        clean = clean.split("```", 1)[1].rsplit("```", 1)[0].strip()
    
    if (clean.startswith("'") and clean.endswith("'")) or (clean.startswith('"') and clean.endswith('"')):
        # Remover aspas exteriores se o secret foi envolto em aspas
        if not (clean.startswith('{"') or clean.startswith("{'")):
            clean = clean[1:-1].strip()

    try:
        return json.loads(clean)
    except Exception:
        try:
            fixed_json = clean.replace("'", '"')
            return json.loads(fixed_json)
        except Exception as e:
            logging.error(
                f"❌ Erro ao fazer parse do secret NOTION_MAPAS_TRANSMISSAO_IDS: {e}.\n"
                "Formato correto para o secret no GitHub Actions:\n"
                '{"Fed": "page_id", "NFP": "page_id", "CPI": "page_id", "PMI": "page_id", "BoJ": "page_id", "ECB": "page_id", "PCE": "page_id", "GDP": "page_id", "BoE": "page_id", "PBoC": "page_id", "RetailSales": "page_id"}'
            )
            return {}


def fetch_camada0_context() -> Tuple[str, str]:
    """
    Consulta a database estruturada 'Contexto Estrutural — Camada 0' via Notion Database Query API.
    Retorna a tupla (Regime de Inflação, Tese Estrutural Ativa).
    """
    if not NOTION_TOKEN or not NOTION_CAMADA0_DB_ID:
        logging.warning("⚠️ NOTION_TOKEN ou NOTION_CAMADA0_DB_ID não configurados.")
        return ("Desconhecido", "Sem tese disponível")

    url = f"https://api.notion.com/v1/databases/{NOTION_CAMADA0_DB_ID}/query"
    payload = {"page_size": 1}

    try:
        res = requests.post(url, headers=NOTION_HEADERS, json=payload, timeout=10)
        if res.status_code == 200:
            results = res.json().get("results", [])
            if results:
                props = results[0].get("properties", {})
                
                # 1. Regime de Inflação (Select ou Rich Text)
                regime_inflacao = "Neutro"
                for p_name, p_val in props.items():
                    if "Regime" in p_name or "Inflação" in p_name:
                        p_type = p_val.get("type")
                        if p_type == "select" and p_val.get("select"):
                            regime_inflacao = p_val["select"].get("name", "Neutro")
                        elif p_type == "rich_text" and p_val.get("rich_text"):
                            regime_inflacao = p_val["rich_text"][0].get("plain_text", "Neutro")

                # 2. Tese Estrutural Ativa
                tese_texto = ""
                for p_name, p_val in props.items():
                    if "Tese" in p_name or "Estrutural" in p_name:
                        p_type = p_val.get("type")
                        if p_type == "title" and p_val.get("title"):
                            tese_texto = p_val["title"][0].get("plain_text", "")
                        elif p_type == "rich_text" and p_val.get("rich_text"):
                            tese_texto = p_val["rich_text"][0].get("plain_text", "")

                return (regime_inflacao, tese_texto or "Tese Estrutural Ativa Padrão")
    except Exception as e:
        logging.error(f"❌ Exceção ao consultar Camada 0 no Notion: {e}")

    return ("Neutro", "Tese Estrutural Padrão")


# -----------------------------------------------------------------------------
# TAREFA 1 — IDENTIFICAR CANDIDATOS (MECÂNICO, SEM LLM)
# -----------------------------------------------------------------------------
def get_catalyst_candidates() -> List[Dict[str, Any]]:
    """
    Identifica eventos de Alto Impacto nos próximos 14 dias que necessitam de análise de catalisadores.
    Retorna lista de dicionários com dados do evento e page_id do Mapa correspondente.
    Inclui retentativas em caso de instabilidade de rede MySQL.
    """
    mapas_ids = load_mapas_transmissao_ids()
    candidates = []

    for attempt in range(1, 4):
        try:
            with engine.connect() as conn:
                sql = text("""
                    SELECT id, event_name, country, currency, event_timestamp, impact_level, actual_val, forecast_val, previous_val, unit
                    FROM economic_calendar
                    WHERE impact_level = 'HIGH'
                      AND event_timestamp BETWEEN NOW() AND NOW() + INTERVAL 14 DAY
                    ORDER BY event_timestamp ASC
                """)
                rows = conn.execute(sql).fetchall()

                for r in rows:
                    event_dict = dict(r._mapping)
                    event_name = str(event_dict.get("event_name", "")).strip()

                    # REGRA EXPLICITA: Se o nome não corresponder exatamente, ignorar
                    map_name = EVENTO_PARA_MAPA.get(event_name)
                    if not map_name:
                        logging.info(f"⏭️ [TAREFA 1] Evento '{event_name}' não consta da tabela estrita EVENTO_PARA_MAPA. Ignorando.")
                        continue

                    page_id = mapas_ids.get(map_name)
                    if not page_id:
                        logging.warning(f"⚠️ [TAREFA 1] Page ID do Mapa '{map_name}' não encontrado no secret NOTION_MAPAS_TRANSMISSAO_IDS para '{event_name}'.")
                        continue

                    event_dict["map_name"] = map_name
                    event_dict["map_page_id"] = page_id
                    candidates.append(event_dict)

                logging.info(f"📋 [TAREFA 1] {len(candidates)} eventos de alto impacto qualificados para análise de catalisadores nos próximos 14 dias.")
                return candidates
        except Exception as e:
            logging.warning(f"⚠️ [TAREFA 1] Tentativa {attempt}/3 falhou ao ligar à DB ({e}). A tentar novamente em 3s...")
            import time
            time.sleep(3)

    logging.error("❌ Falha permanente ao buscar candidatos a catalisadores na DB após 3 tentativas.")
    return []


# -----------------------------------------------------------------------------
# TAREFA 2 — ESCOLHER MECANISMO E ESCREVER PREVISÃO (CLAUDE API - 1 DE CADA VEZ)
# -----------------------------------------------------------------------------
def process_single_catalyst_event(event: Dict[str, Any]) -> bool:
    """
    Processa 1 ÚNICO EVENTO via Claude API, construindo a previsão condicional
    e gravando os 6 campos no Notion. Zero memória entre chamadas.
    """
    event_name = event["event_name"]
    map_name = event["map_name"]
    page_id = event["map_page_id"]

    logging.info(f"🧠 [TAREFA 2] Processando evento único: '{event_name}' com Mapa '{map_name}'...")

    # 1. Ler texto do Mapa de Transmissão via ferramenta de leitura de blocos
    map_content = fetch_notion_page_content(page_id)
    if not map_content:
        logging.error(f"❌ Não foi possível obter o conteúdo do Mapa '{map_name}' (Page ID: {page_id}). Abortando evento.")
        return False

    # 2. Ler Regime de Inflação e Tese da Camada 0 via Notion DB Query
    regime_inflacao, tese_estrutural = fetch_camada0_context()

    # 3. Formatar valores do evento
    forecast_str = f"{event.get('forecast_val')} {event.get('unit', '')}".strip() if event.get('forecast_val') is not None else "N/A"
    previous_str = f"{event.get('previous_val')} {event.get('unit', '')}".strip() if event.get('previous_val') is not None else "N/A"

    # 4. Prompt do Sistema Estrito (Exato conforme especificação)
    system_prompt = """Tarefa única: ler o Mapa de Transmissão fornecido, escolher qual mecanismo é mais relevante agora dado o Regime atual, e escrever uma previsão condicional e falsificável para este evento específico.

Não analises nenhum outro evento além deste. Não inventes mecanismos que não estejam no texto do Mapa fornecido — cita o número do mecanismo exato (ex: "Mecanismo 4").

Se o Mapa fornecido não tiver nenhum mecanismo claramente aplicável ao Regime atual, escreve isso explicitamente em vez de forçar uma escolha.

Responde em JSON estrito, exatamente este formato, nada mais:
{
  "mapas_aplicaveis": "nome do Mapa usado (ex: 'CPI')",
  "mecanismo_aplicavel": "número + 1 frase do porquê, citando o Regime",
  "previsao_condicional": "Se Real > Projetado → [efeito]. Se Real < Projetado → [efeito]. Se em linha → [efeito].",
  "ativo_relacionado": "lista dos ativos afetados, só os da nossa Configuração de Vigilância",
  "impacto_nos_nossos_ativos": "1-2 frases, quais dos nossos 20 vigiados são relevantes e porquê"
}"""

    user_prompt = f"""Evento a Analisar:
- Nome: {event_name}
- Data/Hora: {event.get('event_timestamp')}
- Projetado: {forecast_str}
- Anterior: {previous_str}

Contexto da Camada 0:
- Regime de Inflação Atual: {regime_inflacao}
- Tese Estrutural Ativa: {tese_estrutural}

Texto Completo do Mapa de Transmissão ({map_name}):
\"\"\"
{map_content}
\"\"\"
"""

    # 5. Chamar Claude API (Anthropic API)
    raw_response = call_claude_api(system_prompt, user_prompt, max_tokens=800)
    if not raw_response:
        logging.error(f"❌ Falha ao obter resposta do Claude para o evento '{event_name}'.")
        return False

    # 6. Parse JSON estrito
    try:
        clean_json = raw_response.strip()
        if clean_json.startswith("```json"):
            clean_json = clean_json.split("```json", 1)[1].rsplit("```", 1)[0].strip()
        elif clean_json.startswith("```"):
            clean_json = clean_json.split("```", 1)[1].rsplit("```", 1)[0].strip()

        parsed = json.loads(clean_json)
    except Exception as parse_err:
        logging.error(f"❌ Erro ao fazer parse do JSON do Claude para '{event_name}': {parse_err}. Resposta bruta:\n{raw_response}")
        return False

    # Extract fields
    mapas_app = str(parsed.get("mapas_aplicaveis") or map_name)
    mecanismo_app = str(parsed.get("mecanismo_aplicavel") or "")
    previsao_cond = str(parsed.get("previsao_condicional") or "")
    ativo_rel = str(parsed.get("ativo_relacionado") or "")
    impacto_ativos = str(parsed.get("impacto_nos_nossos_ativos") or "")

    logging.info(f"✅ [CLAUDE API RESULT] Evento: '{event_name}' | Mecanismo: '{mecanismo_app}'")

    # 7. Escrever os 6 campos no Notion na linha do evento
    return _write_catalyst_fields_to_notion(
        mysql_id=event["id"],
        event_name=event_name,
        mapas_aplicaveis=mapas_app,
        mecanismo_aplicavel=mecanismo_app,
        previsao_condicional=previsao_cond,
        ativo_relacionado=ativo_rel,
        impacto_nos_nossos_ativos=impacto_ativos,
        regime_no_momento=regime_inflacao
    )


def _write_catalyst_fields_to_notion(
    mysql_id: int,
    event_name: str,
    mapas_aplicaveis: str,
    mecanismo_aplicavel: str,
    previsao_condicional: str,
    ativo_relacionado: str,
    impacto_nos_nossos_ativos: str,
    regime_no_momento: str
) -> bool:
    """Escreve os 6 campos de catalisador na página correspondente no Notion."""
    if not NOTION_TOKEN or not NOTION_CALENDAR_DB_ID:
        logging.error("❌ NOTION_TOKEN ou NOTION_CALENDAR_DB_ID não configurados.")
        return False

    from app.services.notion_calendar_sync_service import _notion_find_existing_page, _build_rich_text

    page_id = _notion_find_existing_page(mysql_id, "economic_calendar")
    if not page_id:
        logging.warning(f"⚠️ Página do evento '{event_name}' (ID MySQL {mysql_id}) não encontrada no Notion. Não foi possível escrever catalisadores.")
        return False

    url_patch = f"https://api.notion.com/v1/pages/{page_id}"
    update_props = {
        "Mapas de Transmissão Aplicáveis": {"rich_text": _build_rich_text(mapas_aplicaveis)},
        "Mecanismo Aplicável": {"rich_text": _build_rich_text(mecanismo_aplicavel)},
        "Previsão Condicional": {"rich_text": _build_rich_text(previsao_condicional)},
        "Ativo Relacionado": {"rich_text": _build_rich_text(ativo_relacionado)},
        "Impacto nos Nossos Ativos": {"rich_text": _build_rich_text(impacto_nos_nossos_ativos)},
        "Regime no Momento da Previsão": {"rich_text": _build_rich_text(regime_no_momento)}
    }

    try:
        res = requests.patch(url_patch, headers=NOTION_HEADERS, json={"properties": update_props}, timeout=10)
        if res.status_code in [200, 201]:
            logging.info(f"🎉 [NOTION SUCESSO] 6 campos de catalisadores gravados com sucesso para '{event_name}'!")
            return True
        else:
            logging.error(f"❌ Erro HTTP {res.status_code} ao escrever catalisadores no Notion para '{event_name}': {res.text}")
    except Exception as e:
        logging.error(f"❌ Exceção ao escrever catalisadores no Notion para '{event_name}': {e}")

    return False


# -----------------------------------------------------------------------------
# TAREFA 3 — VALIDAÇÃO RETROSPETIVA (MECÂNICO, SEM LLM, ENCADEADA PÓS-EVENTO)
# -----------------------------------------------------------------------------
def validar_previsao_pos_evento(event_row: Dict[str, Any]) -> Optional[str]:
    """
    TAREFA 3 — VALIDAÇÃO RETROSPETIVA COMPLETA (MECÂNICA, VERIFICAÇÃO DE MERCADO REAL)
    
    Verifica se a reação real do mercado (preços em indicator_values: DXY, Ouro, Yields)
    correspondeu à previsão condicional elaborada para o evento.
    Retorna 'Acertou', 'Errou' ou 'Parcial' e grava na coluna 'Validação Pós-Evento' no Notion.
    """
    actual_val = event_row.get("actual_val")
    forecast_val = event_row.get("forecast_val")
    previsao_cond = event_row.get("previsao_condicional", "")
    event_timestamp = event_row.get("event_timestamp")
    event_id = event_row.get("id")

    if actual_val is None or forecast_val is None:
        return None

    # Se a previsão condicional não veio na row dict do MySQL, usar tese padrão por tipo de evento
    if not previsao_cond:
        event_name = str(event_row.get("event_name", ""))
        mapa_cat = EVENTO_PARA_MAPA.get(event_name, "")
        if "CPI" in mapa_cat or "CPI" in event_name or "PCE" in mapa_cat:
            previsao_cond = "Se a inflação sair acima do projetado, DXY sobe e yields sobem; se abaixo, DXY cai e ouro sobe."
        elif "Fed" in mapa_cat or "ECB" in mapa_cat or "BoE" in mapa_cat:
            previsao_cond = "Se as taxas sobem acima do projetado, DXY e yields sobem."
        elif "NFP" in mapa_cat or "Retail" in mapa_cat or "GDP" in mapa_cat:
            previsao_cond = "Se o indicador sair acima do projetado, DXY sobe."
        else:
            previsao_cond = "Se o indicador sair acima do projetado, DXY sobe."

    try:
        diff = float(actual_val) - float(forecast_val)
        macro_outcome = "acima" if diff > 0 else ("abaixo" if diff < 0 else "em linha")

        # 1. Obter a data do evento
        event_date_str = None
        if isinstance(event_timestamp, datetime):
            event_date_str = event_timestamp.strftime("%Y-%m-%d")
        elif event_timestamp:
            event_date_str = str(event_timestamp).split(" ")[0].split("T")[0]

        # 2. Consultar variação real dos ativos de referência na DB MySQL (DXY, Ouro, Yields, S&P500)
        asset_changes = {}
        if event_date_str:
            try:
                from app.database import engine
                with engine.connect() as conn:
                    sql_assets = text("""
                        SELECT symbol, open_val, value
                        FROM indicator_values
                        WHERE DATE(timestamp) = :edate
                          AND symbol IN ('DX-Y.NYB', 'GC=F', '^TNX', '^GSPC')
                    """)
                    rows = conn.execute(sql_assets, {"edate": event_date_str}).fetchall()
                    for r in rows:
                        sym, open_v, close_v = r[0], float(r[1] or 0), float(r[2] or 0)
                        if open_v > 0:
                            pct_var = ((close_v - open_v) / open_v) * 100.0
                            asset_changes[sym] = pct_var
            except Exception as db_err:
                logging.warning(f"⚠️ Erro ao obter cotações de ativos para validação retrospetiva: {db_err}")

        # 3. Avaliar Coerência entre a Tese da Previsão Condicional e a Reação de Mercado Real
        # Se a macro saiu 'acima', mas o DXY caiu / Ouro subiu forte (comportamento inverso ao previsto para DXY/Yields sobem) -> Errou
        dxy_var = asset_changes.get("DX-Y.NYB", 0.0)
        gold_var = asset_changes.get("GC=F", 0.0)
        tnx_var = asset_changes.get("^TNX", 0.0)

        validacao = "Parcial"
        cond_text = previsao_cond.lower()

        # Se a tese previa subida do dólar/yields quando a macro saísse acima, mas o mercado reagiu com queda do dólar/alta do ouro
        if macro_outcome == "acima":
            if "dxy sobe" in cond_text or "dólar sobe" in cond_text or "yields sobem" in cond_text or "pressão" in cond_text:
                if dxy_var < -0.05 or gold_var > 0.5:
                    # Ouro subiu forte ou DXY caiu -> Tese de mercado FALHOU
                    validacao = "Errou"
                else:
                    validacao = "Acertou"
            elif "abaixo" in cond_text:
                validacao = "Errou"
            else:
                validacao = "Parcial"
        elif macro_outcome == "abaixo":
            if "dxy cai" in cond_text or "dólar cai" in cond_text or "ouro sobe" in cond_text:
                if dxy_var > 0.1 or gold_var < -0.5:
                    validacao = "Errou"
                else:
                    validacao = "Acertou"
            elif "acima" in cond_text:
                validacao = "Errou"
            else:
                validacao = "Parcial"
        else:  # em linha
            validacao = "Parcial"

        logging.info(
            f"🎯 [VALIDAÇÃO RETROSPETIVA DE MERCADO] Evento '{event_row.get('event_name')}': "
            f"Macro={macro_outcome} (Real={actual_val} vs Forecast={forecast_val}) | "
            f"Reação Real Ativos (DXY={dxy_var:+.2f}%, Gold={gold_var:+.2f}%, TNX={tnx_var:+.2f}%) -> Validação: {validacao}"
        )

        # 4. Atualizar no Notion
        if event_id:
            try:
                from app.services.notion_calendar_sync_service import _notion_find_existing_page
                page_id = _notion_find_existing_page(event_id, "economic_calendar")
                if page_id:
                    url_patch = f"https://api.notion.com/v1/pages/{page_id}"
                    props = {"Validação Pós-Evento": {"select": {"name": validacao}}}
                    requests.patch(url_patch, headers=NOTION_HEADERS, json={"properties": props}, timeout=10)
                    logging.info(f"✅ Notion atualizado para página {page_id}: Validação Pós-Evento = '{validacao}'")
            except Exception as notion_err:
                logging.warning(f"⚠️ Erro ao atualizar Notion para validação: {notion_err}")

        return validacao
    except Exception as e:
        logging.warning(f"⚠️ Falha na validação retrospetiva: {e}")
        return None
