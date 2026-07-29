# backend/app/services/data_validator.py
"""
Data Quality Engine & Pipeline Validation Service
Implementa os 6 pilares de integridade de dados e os refinamentos do Consultor 1.
"""

import logging
from typing import Dict, Any, List, Tuple
from sqlalchemy import text
from app.database import engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# 1. Limites Rígidos de Plausibilidade por Ticker
PLAUSIBILITY_LIMITS = {
    "^VIX": {"min": 8.0, "max": 90.0},
    "^TNX": {"min": 0.5, "max": 10.0},
    "^TYX": {"min": 0.5, "max": 10.0},
    "^IRX": {"min": 0.1, "max": 10.0},
    "EURUSD=X": {"min": 0.80, "max": 1.60},
    "GBPUSD=X": {"min": 0.90, "max": 2.10},
    "USDJPY=X": {"min": 90.0, "max": 200.0},
    "USDCHF=X": {"min": 0.70, "max": 1.40},
    "AUDUSD=X": {"min": 0.40, "max": 1.10},
    "USDCAD=X": {"min": 1.00, "max": 1.70},
    "GC=F": {"min": 1500.0, "max": 5000.0},
    "CL=F": {"min": 20.0, "max": 200.0},
    "BZ=F": {"min": 20.0, "max": 200.0},
    "HG=F": {"min": 2.0, "max": 10.0},
    "^GSPC": {"min": 2000.0, "max": 10000.0},
    "^NDX": {"min": 5000.0, "max": 30000.0},
    "^STOXX50E": {"min": 2000.0, "max": 8000.0},
    "^GDAXI": {"min": 8000.0, "max": 30000.0},
    "DX-Y.NYB": {"min": 70.0, "max": 130.0}
}

# 2. Limiares de Spikes por Classe de Ativo (Evita Alert Fatigue)
SPIKE_THRESHOLDS = {
    "^VIX": 0.35,        # 35% - VIX oscila fortemente em momentos de pânico real
    "BZ=F": 0.12,        # 12% - Petróleo Brent afetado por choques geopolíticos
    "CL=F": 0.12,        # 12% - Petróleo WTI
    "GC=F": 0.08,        # 8%  - Ouro
    "^TNX": 0.08,        # 8%  - Bond Yields em variação % diária
    "^TYX": 0.08,        # 8%  - Bond Yields 30Y
    "DX-Y.NYB": 0.03,    # 3%  - DXY raramente move mais de 3% num único dia
    "EURUSD=X": 0.025,   # 2.5% - Major Forex
    "GBPUSD=X": 0.025,   # 2.5%
    "USDJPY=X": 0.025,   # 2.5%
    "default": 0.15      # 15% default para ações (O, DAL, F, ENPH, NKE, STLA) e ETFs
}

CENTRAL_BANK_EVENTS = [
    "Fed Interest Rate Decision",
    "ECB Interest Rate Decision",
    "BoE Interest Rate Decision",
    "BoJ Interest Rate Decision",
    "PBoC LPR 1-Year Rate Decision"
]

def log_anomaly(target_table: str, symbol_or_event: str, raw_value: Any, expected_range: str, anomaly_type: str, anomaly_reason: str):
    """Grava um registo de anomalia na tabela de quarentena data_anomalies_log e emite warning no log"""
    logging.warning(f"⚠️ QUARENTENA DATA QUALITY [{anomaly_type}] {symbol_or_event}: {anomaly_reason}")
    try:
        with engine.connect() as conn:
            trans = conn.begin()
            sql = text("""
                INSERT INTO data_anomalies_log 
                (target_table, symbol_or_event, raw_value, expected_range, anomaly_type, anomaly_reason, status)
                VALUES (:target_table, :symbol_or_event, :raw_value, :expected_range, :anomaly_type, :anomaly_reason, 'PENDING')
            """)
            conn.execute(sql, {
                "target_table": target_table,
                "symbol_or_event": symbol_or_event,
                "raw_value": str(raw_value),
                "expected_range": expected_range,
                "anomaly_type": anomaly_type,
                "anomaly_reason": anomaly_reason
            })
            trans.commit()
    except Exception as e:
        logging.error(f"Erro ao gravar na tabela data_anomalies_log: {e}")

