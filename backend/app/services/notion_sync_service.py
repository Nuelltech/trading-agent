# backend/app/services/notion_sync_service.py
"""
Módulo: notion_sync_service.py (Integração com a Database Notion 'Sinais de Liquidez')
Especificação Técnica v2.0 - Secção 3.2

REGRA RÍGIDA:
Apenas escreve campos numéricos e categóricos discretos.
Zero frases ou texto interpretativo de análise/síntese.
Database Notion Target: Sinais de Liquidez (d541f7d1-cc48-4707-8e6d-e5010b2522e4)
"""

import os
import logging
from typing import Dict, Any, Optional
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

NOTION_API_KEY = os.getenv("NOTION_API_KEY", "")
NOTION_LIQUIDITY_DATABASE_ID = os.getenv("NOTION_LIQUIDITY_DATABASE_ID", "d541f7d1-cc48-4707-8e6d-e5010b2522e4")

def build_notion_liquidity_payload(signal: Dict[str, Any], database_id: str = NOTION_LIQUIDITY_DATABASE_ID) -> Dict[str, Any]:
    """
    Constrói o payload JSON estritamente numérico e categórico para a API do Notion.
    Retorna estrutura compatível com Notion API v1 /pages.
    """
    symbol = signal.get("symbol", "N/A")
    event_type = signal.get("event_type", "SWEEP")
    tipo_sinal = "Bullish Sweep" if event_type == "SWEEP_FUNDO" else "Bearish Sweep"
    
    timestamp_iso = signal.get("timestamp", "")
    if " " in timestamp_iso and not "T" in timestamp_iso:
        timestamp_iso = timestamp_iso.replace(" ", "T")
    if len(timestamp_iso) == 10:
        timestamp_iso += "T00:00:00"

    payload = {
        "parent": {"database_id": database_id},
        "properties": {
            "Ativo": {
                "title": [
                    {"text": {"content": str(symbol)}}
                ]
            },
            "Data/Hora Deteção": {
                "date": {"start": timestamp_iso}
            },
            "Tipo de Sinal": {
                "select": {"name": tipo_sinal}
            },
            "Nível Perfurado": {
                "number": float(signal.get("level_broken", 0.0))
            },
            "Pavio (valor absoluto)": {
                "number": float(signal.get("wick_size", 0.0))
            },
            "Threshold Usado": {
                "number": float(signal.get("threshold", 0.0))
            },
            "Rácio Pavio/Threshold": {
                "number": float(signal.get("threshold_ratio", 0.0))
            },
            "K Utilizado": {
                "number": float(signal.get("k_factor", 1.5))
            },
            "ATR_14": {
                "number": float(signal.get("atr14", 0.0))
            },
            "ATR_60 (cap aplicado)": {
                "number": float(signal.get("atr60_capped", 0.0))
            },
            "Status Liquidez": {
                "select": {"name": "Consumida" if signal.get("status") == "LIQUIDEZ_CONSUMIDA" else "Ainda Válida"}
            },
            "Status ATR_60": {
                "select": {"name": "Incompleto (Cold-Start)" if signal.get("status_atr60") == "ATR_60_INCOMPLETO" else "Completo"}
            }
        }
    }
    return payload

def publish_liquidity_signal_to_notion(signal: Dict[str, Any]) -> bool:
    """Envia um registo de sinal de liquidez para a database do Notion 'Sinais de Liquidez'"""
    if not NOTION_API_KEY:
        logging.warning("⚠️ [NOTION] NOTION_API_KEY não configurada. Escrita no Notion ignorada.")
        return False
        
    url = "https://api.notion.com/v1/pages"
    headers = {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28"
    }
    
    payload = build_notion_liquidity_payload(signal)
    
    try:
        res = requests.post(url, json=payload, headers=headers, timeout=10)
        if res.status_code in [200, 201]:
            logging.info(f"✅ [NOTION] Sinal de Liquidez publicado no Notion com sucesso para [{signal.get('symbol')}]")
            return True
        else:
            logging.error(f"❌ [NOTION] Erro na API do Notion ({res.status_code}): {res.text}")
            return False
    except Exception as e:
        logging.error(f"❌ [NOTION] Falha na ligação à API do Notion: {e}")
        return False
