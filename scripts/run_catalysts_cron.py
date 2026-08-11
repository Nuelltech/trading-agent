# scripts/run_catalysts_cron.py
"""
Script CLI: run_catalysts_cron.py (Executor do Cron de Catalisadores)
Suporta execução isolada por passos para validação gradual (Checklist de Aceitação).
"""

import sys
import os
import argparse
import logging

sys.path.append('backend')
from app.services.notion_page_reader_service import fetch_notion_page_content
from app.services.catalysts_service import (
    get_catalyst_candidates,
    process_single_catalyst_event,
    fetch_camada0_context
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


def main():
    parser = argparse.ArgumentParser(description="Cron de Catalisadores — Leitura de Mapas de Transmissão")
    parser.add_argument("--test-page-reader", type=str, help="PASSO 1: Testa a leitura de blocos de uma página Notion (passar Page ID do Mapa)")
    parser.add_argument("--test-task1", action="store_true", help="PASSO 2: Executa a Tarefa 1 (Identificação de Candidatos nos próximos 14d)")
    parser.add_argument("--single-event", type=str, help="PASSO 3/4: Executa a Tarefa 2 para apenas 1 evento específico (passar Nome do Evento)")
    parser.add_argument("--full", action="store_true", help="Execução completa regular (processa todos os candidatos)")

    args = parser.parse_args()

    # PASSO 1: Testar Leitura de Páginas Notion isoladamente
    if args.test_page_reader:
        page_id = args.test_page_reader.strip()
        print(f"\n================ PASSO 1: LEITURA DE PÁGINA NOTION ({page_id}) ================")
        content = fetch_notion_page_content(page_id)
        print("\n--- CONTEÚDO EXTRAÍDO DA PÁGINA NOTION ---")
        print(content[:3000] if content else "⚠️ [VAZIO / SEM CONTEÚDO]")
        print("\n=================================================================================\n")
        return

    # PASSO 2: Testar Tarefa 1 (Candidatos) isoladamente
    if args.test_task1:
        print("\n================ PASSO 2: TAREFA 1 — CANDIDATOS (PRÓXIMOS 14 DIAS) ================")
        candidates = get_catalyst_candidates()
        print(f"Encontrados {len(candidates)} candidatos de alto impacto nos próximos 14 dias:")
        for idx, c in enumerate(candidates, 1):
            print(f"  {idx}. [{c['event_timestamp']}] '{c['event_name']}' -> Mapa: '{c['map_name']}' (Page ID: {c['map_page_id']})")
        print("\n===================================================================================\n")
        return

    # PASSO 3/4: Testar Tarefa 2 com 1 evento específico
    if args.single_event:
        target_name = args.single_event.strip()
        print(f"\n================ PASSO 3/4: TAREFA 2 — 1 EVENTO ESPECÍFICO ('{target_name}') ================")
        candidates = get_catalyst_candidates()
        matched = [c for c in candidates if c["event_name"].lower() == target_name.lower()]

        if not matched:
            print(f"⚠️ Evento '{target_name}' não encontrado nos candidatos dos próximos 14 dias. A tentar buscar evento direto no MySQL...")
            from app.database import engine
            from sqlalchemy import text
            with engine.connect() as conn:
                r = conn.execute(text("SELECT * FROM economic_calendar WHERE event_name LIKE :name LIMIT 1"), {"name": f"%{target_name}%"}).fetchone()
                if r:
                    matched_dict = dict(r._mapping)
                    from app.services.catalysts_service import EVENTO_PARA_MAPA, load_mapas_transmissao_ids
                    map_name = EVENTO_PARA_MAPA.get(matched_dict["event_name"], "CPI")
                    mapas_ids = load_mapas_transmissao_ids()
                    page_id = mapas_ids.get(map_name, "test-page-id")
                    matched_dict["map_name"] = map_name
                    matched_dict["map_page_id"] = page_id
                    matched = [matched_dict]

        if matched:
            event = matched[0]
            print(f"🎯 Evento selecionado: '{event['event_name']}' (Mapa: '{event['map_name']}')")
            success = process_single_catalyst_event(event)
            print(f"Resultado do processamento: {'✅ SUCESSO' if success else '❌ FALHA'}")
        else:
            print(f"❌ Evento '{target_name}' não encontrado.")
        print("\n========================================================================================\n")
        return

    # EXECUÇÃO COMPLETA (REGULAR)
    if args.full or len(sys.argv) == 1:
        print("\n🚀 Executando Cron de Catalisadores completo...")
        candidates = get_catalyst_candidates()
        if not candidates:
            logging.info("ℹ️ Nenhum candidato a catalisador pendente nos próximos 14 dias.")
            return

        success_count = 0
        for event in candidates:
            ok = process_single_catalyst_event(event)
            if ok:
                success_count += 1

        logging.info(f"🎉 Cron de Catalisadores concluído: {success_count}/{len(candidates)} eventos processados com sucesso.")


if __name__ == "__main__":
    main()
