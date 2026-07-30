# scripts/sync_notion_painel_mercado.py
"""
Script Executável: Sincronização do Painel de Mercado / Matriz de Risco no Notion
Lê os valores mais recentes dos indicadores validados no MySQL e escreve no Painel de Mercado do Notion.
"""

import sys
import logging

sys.path.append('backend')
from app.services.notion_painel_mercado_service import publish_painel_mercado_to_notion

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

def main():
    logging.info("🌐 Iniciando Sincronização do Painel de Mercado no Notion...")
    success = publish_painel_mercado_to_notion()
    if success:
        logging.info("🎉 Sincronização do Painel de Mercado concluída com sucesso!")
    else:
        logging.warning("⚠️ Não foi possível sincronizar o Painel de Mercado. Verifique se NOTION_PAINEL_MERCADO_DATABASE_ID está configurada.")

if __name__ == "__main__":
    main()
