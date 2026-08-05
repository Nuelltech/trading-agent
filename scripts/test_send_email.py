# scripts/test_send_email.py
"""
Script de Teste de Envio de Email - Hello World
Testa os dois planos de contingência do Trading Agent:
- Plano A: Envio SMTP direto (Servidor Nuelltech/Hostinger - Portas 465/587)
- Plano B: Envio via Resend API (HTTPS REST API - Porta 443)
"""

import os
import sys
import argparse
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
import requests

# Tentar carregar dotenv se disponível
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)

def test_plano_a_smtp() -> bool:
    """Testa envio de email via SMTP (Servidor Nuelltech / Hostinger)"""
    logging.info("=" * 60)
    logging.info("📧 PLANO A: Testando Envio de Email via SMTP (Servidor Nuelltech)")
    logging.info("=" * 60)

    smtp_server = os.getenv("SMTP_SERVER", "").strip()
    smtp_port_str = os.getenv("SMTP_PORT", "465").strip()
    smtp_username = os.getenv("SMTP_USERNAME", "").strip()
    smtp_password = os.getenv("SMTP_PASSWORD", "").strip()
    email_to = os.getenv("ALERT_EMAIL_TO", "").strip()
    email_from = os.getenv("ALERT_EMAIL_FROM", smtp_username).strip()

    logging.info(f"📍 Servidor SMTP: '{smtp_server}'")
    logging.info(f"🔌 Porta Inicial Configurada: {smtp_port_str}")
    logging.info(f"👤 Utilizador SMTP: '{smtp_username}'")
    logging.info(f"📤 De (From): '{email_from}'")
    logging.info(f"📥 Para (To): '{email_to}'")

    if not smtp_server:
        logging.error("❌ FALHA PLANO A: Variável 'SMTP_SERVER' não está definida!")
        return False
    if not email_to:
        logging.error("❌ FALHA PLANO A: Variável 'ALERT_EMAIL_TO' não está definida!")
        return False
    if not smtp_username or not smtp_password:
        logging.warning("⚠️ SMTP_USERNAME ou SMTP_PASSWORD vazios. Tentando envio sem autenticação...")

    # Montar mensagem MIME
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    subject = "Hello World - Teste de Email (Plano A: SMTP Nuelltech)"
    body = (
        "Hello World!\n\n"
        "Este é um email de teste enviado pelo script de diagnóstico do Trading Agent.\n\n"
        f"• Canal: Plano A (SMTP Directo)\n"
        f"• Servidor: {smtp_server}\n"
        f"• Remetente: {email_from}\n"
        f"• Destinatário: {email_to}\n"
        f"• Data/Hora: {timestamp}\n\n"
        "Se recebeste esta mensagem, a funcionalidade de email SMTP está OPERACIONAL! 🚀"
    )

    msg = MIMEMultipart()
    msg["From"] = email_from
    msg["To"] = email_to
    msg["Subject"] = f"🚨 {subject}"
    msg.attach(MIMEText(body, "plain", "utf-8"))

    # Definir portas a testar (465 SSL preferencial recomendado pela Hostinger, fallback 587 STARTTLS)
    try:
        main_port = int(smtp_port_str)
    except ValueError:
        main_port = 465

    ports_to_try = [main_port]
    if main_port == 465 and 587 not in ports_to_try:
        ports_to_try.append(587)
    elif main_port == 587 and 465 not in ports_to_try:
        ports_to_try.append(465)

    for port in ports_to_try:
        logging.info(f"\n🔄 Tentando conexão SMTP na porta {port}...")
        try:
            if port == 465:
                logging.info(f"🔒 Conectando via SMTP_SSL a {smtp_server}:{port} (timeout=10s)...")
                with smtplib.SMTP_SSL(smtp_server, port, timeout=10) as server:
                    server.set_debuglevel(1) if os.getenv("DEBUG_SMTP") else None
                    if smtp_username and smtp_password:
                        logging.info("🔐 Efetuando autenticação login SMTP...")
                        server.login(smtp_username, smtp_password)
                    logging.info("📤 Enviando mensagem...")
                    server.send_message(msg)
            else:
                logging.info(f"🔓 Conectando via SMTP standard com STARTTLS a {smtp_server}:{port} (timeout=10s)...")
                with smtplib.SMTP(smtp_server, port, timeout=10) as server:
                    server.set_debuglevel(1) if os.getenv("DEBUG_SMTP") else None
                    server.ehlo()
                    logging.info("🛡️ Iniciando STARTTLS...")
                    server.starttls()
                    server.ehlo()
                    if smtp_username and smtp_password:
                        logging.info("🔐 Efetuando autenticação login SMTP...")
                        server.login(smtp_username, smtp_password)
                    logging.info("📤 Enviando mensagem...")
                    server.send_message(msg)

            logging.info(f"✅ SUCESSO PLANO A: Email enviado com sucesso via SMTP (Porta {port})!")
            return True

        except smtplib.SMTPAuthenticationError as auth_err:
            logging.error(f"❌ Erro de Autenticação na porta {port}: {auth_err}")
            logging.error("👉 Verifique se SMTP_USERNAME e SMTP_PASSWORD estão corretos no cPanel/Hostinger.")
        except smtplib.SMTPConnectError as conn_err:
            logging.error(f"❌ Erro de Conexão na porta {port}: {conn_err}")
        except Exception as err:
            logging.error(f"❌ Erro ao enviar na porta {port}: {type(err).__name__} - {err}")

    logging.error("❌ FALHA PLANO A: Nenhuma porta SMTP (465/587) conseguiu concluir o envio.")
    return False


