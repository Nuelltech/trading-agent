# scripts/run_executive_summary_agent.py
"""
Script CLI: run_executive_summary_agent.py (Executor do Agente do Resumo Executivo Diário)
Suporta execução isolada por passos para validação gradual (Checklist de Aceitação).
Flags disponíveis:
  --test-scan             : Testar scan mecânico de tickers ativos da DB (>2%)
  --test-positions        : Testar leitura de posições abertas no Diário de Bordo
  --test-future-calendar   : Testar leitura dos eventos dos próximos 7 dias
  --test-synthesis        : Executa Tarefas 1->2 e a síntese da Tarefa 3, imprimindo o JSON no terminal (SEM gravar no Notion)
  --full                  : Execução completa regular com gravação na database Notion do Resumo Executivo
"""

import sys
import os
import json
import argparse
import logging
from datetime import datetime

sys.path.append('backend')
from app.services.executive_summary_service import (
    task1_scan_candidates,
    task1b_open_positions,
    task1c_future_calendar,
    task2_classify_candidates,
    task3_synthesize_briefing,
    get_active_tickers_from_mysql
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


def main():
    parser = argparse.ArgumentParser(description="Agente do Resumo Executivo Diário")
    parser.add_argument("--test-scan", action="store_true", help="PASSO 1: Testa o scan mecânico de tickers dinâmicos (> 2%%)")
    parser.add_argument("--test-positions", action="store_true", help="PASSO 2: Testa a leitura de posições abertas no Diário de Bordo")
    parser.add_argument("--test-future-calendar", action="store_true", help="PASSO 3: Testa a leitura do calendário futuro (próximos 7d)")
    parser.add_argument("--test-synthesis", action="store_true", help="PASSO 4 (NOVO): Executa a síntese do Claude e imprime o JSON no terminal (SEM gravar no Notion)")
    parser.add_argument("--date", type=str, default=datetime.utcnow().strftime("%Y-%m-%d"), help="Data de referência YYYY-MM-DD (padrão: hoje)")
    parser.add_argument("--full", action="store_true", help="PASSO 5: Execução completa com escrita no Notion")

    args = parser.parse_args()
    target_date = args.date.strip()

    # PASSO 1: Testar Scan (>2%) com tickers dinâmicos do MySQL
    if args.test_scan:
        print(f"\n================ PASSO 1: TESTE SCAN DINÂMICO DE TICKERS (>2%) [{target_date}] ================")
        tickers = get_active_tickers_from_mysql()
        print(f"Tickers ativos carregados dinamicamente do MySQL: {len(tickers)} ativos")
        candidates = task1_scan_candidates(target_date)
        print(f"\nCandidatos qualificados (|variação| > 2%, top {len(candidates)} por magnitude):")
        for idx, c in enumerate(candidates, 1):
            print(f"  {idx}. {c['ticker']}: Variação={c['variacao']}% (Hoje={c['close_hoje']} vs Ontem={c['close_ontem']})")
        print("\n========================================================================================\n")
        return

    # PASSO 2: Testar Posições Abertas
    if args.test_positions:
        print(f"\n================ PASSO 2: TESTE POSIÇÕES ABERTAS (DIÁRIO DE BORDO) [{target_date}] ================")
        positions = task1b_open_positions(target_date)
        if positions:
            print(f"Encontradas {len(positions)} posições abertas:")
            for p in positions:
                status_str = "A Favor ✅" if p['a_favor'] else "Contra ❌"
                print(f"  - {p['ativo']} ({p['direcao']}): Variação Hoje={p['variacao_hoje']}% -> Status: {status_str}")
        else:
            print("ℹ️ Nenhuma posição aberta encontrada (estado normal no momento).")
        print("\n========================================================================================\n")
        return

    # PASSO 3: Testar Calendário Futuro
    if args.test_future_calendar:
        print(f"\n================ PASSO 3: TESTE CALENDÁRIO FUTURO (PRÓXIMOS 7 DIAS) [{target_date}] ================")
        events = task1c_future_calendar(target_date)
        print(f"Encontrados {len(events)} eventos de Alto Impacto agendados para os próximos 7 dias:")
        for idx, e in enumerate(events, 1):
            print(f"  {idx}. [{e['data']}] '{e['evento']}'")
            if e['mecanismo']:
                print(f"     Mecanismo: {e['mecanismo']}")
            if e['previsao']:
                print(f"     Previsão: {e['previsao']}")
        print("\n========================================================================================\n")
        return

    # PASSO 4: Dry-Run de Síntese via Claude API (SEM escrever no Notion)
    if args.test_synthesis:
        print(f"\n================ PASSO 4: SÍNTESE CLAUDE API (DRY-RUN - SEM ESCRITA NO NOTION) [{target_date}] ================")
        candidates = task1_scan_candidates(target_date)
        classified = task2_classify_candidates(candidates, target_date)
        positions = task1b_open_positions(target_date)
        future_events = task1c_future_calendar(target_date)

        print("\n🧠 Invocando Claude API para gerar o resumo executivo...")
        synthesis_json = task3_synthesize_briefing(
            target_date=target_date,
            classified_candidates=classified,
            open_positions=positions,
            future_events=future_events,
            dry_run=True
        )

        print("\n--- JSON BRUTO DEVOLVIDO PELA CLAUDE API (DRY RUN) ---")
        if synthesis_json:
            print(json.dumps(synthesis_json, ensure_ascii=False, indent=2))
        else:
            print("❌ Falha ao obter a resposta JSON do Claude.")
        print("\n========================================================================================\n")
        return

    # PASSO 5: Execução Completa (Com escrita no Notion)
    if args.full or len(sys.argv) == 1:
        print(f"\n🚀 Executando Agente do Resumo Executivo Diário completo [{target_date}]...")
        candidates = task1_scan_candidates(target_date)
        classified = task2_classify_candidates(candidates, target_date)
        positions = task1b_open_positions(target_date)
        future_events = task1c_future_calendar(target_date)

        synthesis_json = task3_synthesize_briefing(
            target_date=target_date,
            classified_candidates=classified,
            open_positions=positions,
            future_events=future_events,
            dry_run=False
        )

        if synthesis_json:
            logging.info("🎉 Agente do Resumo Executivo Diário concluído com sucesso!")
        else:
            logging.error("❌ Falha na execução do Agente do Resumo Executivo Diário.")


if __name__ == "__main__":
    main()
