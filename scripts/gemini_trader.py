# scripts/gemini_trader.py
"""
Script Executável: Pipeline ETL Gemini Trader (Camada 2 - Síntese Estratégica com Pandas Enrichment)
Executa as 5 Fases da especificação do Consultor 2:
1. Filtra 'Configuração de Vigilância' no Notion
2. Separa Lista_Macro vs Lista_Operavel
3. Extrai 50 sessões históricas com Pandas e calcula métricas (SMA20/50, Z-Score 20D, ATR_14D, Var 5D)
4. Injeta linhas desnormalizadas em 'Painel de Mercado Diario - Gemini' (estado [Em processamento]) com Eventos 48h
5. Chama API Gemini (gemini-1.5-pro) e atualiza 'Veredito Tático' no Notion via PATCH (Status [Concluído])
"""

import sys
import logging

sys.path.append('backend')
from app.services.gemini_trader_service import run_gemini_trader_pipeline

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

def main():
    logging.info("🚀 Iniciando Pipeline ETL & Gemini Trader...")
    run_gemini_trader_pipeline()

if __name__ == "__main__":
    main()
