# backend/app/services/data_validator.py
"""
Data Quality Engine & Pipeline Validation Service
Implementa os 6 pilares de integridade de dados e as especificações técnicas da Versão 1.0.
"""

import logging
from typing import Dict, Any, List, Tuple
from datetime import datetime
from sqlalchemy import text
from app.database import engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# 1. Limites Rígidos de Plausibilidade por Ticker (Boundary Check)
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
    "GC=F": {"min": 1000.0, "max": 6000.0},
    "CL=F": {"min": 25.0, "max": 140.0},
    "BZ=F": {"min": 30.0, "max": 150.0},
    "HG=F": {"min": 2.0, "max": 10.0},
    "^GSPC": {"min": 2000.0, "max": 10000.0},
    "^NDX": {"min": 5000.0, "max": 30000.0},
    "^STOXX50E": {"min": 2000.0, "max": 8000.0},
    "^GDAXI": {"min": 8000.0, "max": 30000.0},
    "DX-Y.NYB": {"min": 80.0, "max": 120.0},
    "O": {"min": 20.0, "max": 150.0},
    "DAL": {"min": 15.0, "max": 120.0},
    "F": {"min": 5.0, "max": 40.0},
    "ENPH": {"min": 15.0, "max": 400.0},
    "NKE": {"min": 30.0, "max": 200.0},
    "STLA": {"min": 3.0, "max": 40.0}
}

# 2. Limiares de Spikes por Classe de Ativo (Evita Alert Fatigue)
SPIKE_THRESHOLDS = {
    "^VIX": 0.30,        # 30% - VIX oscila fortemente em momentos de pânico real
    "BZ=F": 0.12,        # 12% - Petróleo Brent afetado por choques geopolíticos
    "CL=F": 0.12,        # 12% - Petróleo WTI
    "GC=F": 0.08,        # 8%  - Ouro
    "^TNX": 0.08,        # 8%  - Bond Yields em variação % diária
    "^TYX": 0.08,        # 8%  - Bond Yields 30Y
    "DX-Y.NYB": 0.03,    # 3%  - DXY raramente move mais de 3% num único dia
    "EURUSD=X": 0.02,    # 2.0% - Major Forex
    "GBPUSD=X": 0.02,    # 2.0%
    "USDJPY=X": 0.02,    # 2.0%
    "default": 0.15      # 15% default para ações e ETFs
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

    # 1. Verificação de Plausibilidade Rígida (Sem mutação silenciosa de escala - Regra estrita do Consultor 1)
    if symbol in PLAUSIBILITY_LIMITS:
        limits = PLAUSIBILITY_LIMITS[symbol]
        if not (limits["min"] <= value <= limits["max"]):
            msg = f"Valor lido {value} fora do intervalo de plausibilidade esperado [{limits['min']}, {limits['max']}]. Possível anomalia de escala da fonte (requer revisão manual)."
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
    Aplica as regras de cluster de datas, filtro de source provider, limite de 3 eventos HIGH por dia,
    e verificação de actual_val em datas futuras.
    Retorna (valid_records, rejected_records).
    """
    valid = []
    rejected = []
    
    cb_dates = {}
    high_impact_counts = {}
    now = datetime.utcnow()

    for r in records:
        event_name = r.get("event_name", "")
        event_timestamp_str = str(r.get("event_timestamp", ""))
        event_date = event_timestamp_str.split(" ")[0] if " " in event_timestamp_str else event_timestamp_str.split("T")[0]
        impact = r.get("impact_level", "HIGH")
        source = r.get("source_provider", "")
        actual_val = r.get("actual_val")

        # 1. Filtro Anti-Mock Silencioso (Proibido MOCK% / UNVERIFIED%)
        if "MOCK" in source.upper() or "UNVERIFIED" in source.upper() or source == "SYSTEM_FEED":
            msg = f"Evento {event_name} rejeitado por conter provider genérico não verificado ({source})"
            log_anomaly("economic_calendar", event_name, source, "VALID_PROVIDER", "UNVERIFIED_MOCK", msg)
            rejected.append(r)
            continue

        # 2. Verificação de actual_val em Datas Futuras (deve ser NULL para eventos no futuro)
        try:
            event_dt = datetime.strptime(event_timestamp_str, "%Y-%m-%d %H:%M:%S") if " " in event_timestamp_str else datetime.strptime(event_timestamp_str, "%Y-%m-%d")
            if event_dt > now and actual_val is not None:
                msg = f"Evento futuro {event_name} ({event_timestamp_str}) possui actual_val preenchido ({actual_val}) em vez de NULL"
                log_anomaly("economic_calendar", event_name, str(actual_val), "NULL_FOR_FUTURE", "OUTOFBOUNDS_PLAUSIBILITY", msg)
                rejected.append(r)
                continue
        except Exception:
            pass

        # 3. Regra Concreta de Cluster de Bancos Centrais (Nenhum par de Fed/BCE/BoE/BoJ/PBoC pode coincidir no mesmo dia)
        if any(cb_name in event_name for cb_name in CENTRAL_BANK_EVENTS):
            if event_date in cb_dates and cb_dates[event_date] != event_name:
                msg = f"Cluster de Bancos Centrais detetado: {event_name} e {cb_dates[event_date]} agendados para a mesma data ({event_date})"
                log_anomaly("economic_calendar", event_name, event_date, "UNIQUE_CB_DATE", "CALENDAR_CLUSTER", msg)
                rejected.append(r)
                continue
            cb_dates[event_date] = event_name

        # 4. Máximo de 3 Eventos HIGH Impact no mesmo dia (Secção 2.5 da Spec)
        if impact == "HIGH":
            high_impact_counts[event_date] = high_impact_counts.get(event_date, 0) + 1
            if high_impact_counts[event_date] > 3:
                msg = f"Cluster excessivo de {high_impact_counts[event_date]} eventos HIGH impact na mesma data ({event_date})"
                log_anomaly("economic_calendar", event_name, event_date, "MAX_3_HIGH_PER_DAY", "CALENDAR_CLUSTER", msg)
                rejected.append(r)
                continue

        valid.append(r)

    return valid, rejected
