# backend/app/services/executive_summary_service.py
"""
Módulo: executive_summary_service.py (Agente do Resumo Executivo Diário)
Especificação Técnica: 11/08/2026

Especificações Principais:
1. Tickers Lidos Dinamicamente do MySQL: SELECT ticker FROM indicators_catalog WHERE is_active = TRUE
2. 5 Dimensões: Cenário Macro (Camada 0), Calendário (hoje + futuro 7d), Scan 53 Instrumentos (>2%), Posições Abertas, Previsão Multi-Dia.
3. Decoupled Pipeline (Tarefas 1, 1b, 1c, 2 e 3).
4. Suporte a dry_run=True (Flag --test-synthesis) para inspeção do JSON sem escrita no Notion.
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
from app.services.claude_analyzer import call_claude_api

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# Secrets
NOTION_TOKEN = os.getenv("NOTION_TOKEN") or os.getenv("NOTION_API_KEY", "")

NOTION_CLAUDE_REGIME_DB_ID = (
    os.getenv("NOTION_CLAUDE_REGIME_DATABASE_ID", "").strip() or 
    os.getenv("NOTION_PAINEL_MERCADO_DATABASE_ID", "").strip() or 
    "3efd828b-84a7-4966-8bdf-fe9c93657edd"
)
NOTION_CALENDAR_DB_ID = os.getenv("NOTION_CALENDAR_DB_ID", "bb7305d4-8a74-4622-b981-6f9a34bb0f35").strip()
NOTION_CAMADA0_DB_ID = os.getenv("NOTION_CAMADA0_DB_ID", "cc9794eb-da24-4c9a-8116-ba859efa65aa").strip()

NOTION_FEED_NOTICIAS_DB_ID = os.getenv("NOTION_FEED_NOTICIAS_DB_ID", "e1c8d3ab-a151-499f-8931-4537f29933ec").strip()
NOTION_DIARIO_BORDO_DB_ID = os.getenv("NOTION_DIARIO_BORDO_DB_ID", "").strip()
NOTION_RESUMO_EXECUTIVO_DB_ID = (
    os.getenv("NOTION_RESUMO_EXECUTIVO_DB_ID", "").strip() or
    os.getenv("NOTION_CLAUDE_REGIME_DATABASE_ID", "").strip() or
    os.getenv("NOTION_PAINEL_MERCADO_DATABASE_ID", "").strip() or
    "3efd828b-84a7-4966-8bdf-fe9c93657edd"
)

NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28"
}


def get_active_tickers_from_mysql() -> List[str]:
    """
    REGRA RÍGIDA: Lê os tickers ativos dinamicamente da tabela indicators_catalog no MySQL.
    NUNCA hardcoded. Inclui retentativas em caso de instabilidade de rede MySQL.
    """
    for attempt in range(1, 4):
        try:
            with engine.connect() as conn:
                sql = text("SELECT ticker FROM indicators_catalog WHERE is_active = TRUE OR is_active IS NULL")
                rows = conn.execute(sql).fetchall()
                tickers = [str(r[0]).strip() for r in rows if r[0]]
                if tickers:
                    logging.info(f"✅ [MYSQL LIVE] {len(tickers)} tickers ativos extraídos dinamicamente do indicators_catalog.")
                    return tickers
        except Exception as e:
            logging.warning(f"⚠️ Tentativa {attempt}/3 falhou ao consultar tickers no MySQL ({e}). A tentar novamente em 3s...")
            import time
            time.sleep(3)

    logging.error("❌ Falha permanente ao consultar tickers ativos no MySQL após 3 tentativas.")
    return []


# -----------------------------------------------------------------------------
# TAREFA 1 — SCAN DO UNIVERSO COMPLETO (MECÂNICO, SEM LLM, THRESHOLD 2%)
# -----------------------------------------------------------------------------
def task1_scan_candidates(target_date: str) -> List[Dict[str, Any]]:
    """
    Faz scan dos fechos de hoje vs. ontem para os tickers ativos no MySQL.
    Filtra abs(variação) > 2% e limita a no máximo 10 candidatos (maior magnitude).
    """
    tickers = get_active_tickers_from_mysql()
    if not tickers:
        return []

    candidates = []
    try:
        with engine.connect() as conn:
            for ticker in tickers:
                sql = text("""
                    SELECT timestamp, value as close
                    FROM indicator_values
                    WHERE symbol = :ticker AND DATE(timestamp) <= DATE(:target_date)
                    ORDER BY timestamp DESC LIMIT 2
                """)
                rows = conn.execute(sql, {"ticker": ticker, "target_date": target_date}).fetchall()
                if len(rows) >= 2:
                    close_hoje = float(rows[0].close) if rows[0].close is not None else None
                    close_ontem = float(rows[1].close) if rows[1].close is not None else None

                    if close_hoje is not None and close_ontem is not None and close_ontem != 0:
                        variacao = ((close_hoje - close_ontem) / close_ontem) * 100.0
                        if abs(variacao) > 2.0:
                            candidates.append({
                                "ticker": ticker,
                                "close_hoje": close_hoje,
                                "close_ontem": close_ontem,
                                "variacao": round(variacao, 2),
                                "abs_variacao": round(abs(variacao), 2)
                            })
    except Exception as e:
        logging.error(f"❌ Erro durante o scan mecánico de candidatos: {e}")

    # Limite de segurança: ordenar por maior magnitude e selecionar no máximo top 10
    candidates.sort(key=lambda x: x["abs_variacao"], reverse=True)
    top_candidates = candidates[:10]
    logging.info(f"📊 [TAREFA 1] {len(candidates)} instrumentos com |variação| > 2%. Selecionados os {len(top_candidates)} de maior magnitude.")
    return top_candidates


# -----------------------------------------------------------------------------
# TAREFA 1b — POSIÇÕES ABERTAS (MECÂNICO, SEM LLM)
# -----------------------------------------------------------------------------
def task1b_open_positions(target_date: str) -> List[Dict[str, Any]]:
    """
    Consulta o Diário de Bordo Estratégico no Notion para posições com Estado = 'Aberta'.
    Calcula se a variação do dia foi a favor ou contra a posição.
    Trata lista vazia sem erros.
    """
    if not NOTION_TOKEN or not NOTION_DIARIO_BORDO_DB_ID:
        logging.info("ℹ️ NOTION_DIARIO_BORDO_DB_ID não configurado. Assumindo lista vazia de posições abertas.")
        return []

    url = f"https://api.notion.com/v1/databases/{NOTION_DIARIO_BORDO_DB_ID}/query"
    payload = {
        "filter": {
            "property": "Estado",
            "select": {"equals": "Aberta"}
        }
    }

    positions = []
    try:
        res = requests.post(url, headers=NOTION_HEADERS, json=payload, timeout=10)
        if res.status_code == 200:
            results = res.json().get("results", [])
            for p in results:
                props = p.get("properties", {})
                ativo = ""
                direcao = "Compra"

                for p_name, p_val in props.items():
                    if "Ativo" in p_name or "Instrumento" in p_name:
                        if p_val.get("type") == "rich_text" and p_val.get("rich_text"):
                            ativo = p_val["rich_text"][0].get("plain_text", "")
                        elif p_val.get("type") == "title" and p_val.get("title"):
                            ativo = p_val["title"][0].get("plain_text", "")
                    elif "Direção" in p_name or "Tipo" in p_name:
                        if p_val.get("type") == "select" and p_val.get("select"):
                            direcao = p_val["select"].get("name", "Compra")

                if ativo:
                    # Obter variação hoje no MySQL
                    var_dia = 0.0
                    with engine.connect() as conn:
                        sql = text("""
                            SELECT value as close FROM indicator_values
                            WHERE symbol = :ticker AND DATE(timestamp) <= DATE(:target_date)
                            ORDER BY timestamp DESC LIMIT 2
                        """)
                        r = conn.execute(sql, {"ticker": ativo, "target_date": target_date}).fetchall()
                        if len(r) >= 2 and r[1].close:
                            c0, c1 = float(r[0].close), float(r[1].close)
                            var_dia = round(((c0 - c1) / c1) * 100.0, 2)

                    a_favor = (var_dia > 0 and "compra" in direcao.lower()) or (var_dia < 0 and "venda" in direcao.lower())
                    positions.append({
                        "ativo": ativo,
                        "direcao": direcao,
                        "variacao_hoje": var_dia,
                        "a_favor": a_favor
                    })
    except Exception as e:
        logging.warning(f"⚠️ Erro ao consultar posições abertas no Notion: {e}")

    logging.info(f"💼 [TAREFA 1b] {len(positions)} posições abertas encontradas no Diário de Bordo.")
    return positions


# -----------------------------------------------------------------------------
# TAREFA 1c — CALENDÁRIO À FRENTE (MECÂNICO, SEM LLM)
# -----------------------------------------------------------------------------
def task1c_future_calendar(target_date: str) -> List[Dict[str, Any]]:
    """
    Consulta no Notion Calendário Económico os eventos com Importância = 'Alta'
    agendados entre amanhã e os próximos 7 dias que continuem sem valor Real.
    """
    if not NOTION_TOKEN or not NOTION_CALENDAR_DB_ID:
        return []

    try:
        dt_target = datetime.strptime(target_date, "%Y-%m-%d")
        dt_start = (dt_target + timedelta(days=1)).strftime("%Y-%m-%d")
        dt_end = (dt_target + timedelta(days=7)).strftime("%Y-%m-%d")

        url = f"https://api.notion.com/v1/databases/{NOTION_CALENDAR_DB_ID}/query"
        payload = {
            "filter": {
                "and": [
                    {
                        "property": "Importância",
                        "select": {"equals": "Alta"}
                    },
                    {
                        "property": "Data",
                        "date": {"on_or_after": dt_start}
                    },
                    {
                        "property": "Data",
                        "date": {"on_or_before": dt_end}
                    }
                ]
            }
        }

        res = requests.post(url, headers=NOTION_HEADERS, json=payload, timeout=10)
        events = []
        if res.status_code == 200:
            results = res.json().get("results", [])
            for page in results:
                props = page.get("properties", {})
                
                # Verificar se Real está vazio
                real_val = None
                if "Real" in props:
                    p_real = props["Real"]
                    if p_real.get("type") == "number":
                        real_val = p_real.get("number")
                    elif p_real.get("type") == "rich_text" and p_real.get("rich_text"):
                        real_val = p_real["rich_text"][0].get("plain_text")

                if real_val is not None:
                    continue  # Já saiu, ignorar

                evento_nome = ""
                if "Evento" in props and props["Evento"].get("title"):
                    evento_nome = props["Evento"]["title"][0].get("plain_text", "")

                data_str = ""
                if "Data" in props and props["Data"].get("date"):
                    data_str = props["Data"]["date"].get("start", "")

                mecanismo = ""
                if "Mecanismo Aplicável" in props and props["Mecanismo Aplicável"].get("rich_text"):
                    mecanismo = props["Mecanismo Aplicável"]["rich_text"][0].get("plain_text", "")

                previsao = ""
                if "Previsão Condicional" in props and props["Previsão Condicional"].get("rich_text"):
                    previsao = props["Previsão Condicional"]["rich_text"][0].get("plain_text", "")

                if evento_nome:
                    events.append({
                        "evento": evento_nome,
                        "data": data_str[:10],
                        "mecanismo": mecanismo,
                        "previsao": previsao
                    })

        logging.info(f"📅 [TAREFA 1c] {len(events)} eventos de Alto Impacto agendados para os próximos 7 dias.")
        return events
    except Exception as e:
        logging.warning(f"⚠️ Erro ao consultar calendário futuro: {e}")
        return []


TICKER_ALIASES: Dict[str, List[str]] = {
    "CL=F": ["CL=F", "WTI", "Petróleo", "Oil", "Crude"],
    "BZ=F": ["BZ=F", "Brent", "Petróleo", "Oil"],
    "DX-Y.NYB": ["DX-Y.NYB", "DXY", "Dólar", "USD", "Dollar"],
    "GC=F": ["GC=F", "Ouro", "Gold"],
    "^TNX": ["^TNX", "Yield", "Yields", "10Y", "Juros", "Treasury"],
    "^TYX": ["^TYX", "Yield", "Yields", "30Y", "Juros"],
    "^GSPC": ["^GSPC", "S&P", "SP500", "Ações", "Stock", "EUA"],
    "^NDX": ["^NDX", "Nasdaq", "NDX", "Tech"],
    "EURUSD=X": ["EURUSD=X", "EUR/USD", "Euro", "EUR"],
    "^VIX": ["^VIX", "VIX", "Volatilidade"],
    "^MOVE": ["^MOVE", "MOVE", "Volatilidade"],
    "HG=F": ["HG=F", "Cobre", "Copper"],
    "NG=F": ["NG=F", "Gás Natural", "Natural Gas"],
    "CC=F": ["CC=F", "Cacau", "Cocoa"],
    "NVDA": ["NVDA", "Nvidia"],
    "AAPL": ["AAPL", "Apple"],
    "TSLA": ["TSLA", "Tesla"],
    "MSFT": ["MSFT", "Microsoft"],
    "GOOGL": ["GOOGL", "Google", "Alphabet"],
    "AMZN": ["AMZN", "Amazon"],
    "META": ["META", "Meta", "Facebook"],
}


# -----------------------------------------------------------------------------
# TAREFA 2 — CLASSIFICAR CADA CANDIDATO (CALENDÁRIO VS. NOTÍCIAS)
# -----------------------------------------------------------------------------
def task2_classify_candidates(candidates: List[Dict[str, Any]], target_date: str) -> List[Dict[str, Any]]:
    """
    Para cada candidato com variação > 2%, verifica se existe evento de calendário de hoje que o explique.
    Usa um mapa de aliases (TICKER_ALIASES) para cruzar símbolos técnicos (ex: CL=F) com termos do Notion (ex: Petróleo, WTI).
    Se existir -> fonte = 'calendario'. Caso contrário -> fonte = 'noticias' (pesquisa no Feed de Notícias).
    """
    classified = []

    for c in candidates:
        ticker = c["ticker"]
        c_item = dict(c)
        aliases = TICKER_ALIASES.get(ticker, [ticker])
        if ticker not in aliases:
            aliases.append(ticker)

        # 1. Verificar Calendário Económico hoje usando OR-filter de aliases (ex: CL=F, WTI, Petróleo)
        found_in_calendar = False
        if NOTION_TOKEN and NOTION_CALENDAR_DB_ID:
            try:
                url = f"https://api.notion.com/v1/databases/{NOTION_CALENDAR_DB_ID}/query"
                or_calendar_filters = [{"property": "Ativo Relacionado", "rich_text": {"contains": alias}} for alias in aliases]
                payload = {
                    "filter": {
                        "and": [
                            {"property": "Data", "date": {"equals": target_date}},
                            {"or": or_calendar_filters}
                        ]
                    }
                }
                res = requests.post(url, headers=NOTION_HEADERS, json=payload, timeout=10)
                if res.status_code == 200:
                    results = res.json().get("results", [])
                    if results:
                        props = results[0].get("properties", {})
                        c_item["fonte"] = "calendario"
                        c_item["mecanismo"] = props.get("Mecanismo Aplicável", {}).get("rich_text", [{}])[0].get("plain_text", "") if props.get("Mecanismo Aplicável", {}).get("rich_text") else ""
                        c_item["previsao_original"] = props.get("Previsão Condicional", {}).get("rich_text", [{}])[0].get("plain_text", "") if props.get("Previsão Condicional", {}).get("rich_text") else ""
                        c_item["validacao"] = props.get("Validação Pós-Evento", {}).get("select", {}).get("name", "") if props.get("Validação Pós-Evento", {}).get("select") else ""
                        found_in_calendar = True
            except Exception as e:
                logging.warning(f"⚠️ Erro ao verificar calendário para {ticker}: {e}")

        # 2. Se não encontrou no calendário, procurar no Feed de Notícias com os aliases do ticker
        if not found_in_calendar:
            c_item["fonte"] = "noticias"
            headlines = []
            if NOTION_TOKEN and NOTION_FEED_NOTICIAS_DB_ID:
                try:
                    url = f"https://api.notion.com/v1/databases/{NOTION_FEED_NOTICIAS_DB_ID}/query"
                    or_news_filters = [{"property": "Ticker(s) Relacionado(s)", "rich_text": {"contains": alias}} for alias in aliases]
                    payload = {
                        "filter": {
                            "or": or_news_filters
                        },
                        "page_size": 3
                    }
                    res = requests.post(url, headers=NOTION_HEADERS, json=payload, timeout=10)
                    if res.status_code == 200:
                        for p in res.json().get("results", []):
                            props = p.get("properties", {})
                            for p_name, p_val in props.items():
                                if p_val.get("type") == "title" and p_val.get("title"):
                                    headlines.append(p_val["title"][0].get("plain_text", ""))
                except Exception as e:
                    logging.warning(f"⚠️ Erro ao consultar feed de notícias para {ticker}: {e}")

            c_item["manchetes"] = headlines

        classified.append(c_item)

    return classified


# -----------------------------------------------------------------------------
# AUXILIAR: LER CAMADA 0 E CAMADA 1
# -----------------------------------------------------------------------------
def fetch_layer0_thesis() -> str:
    """Lê a Tese Estrutural Ativa da Camada 0."""
    if not NOTION_TOKEN or not NOTION_CAMADA0_DB_ID:
        return "Tese Estrutural Padrão"
    try:
        url = f"https://api.notion.com/v1/databases/{NOTION_CAMADA0_DB_ID}/query"
        res = requests.post(url, headers=NOTION_HEADERS, json={"page_size": 1}, timeout=10)
        if res.status_code == 200 and res.json().get("results"):
            props = res.json()["results"][0].get("properties", {})
            for p_name, p_val in props.items():
                if "Tese" in p_name or "Estrutural" in p_name:
                    if p_val.get("type") == "title" and p_val.get("title"):
                        return p_val["title"][0].get("plain_text", "")
                    elif p_val.get("type") == "rich_text" and p_val.get("rich_text"):
                        return p_val["rich_text"][0].get("plain_text", "")
    except Exception as e:
        logging.warning(f"⚠️ Falha ao ler Camada 0: {e}")
    return "Tese Estrutural Padrão"


def fetch_layer1_regime(target_date: str) -> str:
    """Lê o Regime de Risco da Camada 1 para a data."""
    if not NOTION_TOKEN or not NOTION_CLAUDE_REGIME_DB_ID:
        return "Sem Dados"
    try:
        url = f"https://api.notion.com/v1/databases/{NOTION_CLAUDE_REGIME_DB_ID}/query"
        payload = {"filter": {"property": "Data", "title": {"contains": target_date}}}
        res = requests.post(url, headers=NOTION_HEADERS, json=payload, timeout=10)
        if res.status_code == 200 and res.json().get("results"):
            props = res.json()["results"][0].get("properties", {})
            for p_name in ["Regime", "Regime Consolidado"]:
                if p_name in props:
                    p_val = props[p_name]
                    if p_val.get("type") == "select" and p_val.get("select"):
                        return p_val["select"].get("name", "Neutro")
                    elif p_val.get("type") == "rich_text" and p_val.get("rich_text"):
                        return p_val["rich_text"][0].get("plain_text", "Neutro")
    except Exception as e:
        logging.warning(f"⚠️ Falha ao ler Camada 1: {e}")
    return "Misto/Transição"


# -----------------------------------------------------------------------------
# TAREFA 3 — SÍNTESE FINAL (CLAUDE API, 1 CHAMADA POR DIA)
# -----------------------------------------------------------------------------
def task3_synthesize_briefing(
    target_date: str,
    classified_candidates: List[Dict[str, Any]],
    open_positions: List[Dict[str, Any]],
    future_events: List[Dict[str, Any]],
    dry_run: bool = False
) -> Optional[Dict[str, Any]]:
    """
    Executa a chamada única diária à Claude API para gerar o resumo executivo.
    Se dry_run=True, regressa o dicionário parsed sem escrever no Notion.
    """
    logging.info(f"🧠 [TAREFA 3] Gerando Síntese Executiva via Claude API para a data {target_date} (dry_run={dry_run})...")

    layer0_thesis = fetch_layer0_thesis()
    layer1_regime = fetch_layer1_regime(target_date)

    # Prompt do Sistema Estrito
    system_prompt = """Tarefa única: escrever um briefing diário completo do mercado, em linguagem clara e acessível — este texto vai ser lido diretamente por uma pessoa, não só processado por outro sistema.

