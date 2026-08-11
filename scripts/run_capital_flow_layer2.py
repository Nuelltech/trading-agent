# scripts/run_capital_flow_layer2.py
"""
Script Executável: Agente Automatizado para a Camada 2 (Fluxo de Capital Semanal)
Data: 11 de Agosto de 2026

Executa em sequência:
1. TAREFA 1 — Cálculo Determinístico (Python Puro, SEM LLM):
   - Lê MySQL para os últimos 10-15 dias úteis (padrão: 11 sessões), calcula variações, Top 3 Líderes/Laggards,
     divergência sustentada Nasdaq-SOX, sweeps e contaminação por earnings.
   - Escreve os campos mecânicos no Notion e verifica se há erros críticos de origem ou HTTP.
2. TAREFA 2 — Síntese com Julgamento (Claude API com ANTHROPIC_API_KEY_TRADING):
   - Executa APENAS se a Tarefa 1 concluiu sem erros críticos de dados ou escrita.
"""

import sys
import logging
from datetime import datetime

sys.path.append('backend')
from app.services.capital_flow_service import (
    run_tarefa1_calculo_mecanico,
    run_tarefa2_sintese_claude
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

def main():
    target_date = sys.argv[1].strip() if len(sys.argv) > 1 and sys.argv[1].strip() else None
    
    if not target_date:
        # Se nenhuma data for fornecida, verificar a última sessão completa disponível no MySQL
        today_str = datetime.utcnow().strftime("%Y-%m-%d")
        try:
            from app.database import engine
            from sqlalchemy import text
            with engine.connect() as conn:
                res = conn.execute(text("SELECT MAX(DATE(timestamp)) FROM indicator_values")).scalar()
                if res:
                    db_max_date = str(res)[:10]
                    target_date = db_max_date
                else:
                    target_date = today_str
        except Exception:
            target_date = today_str

    logging.info(f"🚀 Iniciando Agente Automatizado da Camada 2 (Fluxo de Capital Semanal) - Sessão {target_date}")

    # 1. Executar Tarefa 1
    calc_data, has_critical_error = run_tarefa1_calculo_mecanico(target_date, window_sessions=11)

    # 2. Executar Tarefa 2 (Apenas se Tarefa 1 correu sem erro crítico de dados de origem)
    if not has_critical_error:
        success_t2 = run_tarefa2_sintese_claude(calc_data, has_critical_error)
        if success_t2:
            logging.info("🎉 Agente da Camada 2 concluído com SUCESSO TOTAL (Tarefas 1 e 2).")
        else:
            logging.error("❌ Tarefa 1 concluída no Notion, mas Tarefa 2 falhou na síntese do Claude.")
    else:
        logging.error("🛑 Tarefa 2 CANCELADA devido a falha crítica na Tarefa 1 (registado no Notion).")

if __name__ == "__main__":
    main()
