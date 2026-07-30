# backend/app/services/notion_calendar_sync_service.py
"""
Módulo: notion_calendar_sync_service.py
Sincroniza as tabelas MySQL `economic_calendar` e `corporate_earnings_calendar`
(já validadas pelo Data Quality Engine) para a tabela Notion
`Calendário Económico & Resultados`.

Regras de design:
- Upsert usa `ID Fonte MySQL` + `Tabela Origem` como chave — nunca comparação de texto.
- Filtro anti-mock: nunca sincroniza linhas com source_provider MOCK_DATA_FALLBACK /
  UNVERIFIED_DEMO / SYSTEM_FEED.
- Campo `Impacto nos Nossos Ativos` nunca escrito pelo cron (reservado à Camada 2).
- Tipo `Evento de Produto` nunca gerado pelo cron (exclusivamente manual).
- As 6 linhas manuais de 20/07 (sem `ID Fonte MySQL`) não são tocadas.
"""

import os
import logging
import re
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional

import requests
from sqlalchemy import text

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# ─── Configuração ──────────────────────────────────────────────────────────────
NOTION_TOKEN = os.getenv("NOTION_TOKEN") or os.getenv("NOTION_API_KEY", "")
NOTION_CALENDAR_DB_ID = os.getenv("NOTION_CALENDAR_DB_ID", "")

NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28"
}

# Providers que nunca devem ser sincronizados (dados não verificados / mock)
BLOCKED_PROVIDERS = {"MOCK_DATA_FALLBACK", "UNVERIFIED_DEMO", "SYSTEM_FEED"}

# Mapeamento impact_level MySQL → Importância Notion
IMPACT_MAP = {
    "HIGH":   "Alta",
    "MEDIUM": "Média",
    "LOW":    "Baixa",
}

# Mapeamento time_of_day MySQL → Momento Notion
MOMENT_MAP = {
    "BEFORE_MARKET": "Before Market",
    "BMO":           "Before Market",
    "AFTER_MARKET":  "After Market",
    "AMC":           "After Market",
}

# Palavras-chave para inferir Tipo a partir do nome do evento macro
MACRO_KEYWORDS = re.compile(
    r"CPI|PPI|PCE|PMI|GDP|NFP|Payroll|Rate|Interest|Inflation|Employment|"
    r"Jobless|Retail|Consumer|ISM|ZEW|IFO|LPR|Inventories|Permits|Sales|"
    r"Confidence|Unemployment|Sentiment",
    re.IGNORECASE
)
GEO_KEYWORDS = re.compile(r"Geopolit|Sanction|War|Conflict|Treaty", re.IGNORECASE)


def _infer_event_type(event_name: str) -> str:
    """
    Infere o tipo do evento macro a partir do nome.
    Nunca gera 'Evento de Produto' — esse tipo é exclusivamente manual.
    """
    if GEO_KEYWORDS.search(event_name):
        return "Geopolítico"
    if MACRO_KEYWORDS.search(event_name):
        return "Macro (CPI/PMI/Juros)"
    return "Outro"


def _format_value(val, unit: str = "") -> Optional[str]:
    """Formata um valor numérico com unidade para texto legível (ex: '2.5 %')."""
    if val is None:
        return None
    unit_str = f" {unit.strip()}" if unit and unit.strip() else ""
    try:
        return f"{float(val):.2f}{unit_str}"
    except (TypeError, ValueError):
        return str(val)


# ─── MySQL: Extração ───────────────────────────────────────────────────────────

def query_economic_calendar(backfill: bool = False) -> List[Dict[str, Any]]:
    """
    Extrai eventos da tabela `economic_calendar` já validados pelo Data Quality Engine.
    - Anti-mock: exclui linhas com source_provider em BLOCKED_PROVIDERS.
    - backfill=True → todo o histórico; False → últimos 30 dias + próximos 90 dias.
    """
    try:
        from app.database import engine
        with engine.connect() as conn:
            if backfill:
                date_filter = ""
                params: Dict[str, Any] = {}
            else:
                date_filter = "AND event_timestamp >= :from_dt AND event_timestamp <= :to_dt"
                params = {
                    "from_dt": (datetime.utcnow() - timedelta(days=30)).strftime("%Y-%m-%d"),
                    "to_dt":   (datetime.utcnow() + timedelta(days=90)).strftime("%Y-%m-%d"),
                }

            sql = text(f"""
                SELECT id, event_name, country, currency, event_timestamp,
                       impact_level, actual_val, forecast_val, previous_val,
                       unit, source_provider
                FROM economic_calendar
                WHERE source_provider NOT IN ('MOCK_DATA_FALLBACK', 'UNVERIFIED_DEMO', 'SYSTEM_FEED')
                {date_filter}
                ORDER BY event_timestamp ASC
            """)
            rows = conn.execute(sql, params).fetchall()
            logging.info(f"📅 [economic_calendar] {len(rows)} eventos lidos do MySQL (backfill={backfill}).")
            return [dict(r._mapping) for r in rows]
    except Exception as e:
        logging.error(f"❌ Erro ao ler economic_calendar do MySQL: {e}")
        return []


