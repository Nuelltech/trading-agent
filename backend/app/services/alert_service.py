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

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formataddr

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

SMTP_SERVER = os.getenv("SMTP_SERVER_HOSTINGER", "").strip() or os.getenv("SMTP_SERVER", "").strip()
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "")
SMTP_PASSWORD = os.getenv("SMTP_HOSTINGER_PASSWD", "").strip() or os.getenv("SMTP_PASSWORD", "").strip()
ALERT_EMAIL_TO = os.getenv("ALERT_EMAIL_TO", "")
ALERT_EMAIL_FROM = os.getenv("ALERT_EMAIL_FROM", SMTP_USERNAME or "alerts@tradingagent.local")
ALERT_EMAIL_FROM_NAME = os.getenv("ALERT_EMAIL_FROM_NAME", "Trader AI").strip()

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

def remove_emojis(text: str) -> str:
    """Remove emojis, simbolos graficos e seletores de variacao Unicode para garantir 0% de pontuação antispam"""
    import re
    emoji_pattern = re.compile(
        "["
        "\U0001F600-\U0001F64F"
        "\U0001F300-\U0001F5FF"
        "\U0001F680-\U0001F6FF"
        "\U0001F1E0-\U0001F1FF"
        "\U00002600-\U000026FF"
        "\U00002700-\U000027BF"
        "\U0001F900-\U0001F9FF"
        "\uFE00-\uFE0F"
        "\u200D"
        "]+", flags=re.UNICODE
    )
    clean = emoji_pattern.sub("", text)
    # Remover múltiplos espaços
    clean = re.sub(r' +', ' ', clean)
    return clean.strip()



def send_email_alert(subject: str, message: str) -> bool:
    """Envia alerta por Email via SMTP. Garantia de 0% emojis no assunto e corpo."""
    if not (SMTP_SERVER and ALERT_EMAIL_TO):
        return False
    try:
        sender_address = ALERT_EMAIL_FROM or SMTP_USERNAME
        from_header = formataddr((ALERT_EMAIL_FROM_NAME, sender_address)) if ALERT_EMAIL_FROM_NAME else sender_address

        # REGRA RÍGIDA: Assunto e mensagem 100% livres de emojis
        clean_subj_raw = remove_emojis(subject)
        clean_subject = f"Trading Agent: {clean_subj_raw}" if not clean_subj_raw.startswith("Trading Agent") else clean_subj_raw
        clean_body = remove_emojis(message)

        msg = MIMEMultipart()
        msg["From"] = from_header
        msg["To"] = ALERT_EMAIL_TO
        msg["Subject"] = clean_subject
        msg.attach(MIMEText(clean_body, "plain", "utf-8"))

        ports_to_try = [int(SMTP_PORT)]
        if int(SMTP_PORT) == 465 and 587 not in ports_to_try:
            ports_to_try.append(587)
        elif int(SMTP_PORT) == 587 and 465 not in ports_to_try:
            ports_to_try.append(465)

        for port in ports_to_try:
            try:
                if port == 465:
                    with smtplib.SMTP_SSL(SMTP_SERVER, port, timeout=5) as server:
                        if SMTP_USERNAME and SMTP_PASSWORD:
                            server.login(SMTP_USERNAME, SMTP_PASSWORD)
                        server.send_message(msg)
                else:
                    with smtplib.SMTP(SMTP_SERVER, port, timeout=5) as server:
                        server.starttls()
                        if SMTP_USERNAME and SMTP_PASSWORD:
                            server.login(SMTP_USERNAME, SMTP_PASSWORD)
                        server.send_message(msg)
                logging.info(f"✅ Alerta enviado com sucesso via SMTP (Porta {port}) para email {ALERT_EMAIL_TO}.")
                return True
            except Exception as port_err:
                logging.warning(f"⚠️ Tentativa SMTP na Porta {port} falhou ({port_err}). Tentando porta alternativa...")

        # Fallback via HTTPS API (Resend API)
        resend_key = os.getenv("RESEND_API_KEY", "")
        if resend_key:
            try:
                resp = requests.post(
                    "https://api.resend.com/emails",
                    headers={"Authorization": f"Bearer {resend_key}", "Content-Type": "application/json"},
                    json={"from": from_header or "Trader AI <onboarding@resend.dev>", "to": [ALERT_EMAIL_TO], "subject": clean_subject, "text": clean_body},
                    timeout=5
                )

                if resp.status_code in [200, 201]:
                    logging.info(f"✅ Alerta enviado com sucesso via Resend HTTPS API para {ALERT_EMAIL_TO}.")
                    return True
                elif resp.status_code == 403 and "not verified" in resp.text:
                    logging.warning("⚠️ Remetente customizado não verificado no Resend. Tentando fallback com 'onboarding@resend.dev'...")
                    fb_resp = requests.post(
                        "https://api.resend.com/emails",
                        headers={"Authorization": f"Bearer {resend_key}", "Content-Type": "application/json"},
                        json={"from": "onboarding@resend.dev", "to": [ALERT_EMAIL_TO], "subject": clean_subject, "text": clean_body},
                        timeout=5
                    )
                    if fb_resp.status_code in [200, 201]:
                        logging.info(f"✅ Alerta enviado com sucesso via Resend (Remetente: onboarding@resend.dev) para {ALERT_EMAIL_TO}.")
                        return True
                    else:
                        logging.warning(f"⚠️ Resend Fallback recusou (HTTP {fb_resp.status_code}): {fb_resp.text}")
                else:
                    logging.warning(f"⚠️ Resend HTTPS API recusou (HTTP {resp.status_code}): {resp.text}")
            except Exception as r_err:
                logging.warning(f"⚠️ Resend HTTPS API falhou: {r_err}")

        logging.error("❌ Erro permanente ao enviar alerta por Email em todas as portas SMTP (465 e 587) e HTTPS API.")
        return False
    except Exception as e:
        logging.error(f"Erro ao enviar alerta por Email: {e}")
        return False

