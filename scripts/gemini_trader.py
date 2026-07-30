# scripts/gemini_trader.py
"""
Script Executável: Pipeline ETL Gemini Trader (Camada 2 - Síntese Estratégica)
Executa as 5 Fases da especificação do Consultor 2:
1. Filtra 'Configuração de Vigilância' no Notion
2. Separa Lista_Macro vs Lista_Operavel
3. Consulta OHLC e Macro no MySQL
4. Injeta linhas desnormalizadas em 'Painel de Mercado Diario - Gemini' (estado [Em processamento])
5. Chama API Gemini (gemini-1.5-pro) e atualiza 'Veredito Tático' no Notion via PATCH
"""

import sys
import logging

sys.path.append('backend')
from app.services.gemini_trader_service import (
    phase1_extract_vigilance_config,
    phase2_split_categorical,
    phase3_fetch_mysql_data,
    phase4_inject_desdenormalized_rows,
    phase5_gemini_verdict_and_patch
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

def main():
    logging.info("🚀 Iniciando Pipeline ETL & Gemini Trader (Camada 2)...")
    
    # Fase 1: Extração e Filtro Mestre
    config_items = phase1_extract_vigilance_config()
    if not config_items:
        logging.warning("⚠️ Nenhum item ativo encontrado na Configuração de Vigilância. Encerrando.")
        return

    # Fase 2: Split Categórico (Em Memória)
    lista_macro, lista_operavel = phase2_split_categorical(config_items)

    # Fase 3: Consulta ao MySQL
    macro_data, operavel_data = phase3_fetch_mysql_data(lista_macro, lista_operavel)

    # Fase 4: Injeção Desnormalizada no Notion
    created_page_ids = phase4_inject_desdenormalized_rows(lista_operavel, macro_data, operavel_data)

    # Fase 5: Gatilho e Retorno do Gemini (PATCH)
    success = phase5_gemini_verdict_and_patch(created_page_ids)
    if success:
        logging.info("🎉 Pipeline Gemini Trader concluído com sucesso!")
    else:
        logging.error("❌ Ocorreram erros na execução da Fase 5 do Gemini Trader.")

if __name__ == "__main__":
    main()
