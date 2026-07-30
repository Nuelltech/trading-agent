# backend/app/services/alert_service.py
"""
Módulo: alert_service.py (Sistema de Alertas Analíticos & Monitor de SLA de Quarentena)
Especificação Técnica v1.0 - Secção 6

FRONTEIRA NÃO-NEGOCIÁVEL:
O sistema NUNCA executa ordens de forma autónoma. O output máximo é um alerta analítico humano.
"""

import os
import logging
from datetime import datetime, timedelta
import requests
from sqlalchemy import text
from app.database import engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

def format_sweep_alert(sweep_event: dict) -> str:
    """Formata o alerta de sweep de acordo com a norma da Secção 6.1"""
    symbol = sweep_event.get("symbol", "N/A")
    event_type = sweep_event.get("event_type", "SWEEP")
    level = sweep_event.get("level_broken", 0.0)
    ratio = sweep_event.get("threshold_ratio", 1.0)
    timestamp = sweep_event.get("timestamp", datetime.utcnow().strftime("%Y-%m-%d %H:%M"))
    
    tipo_str = "Resistência" if event_type == "SWEEP_TOPO" else "Suporte"
    
    alert_msg = f"[SWEEP ALERT] {symbol} | {tipo_str} ${level:.2f} perfurado | Pavio {ratio}x threshold | {timestamp}"
    return alert_msg

def send_alert_notification(message: str) -> bool:
    """Envia o alerta para Discord Webhook e/ou Telegram Bot (se configurados)"""
    logging.info(f"📢 ALERTA ANALÍTICO DISPARADO: {message}")
    sent = False
    
    # 1. Enviar para Discord Webhook
    if DISCORD_WEBHOOK_URL:
        try:
            res = requests.post(DISCORD_WEBHOOK_URL, json={"content": f"🚨 **{message}**"}, timeout=5)
            if res.status_code in [200, 204]:
                logging.info("✅ Alerta enviado com sucesso para o Discord.")
                sent = True
        except Exception as e:
            logging.error(f"Erro ao enviar alerta para Discord: {e}")

    # 2. Enviar para Telegram Bot
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            payload = {"chat_id": TELEGRAM_CHAT_ID, "text": f"🚨 {message}"}
            res = requests.post(url, json=payload, timeout=5)
            if res.status_code == 200:
                logging.info("✅ Alerta enviado com sucesso para o Telegram.")
                sent = True
        except Exception as e:
            logging.error(f"Erro ao enviar alerta para Telegram: {e}")

    return sent

def check_quarantine_sla_violations(hours_threshold: int = 12) -> int:
    """
    Monitor de SLA de Quarentena (Secção 6.4).
    Verifica se existem registos em data_anomalies_log pendentes há mais de X horas (12h)
    e dispara alerta secundário de dados em falta.
    """
    logging.info(f"🔍 Verificando SLA de Quarentena em 'data_anomalies_log' (Limite: {hours_threshold} horas)...")
    cutoff = datetime.utcnow() - timedelta(hours=hours_threshold)
    violations = 0
    
    try:
        with engine.connect() as conn:
            sql = text("""
                SELECT id, symbol_or_event, anomaly_type, created_at 
                FROM data_anomalies_log 
                WHERE status = 'PENDING' AND created_at <= :cutoff
            """)
            rows = conn.execute(sql, {"cutoff": cutoff}).fetchall()
            
            for row in rows:
                anomaly_id, symbol, anomaly_type, created_at = row
                hours_pending = (datetime.utcnow() - created_at).total_seconds() / 3600.0
                alert_msg = f"⚠️ ALERTA SLA QUARENTENA: Dados em falta para [{symbol}] há {hours_pending:.1f} horas (Status: PENDING | Tipo: {anomaly_type})"
                send_alert_notification(alert_msg)
                violations += 1
                
    except Exception as e:
        logging.error(f"Erro ao verificar SLA de quarentena: {e}")
        
    return violations