Regras de linguagem:
- Explicar termos técnicos na primeira vez que aparecem (ex: "CPI (índice de inflação nos EUA)", não só "CPI")
- Preferir frases curtas e diretas a frases longas com várias subordinadas
- Quando um mecanismo complexo do Mapa de Transmissão for citado, traduzir para linguagem simples do que significa na prática para os nossos ativos, não repetir a formulação técnica do Mapa

Regras de conteúdo, alinhadas com o Estatuto do Trader deste sistema:
- Nunca decidir nem recomendar ação de trade — só descrever o que aconteceu e o que está agendado, não é permitido sugerir "comprar" ou "vender"
- Nunca inventar causa/catalisador sem fonte — se não houver notícia nem evento de calendário que explique um movimento, escrever explicitamente "sem catalisador identificado"
- Nunca assumir dados que não foram fornecidos neste prompt — usar só o que está nos inputs, não completar com conhecimento geral sobre os ativos

Para candidatos do scan com fonte="calendario": usa diretamente o mecanismo e a previsão já escritos, mas traduz para linguagem simples — não copies a formulação técnica do Mapa. Inclui se a previsão de hoje bateu certo (validacao: Acertou/Errou/Parcial) ou ainda não foi validada, em linguagem simples ("a nossa previsão de manhã estava certa" / "o mercado reagiu ao contrário do esperado").

