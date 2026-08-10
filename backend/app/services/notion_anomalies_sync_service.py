# backend/app/services/notion_anomalies_sync_service.py
"""
Módulo: notion_anomalies_sync_service.py
Data: 10 de Agosto de 2026

Executa o Upsert incremental da tabela MySQL `data_anomalies_log` para a
Database Notion: 'Quarentena de Anomalias — Claude' (db54764c-a540-4416-aeb8-7205104a5ec7).

Mapeamento de Schema:
- Título Primary Notion: f"{id} - {symbol_or_event}" (ex: "70 - ^VIX")
- ID MySQL: id (Number)
- Símbolo/Evento: symbol_or_event (Rich Text / Select)
- Tipo de Anomalia: anomaly_type (Select / Rich Text)
- Valor Lido: raw_value (Rich Text)
- Limite Esperado: expected_range (Rich Text)
- Status na DB: status (Select / Rich Text)
- Ocorrências: occurrences (Number)
- Data Deteção: first_seen (Date)
- Última Vista: last_seen (Date)

CAMPOS PRESERVADOS NO NOTION (NUNCA MODIFICADOS PELO SYNC):
- Revisto por Claude
- Ação/Nota de Claude
"""

import sys
import os
import json
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional
import requests

sys.path.append('backend')
from sqlalchemy import text
from app.database import engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

NOTION_TOKEN = os.getenv("NOTION_TOKEN") or os.getenv("NOTION_API_KEY", "")
NOTION_ANOMALIES_DATABASE_ID = (
    os.getenv("ANOMALIA_DATABASE_ID", "").strip() or 
    os.getenv("NOTION_ANOMALIES_DATABASE_ID", "").strip() or 
    "db54764c-a540-4416-aeb8-7205104a5ec7"
)

NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28"
}

def get_notion_db_schema(db_id: str) -> Dict[str, str]:
    """Retorna dicionário {property_name: property_type} da database Notion."""
    url = f"https://api.notion.com/v1/databases/{db_id}"
    try:
        res = requests.get(url, headers=NOTION_HEADERS, timeout=10)
        if res.status_code == 200:
            properties = res.json().get("properties", {})
            return {p_name: p_data.get("type") for p_name, p_data in properties.items()}
        else:
            logging.warning(f"⚠️ Falha ao ler schema da db {db_id} (HTTP {res.status_code}): {res.text}")
    except Exception as e:
        logging.warning(f"Erro de conexão ao ler schema da db {db_id}: {e}")
    return {}

def format_notion_date(date_val: Any) -> Optional[str]:
    """Converte valores datetime/date para string ISO YYYY-MM-DD."""
    if not date_val:
        return None
    if isinstance(date_val, (datetime, str)):
        return str(date_val)[:10]
    try:
        return date_val.strftime("%Y-%m-%d")
    except Exception:
        return str(date_val)[:10]

