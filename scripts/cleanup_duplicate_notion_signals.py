# scripts/cleanup_duplicate_notion_signals.py
"""
Script de Limpeza de Duplicados na Database 'Sinais de Liquidez' do Notion.
Identifica páginas com a mesma combinação (Ativo + Data/Hora Deteção + Nível Perfurado),
mantém a primeira e arquiva (archived=True) as páginas duplicadas.
"""

import os
import sys
import logging
import requests

sys.path.append('backend')

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

NOTION_TOKEN = os.getenv("NOTION_TOKEN") or os.getenv("NOTION_API_KEY", "")
NOTION_LIQUIDITY_DATABASE_ID = os.getenv("NOTION_LIQUIDITY_DATABASE_ID", "")

def cleanup_duplicate_liquidity_signals():
    if not NOTION_TOKEN or not NOTION_LIQUIDITY_DATABASE_ID:
        logging.error("❌ NOTION_TOKEN ou NOTION_LIQUIDITY_DATABASE_ID não configurados nas variáveis de ambiente.")
        return

    url_query = f"https://api.notion.com/v1/databases/{NOTION_LIQUIDITY_DATABASE_ID}/query"
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28"
    }

    logging.info(f"🧹 Iniciando varredura e limpeza de duplicados na tabela Sinais de Liquidez ({NOTION_LIQUIDITY_DATABASE_ID})...")

    has_more = True
    next_cursor = None
    all_pages = []

    while has_more:
        payload = {"page_size": 100}
        if next_cursor:
            payload["start_cursor"] = next_cursor

        res = requests.post(url_query, headers=headers, json=payload, timeout=15)
        if res.status_code != 200:
            logging.error(f"❌ Erro ao consultar Notion (HTTP {res.status_code}): {res.text}")
            return

        data = res.json()
        all_pages.extend(data.get("results", []))
        has_more = data.get("has_more", False)
        next_cursor = data.get("next_cursor")

    logging.info(f"📊 Encontradas {len(all_pages)} páginas na tabela.")

    seen_keys = set()
    duplicates_archived = 0

    for page in all_pages:
        page_id = page["id"]
        props = page.get("properties", {})

        ativo_list = props.get("Ativo", {}).get("title", [])
        ativo = ativo_list[0].get("text", {}).get("content", "").strip() if ativo_list else ""

        date_dict = props.get("Data/Hora Deteção", {}).get("date", {}) or {}
        data_hora = date_dict.get("start", "")

        nivel = props.get("Nível Perfurado", {}).get("number", "")

        key = f"{ativo}|{data_hora}|{nivel}"

        if key in seen_keys:
            url_archive = f"https://api.notion.com/v1/pages/{page_id}"
            del_res = requests.patch(url_archive, headers=headers, json={"archived": True}, timeout=10)
            if del_res.status_code == 200:
                logging.info(f"  🗑️ Arquivada página duplicada [{key}] (ID: {page_id})")
                duplicates_archived += 1
            else:
                logging.warning(f"  ⚠️ Falha ao arquivar página [{page_id}]: {del_res.status_code}")
        else:
            seen_keys.add(key)

    logging.info(f"🎉 Limpeza concluída! {duplicates_archived} páginas duplicadas foram arquivadas. Total de páginas únicas mantidas: {len(seen_keys)}.")

if __name__ == "__main__":
    cleanup_duplicate_liquidity_signals()