Para candidatos com fonte="noticias": procura explicação nas manchetes fornecidas. Se não houver notícia clara, escreve "sem catalisador identificado" — nunca inventes.

Para os eventos futuros (próximos 7 dias): se já tiverem Mecanismo Aplicável/Previsão Condicional escritos, resume-os em linguagem simples. Se ainda não tiverem, lista-os só pelo nome e data.

Para as posições abertas: descreve de forma simples se o dia foi bom ou mau para cada posição, usando só os dados fornecidos. Se não houver posições abertas, dizer isso claramente.

Liga a Tese Estrutural Ativa (Camada 0) ao que aconteceu hoje, em linguagem simples — o que a tese diz, e se o dia de hoje a confirma ou não.

Responde em JSON estrito, exatamente este formato, nada mais:
{
  "cenario_macro": "1-2 frases, simples, sobre a tendência de fundo atual",
  "resumo_executivo": "3-4 frases, linguagem corrida e acessível, descritiva não prescritiva",
  "instrumentos_afetados": "lista curta, linguagem simples, com o porquê de cada um",
  "catalisadores_identificados": "em linguagem simples, cita a fonte",
  "posicoes_abertas_status": "linguagem simples, ou 'nenhuma posição aberta'",
  "previsao_multidia": "linguagem simples, o que vigiar nos próximos dias e porquê",
  "fontes_noticias_usadas": "só os títulos usados no caminho de notícias"
}"""

    user_prompt = f"""Dados para o Resumo Executivo ({target_date}):