def send_digest_email_alert(subject: str, alert_items: list) -> bool:
    """
    REGRA DE AGRUPAMENTO: Envia 1 ÚNICO e-mail consolidado (Digest Report) contendo todos os alertas
    detetados durante a execução do cron.
    Garante 0% emojis no assunto e no corpo.
    """
    if not alert_items:
        return False

    clean_items = [remove_emojis(str(item)) for item in alert_items if str(item).strip()]
    if not clean_items:
        return False

    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    clean_subj_raw = remove_emojis(subject)
    clean_subject = f"Trading Agent: {clean_subj_raw}" if not clean_subj_raw.startswith("Trading Agent") else clean_subj_raw

    body_lines = [
        "Trading Agent System Alert Summary Report",
        "--------------------------------------------------",
        f"Timestamp: {timestamp}",
        f"Total Events Detected in Execution: {len(clean_items)}",
        "",
        "Summary of Events:",
        "--------------------------------------------------"
    ]

    for idx, item in enumerate(clean_items, 1):
        body_lines.append(f"{idx}. {item}")

    body_lines.extend([
        "",
        "--------------------------------------------------",
        "This is an automated system notification from Trading Agent."
    ])

    body = "\n".join(body_lines)
    return send_email_alert(clean_subject, body)


def send_alert_notification(message: str, subject: Optional[str] = None, send_email: bool = True) -> bool:
    """Envia o alerta para Discord Webhook, Telegram Bot e/ou Email (se configurados)"""
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

    # 3. Enviar por Email (SMTP) - Apenas se send_email=True
    if send_email and SMTP_SERVER and ALERT_EMAIL_TO:
        if not subject:
            if message.startswith("[SWEEP ALERT]"):
                parts = message.split("|")
                subject = f"Sweep Event {parts[0].replace('[SWEEP ALERT]', '').strip()}"
            elif "QUARENTENA" in message:
                subject = "Data Quarantine Alert"
            else:
                subject = "System Notification"

        if send_email_alert(subject, message):
            sent = True

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
            return violations
                
    except Exception as e:
        logging.error(f"Erro ao verificar SLA de quarentena: {e}")
        return -1
        
    return violations
