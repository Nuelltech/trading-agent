# scripts/test_send_email.py
"""
Script de Teste de Envio de Email - Hello World
Testa os dois planos de contingência do Trading Agent:
- Plano A: Envio SMTP direto (Servidor Nuelltech/Hostinger - Portas 465/587/2525)
- Plano B: Envio via Resend API (HTTPS REST API - Porta 443)
"""

import os
import sys
import socket
import ssl
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

def test_socket_connectivity(host: str, port: int, timeout: float = 10.0) -> tuple[bool, str]:
    """Testa conectividade TCP direta na porta antes de iniciar protocolo SMTP"""
    try:
        ip = socket.gethostbyname(host)
        with socket.create_connection((host, port), timeout=timeout):
            return True, f"Conexão TCP estabelecida com sucesso com {host} ({ip}):{port}"
    except socket.gaierror as dns_err:
        return False, f"Falha na resolução de DNS para '{host}': {dns_err}"
    except socket.timeout:
        return False, f"Timeout ({timeout}s) ao tentar conectar TCP a '{host}':{port} (Porta pode estar bloqueada por firewall/provedor)"
    except Exception as err:
        return False, f"Erro de soquete ao conectar a '{host}':{port} -> {err}"

def test_plano_a_smtp() -> bool:
    """Testa envio de email via SMTP (Servidor Nuelltech / Hostinger)"""
    logging.info("=" * 60)
    logging.info("📧 PLANO A: Testando Envio de Email via SMTP (Servidor Nuelltech / Hostinger)")
    logging.info("=" * 60)

    smtp_server = os.getenv("SMTP_SERVER_HOSTINGER", "").strip() or os.getenv("SMTP_SERVER", "").strip()
    smtp_port_str = os.getenv("SMTP_PORT", "465").strip()
    smtp_username = os.getenv("SMTP_USERNAME", "").strip()
    smtp_password = os.getenv("SMTP_PASSWORD", "").strip()
    email_to = os.getenv("ALERT_EMAIL_TO", "").strip()
    email_from = os.getenv("ALERT_EMAIL_FROM", smtp_username).strip()

    logging.info(f"📍 Servidor SMTP Configurado: '{smtp_server}'")
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
        logging.warning("⚠️ SMTP_USERNAME ou SMTP_PASSWORD vazios.")

    # Lista de servidores a testar (o configurado + servidor cPanel especifico + fallback Hostinger)
    servers_to_test = [smtp_server]
    for fallback_host in ["cpl109.main-hosting.eu", "smtp.hostinger.com"]:
        if fallback_host not in [s.lower() for s in servers_to_test]:
            servers_to_test.append(fallback_host)

    # Montar mensagem MIME
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    subject = "Hello World - Teste de Email (Plano A: SMTP)"

    for server_host in servers_to_test:
        logging.info(f"\n🌐 DIAGNÓSTICO E TESTE PARA O SERVIDOR: '{server_host}'")
        
        # Testar resolução DNS
        try:
            resolved_ip = socket.gethostbyname(server_host)
            logging.info(f"🔍 DNS OK: '{server_host}' resolveu para o IP {resolved_ip}")
        except Exception as dns_err:
            logging.error(f"❌ DNS FALHOU: Não foi possível resolver '{server_host}': {dns_err}")
            continue

        try:
            main_port = int(smtp_port_str)
        except ValueError:
            main_port = 465

        ports_to_try = [main_port]
        for p in [465, 587, 2525]:
            if p not in ports_to_try:
                ports_to_try.append(p)

        for port in ports_to_try:
            logging.info(f"\n🔄 [Servidor: {server_host}] Testando porta {port}...")
            
            # 1. Teste de Conectividade Socket
            sock_ok, sock_msg = test_socket_connectivity(server_host, port, timeout=12.0)
            logging.info(f"  └─ Diagnostic Socket: {sock_msg}")
            
            if not sock_ok:
                logging.warning(f"  ⚠️ Ignorando protocolo SMTP na porta {port} devido a falha de conexão TCP.")
                continue

            # 2. Tentar Envio SMTP
            msg = MIMEMultipart()
            msg["From"] = email_from
            msg["To"] = email_to
            msg["Subject"] = f"🚨 {subject}"
            body = (
                "Hello World!\n\n"
                "Este é um email de teste enviado pelo script de diagnóstico do Trading Agent.\n\n"
                f"• Canal: Plano A (SMTP Directo)\n"
                f"• Servidor Usado: {server_host} (Porta {port})\n"
                f"• Remetente: {email_from}\n"
                f"• Destinatário: {email_to}\n"
                f"• Data/Hora: {timestamp}\n\n"
                "Se recebeste esta mensagem, a funcionalidade de email SMTP está OPERACIONAL! 🚀"
            )
            msg.attach(MIMEText(body, "plain", "utf-8"))

            try:
                if port == 465:
                    logging.info(f"  🔒 Conectando via smtplib.SMTP_SSL({server_host}, {port}, timeout=15)...")
                    context = ssl.create_default_context()
                    with smtplib.SMTP_SSL(server_host, port, context=context, timeout=15) as server:
                        if os.getenv("DEBUG_SMTP"):
                            server.set_debuglevel(1)
                        if smtp_username and smtp_password:
                            logging.info("  🔐 Efetuando login SMTP...")
                            server.login(smtp_username, smtp_password)
                        logging.info("  📤 Enviando mensagem...")
                        server.send_message(msg)
                else:
                    logging.info(f"  🔓 Conectando via smtplib.SMTP({server_host}, {port}, timeout=15)...")
                    with smtplib.SMTP(server_host, port, timeout=15) as server:
                        if os.getenv("DEBUG_SMTP"):
                            server.set_debuglevel(1)
                        server.ehlo()
                        logging.info("  🛡️ Executando STARTTLS...")
                        context = ssl.create_default_context()
                        server.starttls(context=context)
                        server.ehlo()
                        if smtp_username and smtp_password:
                            logging.info("  🔐 Efetuando login SMTP...")
                            server.login(smtp_username, smtp_password)
                        logging.info("  📤 Enviando mensagem...")
                        server.send_message(msg)

                logging.info(f"\n🎉 ✅ SUCESSO PLANO A: Email enviado com sucesso via SMTP ({server_host}:{port})!")
                if server_host != smtp_server:
                    logging.info(f"💡 DICA: Recomenda-se atualizar a variável 'SMTP_SERVER' no GitHub Secrets para '{server_host}'.")
                return True

            except smtplib.SMTPAuthenticationError as auth_err:
                logging.error(f"  ❌ Erro de Autenticação em {server_host}:{port} -> {auth_err}")
                logging.error("  👉 Verifique se SMTP_USERNAME e SMTP_PASSWORD coincidem com a conta de email no cPanel/Hostinger.")
                logging.error("  👉 Certifique-se também de que o remetente (ALERT_EMAIL_FROM) pertence ao mesmo domínio da conta.")
            except Exception as err:
                logging.error(f"  ❌ Erro no envio SMTP em {server_host}:{port} -> {type(err).__name__}: {err}")

    logging.error("\n❌ FALHA PLANO A: Nenhum servidor/porta SMTP conseguiu conectar ou concluir o envio.")
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
        elif response.status_code == 403 and "not verified" in response.text:
            logging.warning("⚠️ Remetente customizado não verificado no Resend. Tentando fallback automático com 'onboarding@resend.dev'...")
            fallback_resp = requests.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {resend_api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "from": "onboarding@resend.dev",
                    "to": [email_to],
                    "subject": f"🚨 {subject} (Fallback Resend)",
                    "text": body
                },
                timeout=10
            )
            logging.info(f"Fallback Resend Status Code: {fallback_resp.status_code}")
            logging.info(f"Fallback Resend Resposta: {fallback_resp.text}")
            if fallback_resp.status_code in [200, 201]:
                logging.info("✅ SUCESSO PLANO B: Email enviado via Resend (Remetente: onboarding@resend.dev)!")
                return True
            else:
                logging.error(f"❌ FALHA PLANO B: Fallback Resend recusado (HTTP {fallback_resp.status_code}).")
                return False
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
        results["Plano A (SMTP Nuelltech / Hostinger)"] = test_plano_a_smtp()

    if args.plan in ["B", "ALL"]:
        results["Plano B (Resend API)"] = test_plano_b_resend()

    logging.info("\n" + "=" * 60)
    logging.info("📊 RESUMO FINAL DOS TESTES DE EMAIL")
    logging.info("=" * 60)
    for plan, success in results.items():
        status_icon = "✅ SUCESSO" if success else "❌ FALHA"
        logging.info(f"• {plan}: {status_icon}")
    logging.info("=" * 60)

    if any(results.values()):
        sys.exit(0)
    else:
        sys.exit(1)

if __name__ == "__main__":
    main()