def sync_anomalies_quarantine_to_notion() -> bool:
    """
    Executa o Upsert da tabela `data_anomalies_log` para o Notion.
    Sincroniza anomalias PENDING + anomalias atualizadas recentemente (últimos 14 dias).
    Preserva intactos os campos 'Revisto por Claude' e 'Ação/Nota de Claude'.
    """
    if not NOTION_TOKEN or not NOTION_ANOMALIES_DATABASE_ID:
        logging.error("❌ NOTION_TOKEN ou NOTION_ANOMALIES_DATABASE_ID não configurados.")
        return False

    logging.info(f"📤 Iniciando Sync da Quarentena de Anomalias para o Notion (DB: {NOTION_ANOMALIES_DATABASE_ID})...")
    
    schema = get_notion_db_schema(NOTION_ANOMALIES_DATABASE_ID)
    if not schema:
        logging.warning("⚠️ Schema do Notion não retornado. Continuando com mapeamento padrão de propriedades...")

    # Identificar colunas do Notion dinamicamente
    title_col = "ID MySQL"
    for p_name, p_type in schema.items():
        if p_type == "title":
            title_col = p_name
            break

    # 1. Carregar anomalias do MySQL (PENDING + anomalias ativas/recentes dos últimos 14 dias)
    anomalies = []
    try:
        with engine.connect() as conn:
            sql = text("""
                SELECT id, target_table, symbol_or_event, raw_value, expected_range, 
                       anomaly_type, anomaly_reason, status, occurrences, repeat_count, 
                       first_seen, last_seen, created_at
                FROM data_anomalies_log
                WHERE status = 'PENDING' 
                   OR last_seen >= CURRENT_TIMESTAMP - INTERVAL 14 DAY
                ORDER BY id ASC
            """)
            rows = conn.execute(sql).fetchall()
            for r in rows:
                anomalies.append(dict(r._mapping))
        logging.info(f"✅ {len(anomalies)} anomalias carregadas da DB MySQL para sincronização.")
    except Exception as e:
        logging.error(f"❌ Erro ao consultar data_anomalies_log na DB: {e}")
        return False

    url_query = f"https://api.notion.com/v1/databases/{NOTION_ANOMALIES_DATABASE_ID}/query"
    synced_count = 0

    for item in anomalies:
        mysql_id = int(item["id"])
        symbol_event = str(item.get("symbol_or_event") or "N/A")
        display_title = f"{mysql_id} - {symbol_event}"
        
        raw_val = str(item.get("raw_value") or "")
        exp_range = str(item.get("expected_range") or "")
        anomaly_type = str(item.get("anomaly_type") or "N/A")
        status_val = str(item.get("status") or "PENDING")
        occurrences_val = int(item.get("occurrences") or item.get("repeat_count") or 1)
        
        first_seen_str = format_notion_date(item.get("first_seen") or item.get("created_at"))
        last_seen_str = format_notion_date(item.get("last_seen") or item.get("created_at"))

        # Construir filtro de busca no Notion por ID MySQL (Number) ou por Título
        query_payload = {
            "filter": {
                "or": [
                    {"property": title_col, "title": {"starts_with": f"{mysql_id} - "}},
                    {"property": "ID MySQL", "number": {"equals": mysql_id}} if "ID MySQL" in schema and schema["ID MySQL"] == "number" else {"property": title_col, "title": {"equals": display_title}}
                ]
            }
        }

        try:
            res = requests.post(url_query, headers=NOTION_HEADERS, json=query_payload, timeout=10)
            existing_results = res.json().get("results", []) if res.status_code == 200 else []

            # Montar propriedades dinamicamente de acordo com os nomes/tipos existentes na DB Notion
            props: Dict[str, Any] = {}
            
            # Título primário (ex: "70 - ^VIX")
            props[title_col] = {"title": [{"text": {"content": display_title}}]}

            # ID MySQL (Propriedade Number se existir no Notion)
            if "ID MySQL" in schema:
                if schema["ID MySQL"] == "number":
                    props["ID MySQL"] = {"number": mysql_id}
                elif schema["ID MySQL"] == "rich_text":
                    props["ID MySQL"] = {"rich_text": [{"text": {"content": str(mysql_id)}}]}

            # Símbolo/Evento
            if "Símbolo/Evento" in schema:
                p_type = schema["Símbolo/Evento"]
                if p_type == "select":
                    props["Símbolo/Evento"] = {"select": {"name": symbol_event}}
                elif p_type in ["rich_text", "title"]:
                    props["Símbolo/Evento"] = {"rich_text": [{"text": {"content": symbol_event}}]}
            elif "Simbolo/Evento" in schema:
                props["Simbolo/Evento"] = {"rich_text": [{"text": {"content": symbol_event}}]}

            # Tipo de Anomalia
            if "Tipo de Anomalia" in schema:
                p_type = schema["Tipo de Anomalia"]
                if p_type == "select":
                    props["Tipo de Anomalia"] = {"select": {"name": anomaly_type}}
                else:
                    props["Tipo de Anomalia"] = {"rich_text": [{"text": {"content": anomaly_type}}]}

            # Valor Lido
            if "Valor Lido" in schema:
                p_type = schema["Valor Lido"]
                if p_type == "number":
                    try:
                        props["Valor Lido"] = {"number": float(raw_val)}
                    except Exception:
                        props["Valor Lido"] = {"rich_text": [{"text": {"content": raw_val}}]}
                else:
                    props["Valor Lido"] = {"rich_text": [{"text": {"content": raw_val}}]}

            # Limite Esperado
            if "Limite Esperado" in schema:
                props["Limite Esperado"] = {"rich_text": [{"text": {"content": exp_range}}]}

            # Status na DB
            if "Status na DB" in schema:
                p_type = schema["Status na DB"]
                if p_type == "select":
                    props["Status na DB"] = {"select": {"name": status_val}}
                else:
                    props["Status na DB"] = {"rich_text": [{"text": {"content": status_val}}]}

            # Ocorrências
            if "Ocorrências" in schema:
                props["Ocorrências"] = {"number": occurrences_val}
            elif "Ocorrencias" in schema:
                props["Ocorrencias"] = {"number": occurrences_val}

            # Data Deteção
            if "Data Deteção" in schema and first_seen_str:
                props["Data Deteção"] = {"date": {"start": first_seen_str}}
            elif "Data Detecao" in schema and first_seen_str:
                props["Data Detecao"] = {"date": {"start": first_seen_str}}

            # Última Vista
            if "Última Vista" in schema and last_seen_str:
                props["Última Vista"] = {"date": {"start": last_seen_str}}
            elif "Ultima Vista" in schema and last_seen_str:
                props["Ultima Vista"] = {"date": {"start": last_seen_str}}

            if existing_results:
                page_id = existing_results[0]["id"]
                url_patch = f"https://api.notion.com/v1/pages/{page_id}"
                patch_res = requests.patch(url_patch, headers=NOTION_HEADERS, json={"properties": props}, timeout=10)
                if patch_res.status_code in [200, 201]:
                    synced_count += 1
                    logging.info(f"✅ [NOTION ANOMALIES] [{display_title}] atualizado no Notion (Status={status_val}, Ocorrências={occurrences_val}).")
                else:
                    logging.error(f"❌ [NOTION ANOMALIES] Erro ao atualizar [{display_title}] ({patch_res.status_code}): {patch_res.text}")
            else:
                post_payload = {
                    "parent": {"database_id": NOTION_ANOMALIES_DATABASE_ID},
                    "properties": props
                }
                url_post = "https://api.notion.com/v1/pages"
                post_res = requests.post(url_post, headers=NOTION_HEADERS, json=post_payload, timeout=10)
                if post_res.status_code in [200, 201]:
                    synced_count += 1
                    logging.info(f"✅ [NOTION ANOMALIES] Linha criada no Notion para [{display_title}] (Status={status_val}).")
                else:
                    logging.error(f"❌ [NOTION ANOMALIES] Erro ao criar linha [{display_title}] ({post_res.status_code}): {post_res.text}")

        except Exception as ex:
            logging.error(f"❌ Erro no Upsert de Anomalia [{display_title}] para o Notion: {ex}")

    logging.info(f"🎉 Sync de Anomalias Concluído com Sucesso: {synced_count} de {len(anomalies)} anomalias processadas no Notion.")
    return True

if __name__ == "__main__":
    sync_anomalies_quarantine_to_notion()
