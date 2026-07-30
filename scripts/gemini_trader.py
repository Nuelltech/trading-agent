# scripts/gemini_trader.py
"""
Script Executável: Pipeline ETL Gemini Trader (Camada 2 - Síntese Estratégica com Pandas Enrichment)

Executa as 5 Fases da especificação do Consultor 2:
1. Filtra 'Configuração de Vigilância' no Notion
2. Separa Lista_Macro vs Lista_Operavel
3. Extrai 50 sessões históricas com Pandas e calcula métricas (SMA20/50, Z-Score 20D, ATR_14D, Var 5D)
4. Injeta/atualiza linhas em 'Painel de Mercado Diario - Gemini' com dados quantitativos ([Em processamento])
5. [OPCIONAL] Chama API Gemini e atualiza 'Veredito Tático' no Notion via PATCH (Status [Concluído])
   → Fase 5 só é executada se a variável de ambiente ENABLE_GEMINI_API=true estiver definida.
   → Por design, só deve correr 1x por dia no cron das 22h00 (Briefing Noturno),
     para não esgotar a quota gratuita da API do Google.

Modos de execução:
  - Modo ETL Diurno (cron regular):   ENABLE_GEMINI_API não definida → só Fases 1-4
  - Modo Briefing Noturno (22h00):    ENABLE_GEMINI_API=true → Fases 1-5 (com veredito Gemini)
"""

import os
import sys
import logging

sys.path.append('backend')
from app.services.gemini_trader_service import run_gemini_trader_pipeline

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

def main():
    # Lê a flag de activação da API Gemini a partir da variável de ambiente
    enable_gemini = os.environ.get("ENABLE_GEMINI_API", "false").strip().lower() == "true"

    if enable_gemini:
        logging.info("🚀 Iniciando Pipeline ETL & Gemini Trader... [MODO: BRIEFING NOTURNO — Fases 1 a 5]")
    else:
        logging.info("🚀 Iniciando Pipeline ETL & Gemini Trader... [MODO: ETL DIURNO — Fases 1 a 4]")

    run_gemini_trader_pipeline(enable_gemini_api=enable_gemini)

if __name__ == "__main__":
    main()