def test_plano_b_resend() -> bool:
    """Testa envio de email via Resend HTTPS REST API"""
    logging.info("\n" + "=" * 60)
    logging.info("📨 PLANO B: Testando Envio de Email via Resend (HTTPS API)")
    logging.info("=" * 60)

    resend_api_key = os.getenv("RESEND_API_KEY", "").strip()
    email_to = os.getenv("ALERT_EMAIL_TO", "").strip()
    email_from = os.getenv("ALERT_EMAIL_FROM", "").strip() or "onboarding@resend.dev"

    logging.info(f"🔑 RESEND_API_KEY Presente: {'Sim (' + resend_api_key[:6] + '...)' if resend_api_key else 'Não'}")
    logging.info(f"📤 De (From): '{email_from}'")
    logging.info(f"📥 Para (To): '{email_to}'")

    if not resend_api_key:
        logging.error("❌ FALHA PLANO B: Variável 'RESEND_API_KEY' não está definida!")
        return False
    if not email_to:
        logging.error("❌ FALHA PLANO B: Variável 'ALERT_EMAIL_TO' não está definida!")
        return False

    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    subject = "Hello World - Teste de Email (Plano B: Resend API)"
    body = (
        "Hello World!\n\n"
        "Este é um email de teste enviado pelo script de diagnóstico do Trading Agent.\n\n"
        f"• Canal: Plano B (Resend REST API HTTPS)\n"
        f"• Remetente: {email_from}\n"
        f"• Destinatário: {email_to}\n"
        f"• Data/Hora: {timestamp}\n\n"
        "Se recebeste esta mensagem, o envio via Resend API está OPERACIONAL! 🚀"
    )

    try:
        logging.info("🌐 Fazendo requisição HTTP POST para https://api.resend.com/emails ...")
        response = requests.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {resend_api_key}",
                "Content-Type": "application/json"
            },
            json={
                "from": email_from,
                "to": [email_to],
                "subject": f"🚨 {subject}",
                "text": body
            },
            timeout=10
        )

        logging.info(f"HTTP Status Code: {response.status_code}")
        logging.info(f"HTTP Resposta: {response.text}")

        if response.status_code in [200, 201]:
            logging.info("✅ SUCESSO PLANO B: Email enviado com sucesso via Resend API!")
            return True
        else:
            logging.error(f"❌ FALHA PLANO B: Resend API retornou erro HTTP {response.status_code}.")
            return False

    except Exception as err:
        logging.error(f"❌ FALHA PLANO B: Erro na requisição HTTP Resend API: {err}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Script de Teste de Envio de Email (Hello World)")
    parser.add_argument(
        "--plan",
        choices=["A", "B", "ALL"],
        default="ALL",
        help="Escolha o plano a testar: A (SMTP Nuelltech), B (Resend API), ou ALL (ambos)"
    )
    args = parser.parse_args()

    results = {}

    if args.plan in ["A", "ALL"]:
        results["Plano A (SMTP Nuelltech)"] = test_plano_a_smtp()

    if args.plan in ["B", "ALL"]:
        results["Plano B (Resend API)"] = test_plano_b_resend()

    logging.info("\n" + "=" * 60)
    logging.info("📊 RESUMO FINAL DOS TESTES DE EMAIL")
    logging.info("=" * 60)
    for plan, success in results.items():
        status_icon = "✅ SUCESSO" if success else "❌ FALHA"
        logging.info(f"• {plan}: {status_icon}")
    logging.info("=" * 60)

    # Retorna código de erro 0 se pelo menos um funcionou, ou 1 se todos falharam
    if any(results.values()):
        sys.exit(0)
    else:
        sys.exit(1)

if __name__ == "__main__":
    main()
