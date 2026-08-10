# backend/app/services/claude_analyzer.py
"""
Módulo: claude_analyzer.py
Data: 10 de Agosto de 2026

Cliente REST nativo para a Anthropic API (Claude).
Suporta os secrets: ANTHROPIC_API_KEY_TRADING (preferencial) e ANTHROPIC_API_KEY.
"""

import os
import json
import logging
from typing import Dict, Any, Optional
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

ANTHROPIC_API_KEY = (
    os.getenv("ANTHROPIC_API_KEY_TRADING", "").strip() or 
    os.getenv("ANTHROPIC_API_KEY", "").strip()
)

CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "").strip() or os.getenv("ANTHROPIC_MODEL", "").strip() or "claude-3-5-haiku-20241022"
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"

def call_claude_api(system_prompt: str, user_prompt: str, model: Optional[str] = None, max_tokens: int = 1000) -> Optional[str]:
    """
    Executa uma chamada REST direta à API da Anthropic (Claude).
    Devolve o texto de resposta ou None em caso de falha.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY_TRADING", "").strip() or os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        logging.error("❌ ANTHROPIC_API_KEY_TRADING ou ANTHROPIC_API_KEY não configurados nos Secrets do GitHub.")
        return None

    target_model = model or CLAUDE_MODEL

    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json"
    }

    payload = {
        "model": target_model,
        "max_tokens": max_tokens,
        "system": system_prompt,
        "messages": [
            {"role": "user", "content": user_prompt}
        ]
    }

    try:
        logging.info(f"🧠 Enviando chamada à Anthropic API (Modelo: {target_model})...")
        res = requests.post(ANTHROPIC_API_URL, headers=headers, json=payload, timeout=30)
        
        if res.status_code == 200:
            data = res.json()
            content_blocks = data.get("content", [])
            if content_blocks and isinstance(content_blocks, list):
                text_result = content_blocks[0].get("text", "")
                logging.info(f"✅ Resposta recebida da Anthropic API com sucesso ({len(text_result)} chars).")
                return text_result
        else:
            logging.error(f"❌ Erro na Anthropic API (HTTP {res.status_code}): {res.text}")
    except Exception as e:
        logging.error(f"❌ Exceção ao chamar Anthropic API: {e}")

    return None