def validate_ohlc_record(record: Dict[str, Any]) -> Tuple[bool, Dict[str, Any], str]:
    """
    Valida a integridade de um registo de cotação de mercado (indicator_values).
    Retorna (is_valid, sanitized_record, error_message).
    """
    symbol = record.get("symbol")
    value = float(record.get("value", 0.0))
    open_val = float(record.get("open_val", value))
    high_val = float(record.get("high_val", value))
    low_val = float(record.get("low_val", value))

    # 1. Ajuste Automático de Escala de Yields (^TNX, ^TYX) se detetada divisão incorreta (Yahoo Finance scale issue)
    if symbol in ["^TNX", "^TYX"]:
        if value < 0.1:
            corrected_value = value * 100.0
            logging.info(f"🔧 Correção automática de escala em {symbol} (x100): {value} -> {corrected_value}%")
            value = corrected_value
            record["value"] = value
        elif 0.1 <= value < 0.5:
            corrected_value = value * 10.0
            logging.info(f"🔧 Correção automática de escala em {symbol} (x10): {value} -> {corrected_value}%")
            value = corrected_value
            record["value"] = value

    # 2. Verificação de Plausibilidade Rígida
    if symbol in PLAUSIBILITY_LIMITS:
        limits = PLAUSIBILITY_LIMITS[symbol]
        if not (limits["min"] <= value <= limits["max"]):
            msg = f"Valor {value} fora do intervalo de plausibilidade esperado [{limits['min']}, {limits['max']}]"
            log_anomaly("indicator_values", symbol, value, f"[{limits['min']}, {limits['max']}]", "OUTOFBOUNDS_PLAUSIBILITY", msg)
            return False, record, msg

    # 3. Regra Matemática Universal OHLC (low <= open/close <= high)
    sanitized_high = max(high_val, open_val, value)
    sanitized_low = min(low_val, open_val, value)

    if sanitized_high != high_val or sanitized_low != low_val:
        msg = f"Sanitização OHLC efetuada em {symbol}: Open={open_val}, High={high_val}->{sanitized_high}, Low={low_val}->{sanitized_low}, Close={value}"
        logging.info(msg)
        record["high_val"] = sanitized_high
        record["low_val"] = sanitized_low

    # 4. Detetor de Spikes por Classe de Ativo (Comparação com o registo anterior na DB)
    try:
        with engine.connect() as conn:
            sql = text("SELECT value FROM indicator_values WHERE symbol = :symbol ORDER BY timestamp DESC LIMIT 1")
            prev = conn.execute(sql, {"symbol": symbol}).fetchone()
            if prev and prev[0]:
                prev_val = float(prev[0])
                if prev_val > 0:
                    pct_change = abs(value - prev_val) / prev_val
                    threshold = SPIKE_THRESHOLDS.get(symbol, SPIKE_THRESHOLDS["default"])
                    if pct_change > threshold:
                        msg = f"Spike de variação diária de {pct_change*100:.2f}% excede o limiar tolerado de {threshold*100:.1f}% (Anterior={prev_val}, Atual={value})"
                        log_anomaly("indicator_values", symbol, value, f"Spike Max {threshold*100}%", "PERCENT_SPIKE", msg)
                        return False, record, msg
    except Exception as e:
        logging.debug(f"Não foi possível comparar spike anterior para {symbol}: {e}")

    return True, record, "OK"

def validate_economic_calendar_records(records: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Valida o conjunto de eventos do calendário económico.
    Aplica as regras de cluster de datas e filtro de source provider.
    Retorna (valid_records, rejected_records).
    """
    valid = []
    rejected = []
    
    cb_dates = {}
    high_impact_counts = {}

    for r in records:
        event_name = r.get("event_name", "")
        event_timestamp = str(r.get("event_timestamp", ""))
        event_date = event_timestamp.split(" ")[0] if " " in event_timestamp else event_timestamp.split("T")[0]
        impact = r.get("impact_level", "HIGH")
        source = r.get("source_provider", "")

        # 1. Filtro Anti-Mock Silencioso
        if source in ["SYSTEM_FEED", "MOCK_UNVERIFIED"]:
            msg = f"Evento {event_name} rejeitado por conter provider genérico não verificado ({source})"
            log_anomaly("economic_calendar", event_name, source, "VALID_PROVIDER", "UNVERIFIED_MOCK", msg)
            rejected.append(r)
            continue

        # 2. Regra Concreta de Cluster de Bancos Centrais (Fed/BCE/BoE/BoJ/PBoC não coincidem no mesmo dia)
        if any(cb_name in event_name for cb_name in CENTRAL_BANK_EVENTS):
            if event_date in cb_dates and cb_dates[event_date] != event_name:
                msg = f"Cluster de Bancos Centrais detetado: {event_name} e {cb_dates[event_date]} agendados para a mesma data ({event_date})"
                log_anomaly("economic_calendar", event_name, event_date, "UNIQUE_CB_DATE", "CALENDAR_CLUSTER", msg)
                rejected.append(r)
                continue
            cb_dates[event_date] = event_name

        # 3. Limite de Eventos HIGH Impact no mesmo timestamp
        if impact == "HIGH":
            high_impact_counts[event_date] = high_impact_counts.get(event_date, 0) + 1
            if high_impact_counts[event_date] > 5:
                msg = f"Cluster excessivo de {high_impact_counts[event_date]} eventos HIGH impact na mesma data ({event_date})"
                log_anomaly("economic_calendar", event_name, event_date, "MAX_5_HIGH_PER_DAY", "CALENDAR_CLUSTER", msg)
                rejected.append(r)
                continue

        valid.append(r)

    return valid, rejected
