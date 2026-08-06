# scripts/test_alert_triggers.py
"""
Script de Teste de Disparo de Alertas do Trading Agent
Testa a emissão dos dois pilares de alertas:
1. Alerta de Sweep de Liquidez (Mercado em Tempo Real)
2. Alerta de SLA de Quarentena (Qualidade de Dados)
"""

import sys
import logging
from datetime import datetime

sys.path.append('backend')
from app.services.alert_service import (
    format_sweep_alert,
    send_alert_notification,
    send_email_alert
)

# Tentar carregar dotenv se disponível
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

def test_sweep_alert_trigger() -> bool:
    """Dispara um alerta sintético de Sweep de Liquidez"""
    logging.info("=" * 60)
    logging.info("1️⃣ TESTANDO DISPARO: Alerta de Sweep de Liquidez")
    logging.info("=" * 60)

    synthetic_sweep = {
        "symbol": "Brent (BZ=F) - TESTE",
        "event_type": "SWEEP_FUNDO",
        "level_broken": 89.50,
        "threshold_ratio": 1.58,
        "timestamp": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    }

    alert_message = format_sweep_alert(synthetic_sweep)
    logging.info(f"📢 Alerta Formatado: {alert_message}")

    # Envia via alert_service (Email, Discord, Telegram)
    success = send_alert_notification(alert_message)
    if success:
        logging.info("✅ SUCESSO: Alerta de Sweep de Liquidez entregue com sucesso!")
    else:
        logging.error("❌ FALHA: Erro ao entregar Alerta de Sweep de Liquidez.")
    return success

def test_quarantine_sla_trigger() -> bool:
    """Dispara um alerta sintético de SLA de Quarentena"""
    logging.info("\n" + "=" * 60)
    logging.info("2️⃣ TESTANDO DISPARO: Alerta de SLA de Quarentena (Dados em Falta)")
    logging.info("=" * 60)

    synthetic_symbol = "Gold (GC=F) - TESTE"
    hours_pending = 14.5
    anomaly_type = "VALOR_NULO_CRITICO"

    alert_message = (
        f"⚠️ ALERTA SLA QUARENTENA: Dados em falta para [{synthetic_symbol}] "
        f"há {hours_pending:.1f} horas (Status: PENDING | Tipo: {anomaly_type})"
    )
    logging.info(f"📢 Alerta Formatado: {alert_message}")

    success = send_email_alert("Violação de SLA de Quarentena", alert_message)
    if success:
        logging.info("✅ SUCESSO: Alerta de SLA de Quarentena entregue com sucesso!")
    else:
        logging.error("❌ FALHA: Erro ao entregar Alerta de SLA de Quarentena.")
    return success

def main():
    logging.info("🚀 INICIANDO TESTE INTEGRADO DE ALERTAS DO TRADING AGENT...")
    r1 = test_sweep_alert_trigger()
    r2 = test_quarantine_sla_trigger()

    logging.info("\n" + "=" * 60)
    logging.info("📊 RESUMO DOS TESTES DE ALERTAS")
    logging.info("=" * 60)
    logging.info(f"• Alerta Sweep Liquidez: {'✅ OK' if r1 else '❌ FALHA'}")
    logging.info(f"• Alerta SLA Quarentena: {'✅ OK' if r2 else '❌ FALHA'}")
    logging.info("=" * 60)

    if r1 or r2:
        sys.exit(0)
    else:
        sys.exit(1)

if __name__ == "__main__":
    main()
