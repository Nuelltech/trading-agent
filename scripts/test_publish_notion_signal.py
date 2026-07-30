# scripts/test_publish_notion_signal.py
"""
Script de Teste de Publicação no Notion 'Sinais de Liquidez'
Envia um registo de teste estruturado para confirmar a escrita na API do Notion.
"""

import sys
import logging
from datetime import datetime

sys.path.append('backend')
from app.services.notion_sync_service import publish_liquidity_signal_to_notion

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

def test_publish():
    test_signal = {
        "symbol": "Brent (BZ=F) - TESTE",
        "event_type": "SWEEP_FUNDO",
        "timestamp": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        "level_broken": 89.50,
        "wick_size": 1.42,
        "threshold": 0.87,
        "threshold_ratio": 1.63,
        "k_factor": 1.5,
        "atr14": 0.58,
        "atr60_capped": 0.52,
        "status": "LIQUIDEZ_CONSUMIDA",
        "status_atr60": "COMPLETO"
    }
    logging.info("🚀 Enviando registo de teste estruturado para o Notion...")
    success = publish_liquidity_signal_to_notion(test_signal)
    if success:
        logging.info("🎉 Sucesso! O registo de teste apareceu na tua tabela do Notion.")
    else:
        logging.error("❌ Erro ao publicar no Notion.")

if __name__ == "__main__":
    test_publish()
