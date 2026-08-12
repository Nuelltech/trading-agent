# scripts/notion_close_backfill.py
"""
Backfill retroativo do Close Diario para o Notion.
Lê as datas da variável de ambiente BACKFILL_DATES (YYYY-MM-DD separadas por vírgula).
Fallback: 3 dias em falta do incidente ^TNX/^TYX de agosto 2026.
"""
import sys, os, logging, requests
sys.path.append("backend")

from app.database import engine
from sqlalchemy import text
from app.services.notion_claude_sync_service import (
    fetch_ticker_ohlc,
    get_notion_db_schema_properties,
    NOTION_TOKEN,
    NOTION_CLAUDE_CLOSE_DATABASE_ID,
    NOTION_HEADERS,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger(__name__)

DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"
dates_env = os.getenv("BACKFILL_DATES", "2026-08-07,2026-08-10,2026-08-11")
MISSING_DATES = [d.strip() for d in dates_env.split(",") if d.strip()]

log.info(f"Datas a processar: {MISSING_DATES}")
log.info(f"Dry run: {DRY_RUN}")

if not NOTION_TOKEN or not NOTION_CLAUDE_CLOSE_DATABASE_ID:
    log.error("NOTION_TOKEN ou NOTION_CLAUDE_CLOSE_DATABASE_ID nao configurados.")
    sys.exit(1)

all_indicators = []
try:
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT ticker, name, category FROM indicators_catalog WHERE is_active = TRUE"
        )).fetchall()
        all_indicators = [{"ticker": r[0], "nome": r[1] or r[0], "categoria": r[2] or "Geral"} for r in rows]
    log.info(f"{len(all_indicators)} ativos carregados.")
except Exception as e:
    log.error(f"Erro ao carregar indicators_catalog: {e}")
    sys.exit(1)

db_schema  = get_notion_db_schema_properties(NOTION_CLAUDE_CLOSE_DATABASE_ID)
title_col  = next((p for p, t in db_schema.items() if t == "title"), "Ticker")
date_col   = "Data"      if "Data"      in db_schema else "Date"
close_col  = "Close"     if "Close"     in db_schema else "Fecho"
name_col   = "Nome"      if "Nome"      in db_schema else "Name"
cat_col    = "Categoria" if "Categoria" in db_schema else "Category"

url_query = f"https://api.notion.com/v1/databases/{NOTION_CLAUDE_CLOSE_DATABASE_ID}/query"
url_post  = "https://api.notion.com/v1/pages"

total_created = total_updated = total_skipped = total_errors = 0

for target_date in MISSING_DATES:
    log.info(f"\n{'='*60}\nProcessando {target_date}\n{'='*60}")
    created = updated = skipped = errors = 0

    for ind in all_indicators:
        ticker = ind["ticker"]
        nome   = ind["nome"]
        cat    = ind.get("categoria", "Geral")

        ohlc = fetch_ticker_ohlc(ticker, target_date)
        if not ohlc or ohlc["close"] == 0.0 or ohlc.get("date") != target_date:
            skipped += 1
            continue

        close_val  = ohlc["close"]
        entry_date = ohlc["date"]

        if DRY_RUN:
            log.info(f"  [DRY RUN] [{ticker}] {entry_date}: Close={close_val}")
            created += 1
            continue

        query_payload = {"filter": {"and": [
            {"property": title_col, "title":  {"equals": ticker}},
            {"property": date_col,  "date":   {"equals": entry_date}}
        ]}}
        try:
            res     = requests.post(url_query, headers=NOTION_HEADERS, json=query_payload, timeout=10)
            results = res.json().get("results", []) if res.status_code == 200 else []

            if results:
                page_id  = results[0]["id"]
                patch_r  = requests.patch(
                    f"https://api.notion.com/v1/pages/{page_id}",
                    headers=NOTION_HEADERS,
                    json={"properties": {close_col: {"number": round(close_val, 4)}}},
                    timeout=10
                )
                if patch_r.status_code in [200, 201]:
                    log.info(f"  [{ticker}] {entry_date}: ATUALIZADO (Close={close_val})")
                    updated += 1
                else:
                    log.error(f"  [{ticker}] PATCH falhou ({patch_r.status_code}): {patch_r.text[:100]}")
                    errors += 1
            else:
                props = {
                    title_col: {"title": [{"text": {"content": ticker}}]},
                    close_col: {"number": round(close_val, 4)},
                    date_col:  {"date": {"start": entry_date}},
                }
                if name_col in db_schema:
                    t = db_schema[name_col]
                    props[name_col] = {"select": {"name": nome}} if t == "select" else {"rich_text": [{"text": {"content": nome}}]}
                if cat_col in db_schema:
                    t = db_schema[cat_col]
                    props[cat_col]  = {"select": {"name": cat}}  if t == "select" else {"rich_text": [{"text": {"content": cat}}]}

                post_r = requests.post(
                    url_post,
                    headers=NOTION_HEADERS,
                    json={"parent": {"database_id": NOTION_CLAUDE_CLOSE_DATABASE_ID}, "properties": props},
                    timeout=10
                )
                if post_r.status_code in [200, 201]:
                    log.info(f"  [{ticker}] {entry_date}: CRIADO (Close={close_val})")
                    created += 1
                else:
                    log.error(f"  [{ticker}] POST falhou ({post_r.status_code}): {post_r.text[:100]}")
                    errors += 1
        except Exception as e:
            log.error(f"  [{ticker}] Excecao: {e}")
            errors += 1

    log.info(f"  {target_date}: {created} criados | {updated} atualizados | {skipped} sem dado | {errors} erros")
    total_created += created; total_updated += updated
    total_skipped += skipped; total_errors  += errors

log.info(f"\n{'='*60}")
log.info(f"BACKFILL CONCLUIDO: {total_created} criados | {total_updated} atualizados | {total_skipped} sem dado | {total_errors} erros")
log.info(f"{'='*60}")