def query_corporate_earnings(backfill: bool = False) -> List[Dict[str, Any]]:
    """
    Extrai registos da tabela `corporate_earnings_calendar` já validados.
    - Anti-mock aplicado.
    - backfill=True → histórico completo; False → últimos 30 dias + próximos 90 dias.
    """
    try:
        from app.database import engine
        with engine.connect() as conn:
            if backfill:
                date_filter = ""
                params: Dict[str, Any] = {}
            else:
                date_filter = "AND event_date >= :from_dt AND event_date <= :to_dt"
                params = {
                    "from_dt": (datetime.utcnow() - timedelta(days=30)).strftime("%Y-%m-%d"),
                    "to_dt":   (datetime.utcnow() + timedelta(days=90)).strftime("%Y-%m-%d"),
                }

            sql = text(f"""
                SELECT id, symbol, company_name, event_date, time_of_day,
                       eps_estimate, eps_actual, revenue_estimate, revenue_actual,
                       fiscal_period, source_provider
                FROM corporate_earnings_calendar
                WHERE source_provider NOT IN ('MOCK_DATA_FALLBACK', 'UNVERIFIED_DEMO', 'SYSTEM_FEED')
                {date_filter}
                ORDER BY event_date ASC
            """)
            rows = conn.execute(sql, params).fetchall()
            logging.info(f"🏢 [corporate_earnings_calendar] {len(rows)} earnings lidos do MySQL (backfill={backfill}).")
            return [dict(r._mapping) for r in rows]
    except Exception as e:
        logging.error(f"❌ Erro ao ler corporate_earnings_calendar do MySQL: {e}")
        return []


# ─── Notion: Upsert ───────────────────────────────────────────────────────────

def _notion_find_existing_page(mysql_id: int, tabela_origem: str) -> Optional[str]:
    """
    Procura uma página no Notion com `ID Fonte MySQL == mysql_id` AND `Tabela Origem == tabela_origem`.
    Devolve o page_id se encontrado, None caso contrário.
    """
    url = f"https://api.notion.com/v1/databases/{NOTION_CALENDAR_DB_ID}/query"
    payload = {
        "filter": {
            "and": [
                {"property": "ID Fonte MySQL", "number": {"equals": mysql_id}},
                {"property": "Tabela Origem",  "select": {"equals": tabela_origem}}
            ]
        },
        "page_size": 1
    }
    try:
        res = requests.post(url, headers=NOTION_HEADERS, json=payload, timeout=10)
        if res.status_code == 200:
            results = res.json().get("results", [])
            if results:
                return results[0]["id"]
    except Exception as e:
        logging.warning(f"⚠️ Erro ao procurar página Notion (id={mysql_id}, tabela={tabela_origem}): {e}")
    return None


def _build_rich_text(value: Optional[str]) -> list:
    if not value:
        return []
    return [{"text": {"content": str(value)[:2000]}}]


def _notion_create_page(properties: Dict[str, Any]) -> bool:
    """Cria uma nova página no Notion com todas as propriedades."""
    url = "https://api.notion.com/v1/pages"
    payload = {
        "parent": {"database_id": NOTION_CALENDAR_DB_ID},
        "properties": properties
    }
    try:
        res = requests.post(url, headers=NOTION_HEADERS, json=payload, timeout=10)
        if res.status_code in [200, 201]:
            return True
        logging.error(f"❌ Erro ao criar página Notion (HTTP {res.status_code}): {res.text[:300]}")
    except Exception as e:
        logging.error(f"❌ Exceção ao criar página Notion: {e}")
    return False


def _notion_update_page(page_id: str, properties: Dict[str, Any]) -> bool:
    """Atualiza apenas os campos 'Real' de uma página existente no Notion."""
    url = f"https://api.notion.com/v1/pages/{page_id}"
    try:
        res = requests.patch(url, headers=NOTION_HEADERS, json={"properties": properties}, timeout=10)
        if res.status_code in [200, 201]:
            return True
        logging.error(f"❌ Erro ao atualizar página Notion {page_id} (HTTP {res.status_code}): {res.text[:300]}")
    except Exception as e:
        logging.error(f"❌ Exceção ao atualizar página Notion {page_id}: {e}")
    return False


