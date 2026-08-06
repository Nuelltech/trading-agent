"""
Script Executável: Recolha de Feed de Notícias de Mercado (FMP → Notion)

Recolhe headlines financeiras em bruto via Financial Modeling Prep:
  - /v3/stock_news para tickers ativos na Configuração de Vigilância
  - /v4/general_news para catalisadores macro sem ticker específico

Insere na tabela Notion 'Feed de Notícias' (e1c8d3ab-a151-499f-8931-4537f29933ec).

Variáveis de ambiente obrigatórias:
  FMP_API_KEY
  NOTION_TOKEN (ou NOTION_API_KEY)
  NOTION_CONFIG_DB_ID
  NOTION_NEWS_DB_ID (opcional — tem default hardcoded)

Modos de execução:
  - Cron (default):   sem argumentos → últimas 2h (modo incremental)
  - Backfill inicial: --backfill     → últimos 7 dias
"""

import os
import sys
import argparse
import logging

sys.path.append('backend')
from app.services.news_collector import run_news_collection

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


def main():
    try:
        parser = argparse.ArgumentParser(description="Feed de Notícias de Mercado (FMP → Notion)")
        parser.add_argument(
            "--backfill",
            action="store_true",
            default=False,
            help="Modo backfill: recolhe os últimos 7 dias em vez das últimas 2h"
        )
        args = parser.parse_args()

        if args.backfill:
            logging.info("🚀 [MODO BACKFILL] Recolhendo notícias dos últimos 7 dias...")
        else:
            logging.info("🚀 [MODO CRON] Recolhendo notícias recentes (últimas 2h)...")

        stats = run_news_collection(backfill=args.backfill)

        if stats.get("errors", 0) > 0:
            logging.warning(f"⚠️ {stats['errors']} erros durante a recolha — verificar logs acima.")
            try:
                from app.services.alert_service import send_alert_notification
                send_alert_notification(f"⚠️ [AVISO INGESTÃO] Feed de Notícias reportou {stats['errors']} erros durante a recolha.")
            except Exception:
                pass
    except Exception as e:
        logging.error(f"❌ Erro fatal no pipeline de notícias: {e}")
        try:
            from app.services.alert_service import send_alert_notification
            send_alert_notification(f"🚨 [FALHA CRÍTICA INGESTÃO] Ingestão de Notícias falhou: {type(e).__name__} - {e}")
        except Exception:
            pass
        raise

    sys.exit(0)

if __name__ == "__main__":
    main()

