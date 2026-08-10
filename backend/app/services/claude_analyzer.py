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

CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "").strip() or os.getenv("ANTHROPIC_MODEL", "").strip() or "claude-sonnet-4-6"
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"

def call_claude_api(system_prompt: str, user_prompt: str, model: Optional[str] = None, max_tokens: int = 1000) -> Optional[str]:
    """
    Executa uma chamada REST direta à API da Anthropic (Claude).
    Possui fallback automático de modelos caso devolva 404.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY_TRADING", "").strip() or os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        logging.error("❌ ANTHROPIC_API_KEY_TRADING ou ANTHROPIC_API_KEY não configurados nos Secrets do GitHub.")
        return None

    primary_model = model or CLAUDE_MODEL
    # Lista de candidatos a testar em sequência se o primeiro devolver 404
    candidate_models = [primary_model, "claude-sonnet-4-6", "claude-3-5-sonnet-20241022", "claude-3-haiku-20240307"]
    
    # Remover duplicados preservando a ordem
    models_to_try = []
    for m in candidate_models:
        if m and m not in models_to_try:
            models_to_try.append(m)

    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json"
    }

    for target_model in models_to_try:
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
                    logging.info(f"✅ Resposta recebida da Anthropic API com sucesso usando modelo [{target_model}] ({len(text_result)} chars).")
                    return text_result
            elif res.status_code == 404:
                logging.warning(f"⚠️ Modelo [{target_model}] não encontrado na Anthropic API (HTTP 404). Tentando próximo modelo...")
                continue
            else:
                logging.error(f"❌ Erro na Anthropic API (HTTP {res.status_code}): {res.text}")
                break
        except Exception as e:
            logging.error(f"❌ Exceção ao chamar Anthropic API ({target_model}): {e}")
            break

    return None