def upsert_economic_event(row: Dict[str, Any]) -> str:
    """
    Upsert de um evento macro (economic_calendar) no Notion.
    Devolve 'created', 'updated' ou 'error'.
    """
    mysql_id = row["id"]
    tabela_origem = "economic_calendar"

    existing_page_id = _notion_find_existing_page(mysql_id, tabela_origem)

    # Campos "Real" que podem mudar após o evento acontecer
    real_text = _format_value(row.get("actual_val"), row.get("unit", ""))
    update_props = {}
    if real_text:
        update_props["Real"] = {"rich_text": _build_rich_text(real_text)}

    if existing_page_id:
        # Só atualiza campos "Real" — nunca reescreve Evento, Data, Tipo, etc.
        if update_props:
            success = _notion_update_page(existing_page_id, update_props)
            return "updated" if success else "error"
        return "skipped"  # Sem dados novos para actualizar

    # Criar nova página completa
    event_date = row.get("event_timestamp")
    date_str = str(event_date)[:10] if event_date else None  # YYYY-MM-DD

    full_props: Dict[str, Any] = {
        "Evento": {
            "title": [{"text": {"content": str(row.get("event_name", ""))[:255]}}]
        },
        "ID Fonte MySQL": {"number": mysql_id},
        "Tabela Origem":  {"select": {"name": tabela_origem}},
        "Fonte de Registo": {"select": {"name": "Automático (Cron)"}},
        "Tipo": {"select": {"name": _infer_event_type(str(row.get("event_name", "")))}},
        "Importância": {"select": {"name": IMPACT_MAP.get(str(row.get("impact_level", "")).upper(), "Média")}},
        "País/Empresa": {"rich_text": _build_rich_text(str(row.get("country") or row.get("country_or_entity") or ""))},
    }

    if date_str:
        full_props["Data"] = {"date": {"start": date_str}}

    forecast_text = _format_value(row.get("forecast_val"), row.get("unit", ""))
    if forecast_text:
        full_props["Projetado"] = {"rich_text": _build_rich_text(forecast_text)}

    if real_text:
        full_props["Real"] = {"rich_text": _build_rich_text(real_text)}

    success = _notion_create_page(full_props)
    return "created" if success else "error"


def upsert_earnings_event(row: Dict[str, Any]) -> str:
    """
    Upsert de um earnings corporativo (corporate_earnings_calendar) no Notion.
    Devolve 'created', 'updated' ou 'error'.
    """
    mysql_id = row["id"]
    tabela_origem = "corporate_earnings_calendar"

    existing_page_id = _notion_find_existing_page(mysql_id, tabela_origem)

    # Campos "Real" (só actualizados, nunca na criação se forem None)
    update_props = {}
    if row.get("eps_actual") is not None:
        update_props["EPS Real"] = {"number": float(row["eps_actual"])}
    if row.get("revenue_actual") is not None:
        update_props["Receita Real"] = {"number": float(row["revenue_actual"])}

    if existing_page_id:
        if update_props:
            success = _notion_update_page(existing_page_id, update_props)
            return "updated" if success else "error"
        return "skipped"

    # Criar nova página completa
    company = str(row.get("company_name") or row.get("symbol") or "")
    period = str(row.get("fiscal_period") or "")
    evento_title = f"{company} {period}".strip() if period else company

    event_date = row.get("event_date")
    date_str = str(event_date)[:10] if event_date else None

    time_raw = str(row.get("time_of_day") or "").upper()
    momento = MOMENT_MAP.get(time_raw, "N/A")

    full_props: Dict[str, Any] = {
        "Evento": {
            "title": [{"text": {"content": evento_title[:255]}}]
        },
        "ID Fonte MySQL": {"number": mysql_id},
        "Tabela Origem":  {"select": {"name": tabela_origem}},
        "Fonte de Registo": {"select": {"name": "Automático (Cron)"}},
        "Tipo": {"select": {"name": "Resultados Empresa"}},
        "Importância": {"select": {"name": IMPACT_MAP.get(str(row.get("impact_level", "")).upper(), "Alta")}},
        "País/Empresa": {"rich_text": _build_rich_text(str(row.get("symbol") or ""))},
        "Ativo Relacionado": {"rich_text": _build_rich_text(str(row.get("symbol") or ""))},
        "Momento": {"select": {"name": momento}},
    }

    if date_str:
        full_props["Data"] = {"date": {"start": date_str}}

    if row.get("eps_estimate") is not None:
        full_props["EPS Estimado"] = {"number": float(row["eps_estimate"])}
    if row.get("eps_actual") is not None:
        full_props["EPS Real"] = {"number": float(row["eps_actual"])}
    if row.get("revenue_estimate") is not None:
        full_props["Receita Estimada"] = {"number": float(row["revenue_estimate"])}
    if row.get("revenue_actual") is not None:
        full_props["Receita Real"] = {"number": float(row["revenue_actual"])}

    success = _notion_create_page(full_props)
    return "created" if success else "error"