- Regime de Risco (Camada 1): {layer1_regime}
- Tese Estrutural Ativa (Camada 0): "{layer0_thesis}"

Candidatos do Scan (>2% variação hoje):
{json.dumps(classified_candidates, ensure_ascii=False, indent=2)}

Posições Abertas (Diário de Bordo):
{json.dumps(open_positions, ensure_ascii=False, indent=2) if open_positions else "Nenhuma posição aberta no momento."}

Eventos Futuros de Alto Impacto (Próximos 7 Dias):
{json.dumps(future_events, ensure_ascii=False, indent=2) if future_events else "Nenhum evento de alto impacto agendado."}
"""

    raw_response = call_claude_api(system_prompt, user_prompt, max_tokens=3000)
    if not raw_response:
        logging.error("❌ Falha ao obter resposta da Claude API.")
        return None

    # Parse JSON
    try:
        clean_json = raw_response.strip()
        if clean_json.startswith("```json"):
            clean_json = clean_json.split("```json", 1)[1].rsplit("```", 1)[0].strip()
        elif clean_json.startswith("```"):
            clean_json = clean_json.split("```", 1)[1].rsplit("```", 1)[0].strip()

        parsed = json.loads(clean_json)
    except Exception as parse_err:
        logging.error(f"❌ Erro no parse do JSON da síntese: {parse_err}. Resposta bruta:\n{raw_response}")
        return None

    if dry_run:
        logging.info("ℹ️ [DRY RUN ATIVADO] Síntese concluída com sucesso. Retornando JSON sem gravar no Notion.")
        return parsed

    # Gravar no Notion se não for dry_run
    success = write_executive_summary_to_notion(target_date, parsed)
    return parsed if success else None


def write_executive_summary_to_notion(target_date: str, data: Dict[str, Any]) -> bool:
    """
    Grava/Atualiza os campos do Resumo Executivo na tabela do Notion.
    Se a página da data já existir (ex: criada pela Camada 1), faz PATCH/UPDATE.
    Se não existir, faz POST (criação).
    """
    if not NOTION_TOKEN or not NOTION_RESUMO_EXECUTIVO_DB_ID:
        logging.error("❌ NOTION_TOKEN ou NOTION_RESUMO_EXECUTIVO_DB_ID não configurados.")
        return False

    title_text = f"Resumo — {target_date}"
    from app.services.notion_calendar_sync_service import _build_rich_text

    props = {
        "Resumo Executivo": {"rich_text": _build_rich_text(str(data.get("resumo_executivo", "")))},
        "Instrumentos Afetados": {"rich_text": _build_rich_text(str(data.get("instrumentos_afetados", "")))},
        "Catalisadores Identificados": {"rich_text": _build_rich_text(str(data.get("catalisadores_identificados", "")))},
        "Fontes de Notícias Usadas": {"rich_text": _build_rich_text(str(data.get("fontes_noticias_usadas", "")))},
        "Cenário Macro": {"rich_text": _build_rich_text(str(data.get("cenario_macro", "")))},
        "Calendário Próximos Dias": {"rich_text": _build_rich_text(str(data.get("previsao_multidia", "")))},
        "Posições Abertas — Estado": {"rich_text": _build_rich_text(str(data.get("posicoes_abertas_status", "")))},
        "Previsão Multi-Dia": {"rich_text": _build_rich_text(str(data.get("previsao_multidia", "")))}
    }

    # 1. Procurar se já existe página para a data nesta database
    existing_page_id = None
    try:
        url_query = f"https://api.notion.com/v1/databases/{NOTION_RESUMO_EXECUTIVO_DB_ID}/query"
        payload_query = {
            "filter": {
                "or": [
                    {"property": "Data + Título", "title": {"contains": target_date}},
                    {"property": "Data", "date": {"equals": target_date}}
                ]
            }
        }
        res_query = requests.post(url_query, headers=NOTION_HEADERS, json=payload_query, timeout=10)
        if res_query.status_code == 200:
            results = res_query.json().get("results", [])
            if results:
                existing_page_id = results[0]["id"]
    except Exception as query_err:
        logging.warning(f"⚠️ Erro ao procurar página existente da data {target_date}: {query_err}")

    # 2. Se já existe -> PATCH; Se não existe -> POST
    success = False
    if existing_page_id:
        url_patch = f"https://api.notion.com/v1/pages/{existing_page_id}"
        payload_patch = {"properties": props}
        try:
            res = requests.patch(url_patch, headers=NOTION_HEADERS, json=payload_patch, timeout=10)
            if res.status_code == 200:
                logging.info(f"🎉 [NOTION SUCESSO] Página existente '{target_date}' atualizada com sucesso no Notion!")
                success = True
            else:
                logging.error(f"❌ Erro HTTP {res.status_code} ao atualizar página Notion: {res.text}")
        except Exception as e:
            logging.error(f"❌ Exceção ao atualizar página no Notion: {e}")
    else:
        props["Data + Título"] = {"title": [{"text": {"content": title_text}}]}
        props["Data"] = {"date": {"start": target_date}}
        url_post = "https://api.notion.com/v1/pages"
        payload_post = {
            "parent": {"database_id": NOTION_RESUMO_EXECUTIVO_DB_ID},
            "properties": props
        }
        try:
            res = requests.post(url_post, headers=NOTION_HEADERS, json=payload_post, timeout=10)
            if res.status_code in [200, 201]:
                logging.info(f"🎉 [NOTION SUCESSO] Nova linha '{title_text}' criada com sucesso na tabela Resumo Executivo Diário!")
                success = True
            else:
                logging.error(f"❌ Erro HTTP {res.status_code} ao criar linha no Notion: {res.text}")
        except Exception as e:
            logging.error(f"❌ Exceção ao criar linha no Notion: {e}")

    if success:
        try:
            from app.services.alert_service import send_executive_summary_email
            send_executive_summary_email(data, target_date)
        except Exception as mail_err:
            logging.warning(f"⚠️ Aviso ao enviar e-mail do resumo executivo: {mail_err}")

    return success
