# scripts/sync_notion_claude.py
"""
Script Executável: Sincronização Geral do Painel do Claude no Notion (Adenda v2)
Executa de forma síncrona:
1. OHLC Ativos Vigiados — Claude (076b90fe-be23-4cdc-933a-e46dc99d669c)
2. Close Diário — Todos os Ativos — Claude (25fd82e4-92d7-4401-af67-a39daeec9e0b)
3. Resumo Diário — Regime de Risco — Claude (3efd828b-84a7-4966-8bdf-fe9c93657edd)
"""

import sys
import logging

sys.path.append('backend')
from app.database import engine
from app.services.notion_claude_sync_service import (
    sync_claude_ohlc_vigiados,
    sync_claude_close_todos_ativos,
    sync_claude_resumo_regime
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

def main():
    logging.info("🚀 Iniciando Sincronização das Databases do Claude no Notion (Adenda v2)...")
    try:
        # 1. OHLC Ativos Vigiados (Fase A Configuração + Fase B Upsert)
        logging.info("📊 1/3 Syncing 'OHLC Ativos Vigiados — Claude'...")
        sync_claude_ohlc_vigiados()

        # 2. Close Diário — Todos os Ativos (36 indicadores)
        logging.info("📈 2/3 Syncing 'Close Diário — Todos os Ativos — Claude'...")
        sync_claude_close_todos_ativos()

        # 3. Resumo Diário — Regime de Risco (Linha da Sessão sem tocar no Regime)
        logging.info("🏛️ 3/3 Syncing 'Resumo Diário — Regime de Risco — Claude'...")
        sync_claude_resumo_regime()

        logging.info("🎉 Sincronização de todas as databases do Claude concluída!")
    finally:
        try:
            engine.dispose()
        except Exception:
            pass

if __name__ == "__main__":
    main()

