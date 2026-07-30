# scripts/sync_notion_claude_ohlc.py
"""
Script Executável: Sincronização da Watchlist OHLC do Claude no Notion
Lê a Configuração de Vigilância no Notion e executa o Upsert na tabela 'OHLC Ativos Vigiados — Claude'.
"""

import sys
import logging

sys.path.append('backend')
from app.services.notion_claude_ohlc_service import sync_claude_ohlc_to_notion

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

def main():
    logging.info("🚀 Iniciando Sincronização do Painel Diário Claude (OHLC Ativos Vigiados)...")
    success = sync_claude_ohlc_to_notion()
    if success:
        logging.info("🎉 Sincronização do Painel Claude OHLC concluída com sucesso!")
    else:
        logging.error("❌ Ocorreram erros na sincronização do Painel Claude OHLC.")

if __name__ == "__main__":
    main()