# ─── Detecção de Evento Alta para Frequência Dinâmica ─────────────────────────

def detect_high_importance_today() -> bool:
    """
    Verifica se existe pelo menos 1 evento com Importância Alta agendado para hoje.
    Usado por outros pipelines para decidir a frequência de sync.
    """
    today = datetime.utcnow().strftime("%Y-%m-%d")
    url = f"https://api.notion.com/v1/databases/{NOTION_CALENDAR_DB_ID}/query"
    payload = {
        "filter": {
            "and": [
                {"property": "Importância", "select": {"equals": "Alta"}},
                {"property": "Data", "date": {"equals": today}}
            ]
        },
        "page_size": 1
    }
    try:
        res = requests.post(url, headers=NOTION_HEADERS, json=payload, timeout=10)
        if res.status_code == 200:
            has_high = len(res.json().get("results", [])) > 0
            logging.info(f"🔔 Evento Alta hoje ({today}): {'SIM — modo acelerado' if has_high else 'Não — modo EOD'}")
            return has_high
    except Exception as e:
        logging.warning(f"⚠️ Não foi possível verificar eventos Alta de hoje: {e}")
    return False


# ─── Pipeline Principal ────────────────────────────────────────────────────────

def run_calendar_sync_pipeline(backfill: bool = False) -> None:
    """
    Pipeline principal: MySQL → Notion (Calendário Económico & Resultados).

    Args:
        backfill: Se True, sincroniza TODO o histórico já validado no MySQL.
                  Se False (default), apenas os últimos 30 dias + próximos 90 dias.
    """
    if not NOTION_TOKEN:
        logging.error("❌ NOTION_TOKEN não configurado. Sync abortado.")
        return

    if not NOTION_CALENDAR_DB_ID:
        logging.error("❌ NOTION_CALENDAR_DB_ID não configurado. Sync abortado.")
        return

    mode = "BACKFILL COMPLETO" if backfill else "INCREMENTAL (30d passados + 90d futuros)"
    logging.info(f"🗓️ Iniciando Sync Calendário Económico & Resultados [{mode}]...")

    stats = {"eco_created": 0, "eco_updated": 0, "eco_skipped": 0, "eco_error": 0,
             "earn_created": 0, "earn_updated": 0, "earn_skipped": 0, "earn_error": 0}

    # ── Fase A: economic_calendar ──────────────────────────────────────────────
    eco_rows = query_economic_calendar(backfill=backfill)
    logging.info(f"📤 Sincronizando {len(eco_rows)} eventos macro para o Notion...")

    for row in eco_rows:
        result = upsert_economic_event(row)
        stats[f"eco_{result}"] = stats.get(f"eco_{result}", 0) + 1

    logging.info(
        f"✅ [economic_calendar] Criados: {stats['eco_created']} | "
        f"Atualizados: {stats['eco_updated']} | "
        f"Sem mudança: {stats['eco_skipped']} | "
        f"Erros: {stats['eco_error']}"
    )

    # ── Fase B: corporate_earnings_calendar ────────────────────────────────────
    earn_rows = query_corporate_earnings(backfill=backfill)
    logging.info(f"📤 Sincronizando {len(earn_rows)} earnings para o Notion...")

    for row in earn_rows:
        result = upsert_earnings_event(row)
        stats[f"earn_{result}"] = stats.get(f"earn_{result}", 0) + 1

    logging.info(
        f"✅ [corporate_earnings_calendar] Criados: {stats['earn_created']} | "
        f"Atualizados: {stats['earn_updated']} | "
        f"Sem mudança: {stats['earn_skipped']} | "
        f"Erros: {stats['earn_error']}"
    )

    total_ops = stats['eco_created'] + stats['eco_updated'] + stats['earn_created'] + stats['earn_updated']
    logging.info(f"🎉 Sync Calendário concluído! {total_ops} operações Notion realizadas.")
