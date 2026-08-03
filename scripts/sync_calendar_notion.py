# scripts/sync_calendar_notion.py
"""
Script Executável: Sync Calendário Económico & Resultados (MySQL → Notion)

Sincroniza as tabelas `economic_calendar` e `corporate_earnings_calendar`
(já validadas pelo Data Quality Engine) para a tabela Notion:
  'Calendário Económico & Resultados'

Variáveis de ambiente obrigatórias:
  NOTION_TOKEN (ou NOTION_API_KEY)
  NOTION_CALENDAR_DB_ID
  MYSQL_HOST, MYSQL_PORT, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE

Modos de execução:
  - Incremental (default):  CALENDAR_BACKFILL não definida → últimos 30d + próximos 90d
  - Backfill completo:      CALENDAR_BACKFILL=true → todo o histórico validado no MySQL
    → Usar apenas na primeira corrida ou para re-sincronização total.
"""

import os
import sys
import logging

sys.path.append('backend')
from app.database import engine
from app.services.notion_calendar_sync_service import run_calendar_sync_pipeline

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


def main():
    backfill = os.environ.get("CALENDAR_BACKFILL", "false").strip().lower() == "true"

    if backfill:
        logging.info("🚀 [MODO: BACKFILL COMPLETO] A sincronizar TODO o histórico MySQL → Notion...")
    else:
        logging.info("🚀 [MODO: INCREMENTAL] A sincronizar últimos 30d + próximos 90d MySQL → Notion...")

    try:
        run_calendar_sync_pipeline(backfill=backfill)
    finally:
        try:
            engine.dispose()
        except Exception:
            pass


if __name__ == "__main__":
    main()

