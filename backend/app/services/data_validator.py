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
    "MOVE": {"min": 20.0, "max": 300.0},
    "^TNX": {"min": 0.5, "max": 10.0},
    "^TYX": {"min": 0.5, "max": 10.0},
    "^IRX": {"min": 0.1, "max": 10.0},
    "T10YIE": {"min": 0.1, "max": 8.0},
    "IRLTLT01ITM156N": {"min": 0.1, "max": 12.0},
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
    "CC=F": {"min": 1000.0, "max": 15000.0},
    "^GSPC": {"min": 2000.0, "max": 10000.0},
    "^NDX": {"min": 5000.0, "max": 40000.0},

    "^STOXX50E": {"min": 2000.0, "max": 8000.0},
    "^GDAXI": {"min": 8000.0, "max": 30000.0},
    "DX-Y.NYB": {"min": 80.0, "max": 120.0},
    "O": {"min": 20.0, "max": 150.0},
    "DAL": {"min": 15.0, "max": 120.0},
    "F": {"min": 5.0, "max": 40.0},
    "ENPH": {"min": 15.0, "max": 400.0},
    "NKE": {"min": 30.0, "max": 200.0},
    "STLA": {"min": 3.0, "max": 40.0},
    "NVDA": {"min": 20.0, "max": 500.0},
    "TSM": {"min": 20.0, "max": 500.0},
    "ASML": {"min": 100.0, "max": 2000.0},
    "BABA": {"min": 20.0, "max": 400.0},
    "BBVA": {"min": 2.0, "max": 30.0},
    "JPM": {"min": 50.0, "max": 400.0},
    "MU": {"min": 15.0, "max": 1500.0}
}

# 2. Limiares de Spikes por Classe de Ativo (Evita Alert Fatigue)
SPIKE_THRESHOLDS = {
    "^VIX": 0.30,        # 30% - VIX oscila fortemente em momentos de pânico real
    "MOVE": 0.25,        # 25% - MOVE Index volatilidade de obrigações
    "^KS11": 0.20,       # 20% - Kospi Index em recuperações pós-decisão de política
    "BZ=F": 0.12,        # 12% - Petróleo Brent afetado por choques geopolíticos
    "CL=F": 0.12,        # 12% - Petróleo WTI
    "CC=F": 0.15,        # 15% - Futuros de Cacau
    "GC=F": 0.08,        # 8%  - Ouro
    "^TNX": 0.08,        # 8%  - Bond Yields em variação % diária
    "^TYX": 0.08,        # 8%  - Bond Yields 30Y
    "DX-Y.NYB": 0.03,    # 3%  - DXY raramente move mais de 3% num único dia
    "EURUSD=X": 0.02,    # 2.0% - Major Forex
    "GBPUSD=X": 0.02,    # 2.0%
    "USDJPY=X": 0.035,   # 3.5% - Intervenções do Banco do Japão (BoJ) produzem movimentos reais
    "default": 0.15      # 15% default para ações e ETFs
}


def auto_resolve_anomalies(target_table: str, symbol_or_event: str):
    """
    Auto-resolução de anomalias pendentes: quando um novo registo válido é promovido
    para a produção, as anomalias pendentes desse símbolo/evento são marcadas como RESOLVED_APPROVED.
    Mapeia também variações do nome do evento (ex: 'US Core CPI (MoM)' -> 'US Core CPI').
    """
    clean_symbol = symbol_or_event.split(" (")[0].strip() if " (" in symbol_or_event else symbol_or_event.strip()
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                UPDATE data_anomalies_log 
                SET status = 'RESOLVED_APPROVED', 
                    last_seen = NOW()
                WHERE target_table = :target_table 
                  AND (
                    symbol_or_event = :symbol_or_event 
                    OR symbol_or_event = :clean_symbol
                    OR symbol_or_event LIKE CONCAT(:clean_symbol, '%')
                  )
                  AND status = 'PENDING'
            """), {"target_table": target_table, "symbol_or_event": symbol_or_event, "clean_symbol": clean_symbol})
    except Exception as e:
        logging.debug(f"Não foi possível auto-resolver anomalias para {symbol_or_event}: {e}")

CENTRAL_BANK_EVENTS = [
    "Fed Interest Rate Decision",
    "ECB Interest Rate Decision",
    "BoE Interest Rate Decision",
    "BoJ Interest Rate Decision",
    "PBoC LPR 1-Year Rate Decision"
]

def log_anomaly(target_table: str, symbol_or_event: str, raw_value: Any, expected_range: str, anomaly_type: str, anomaly_reason: str):
    """Grava ou deduplica um registo de anomalia na quarentena data_anomalies_log com escalonamento urgente ao atingir 5 repetições"""
    logging.warning(f"⚠️ QUARENTENA DATA QUALITY [{anomaly_type}] {symbol_or_event}: {anomaly_reason}")
    try:
        with engine.connect() as conn:
            trans = conn.begin()
            # 1. Verificar se já existe uma anomalia PENDING igual (Deduplicação)
            check_sql = text("""
                SELECT id, occurrences FROM data_anomalies_log 
                WHERE target_table = :target_table 
                  AND symbol_or_event = :symbol_or_event 
                  AND anomaly_type = :anomaly_type 
                  AND status = 'PENDING'
                LIMIT 1
            """)
            existing = conn.execute(check_sql, {
                "target_table": target_table,
                "symbol_or_event": symbol_or_event,
                "anomaly_type": anomaly_type
            }).fetchone()

            if existing:
                anomaly_id, occurrences = existing[0], existing[1] + 1
                new_status = 'ESCALATED_RECURRING' if occurrences >= 5 else 'PENDING'
                update_sql = text("""
                    UPDATE data_anomalies_log 
                    SET occurrences = :occurrences, repeat_count = :occurrences, raw_value = :raw_value, 
                        anomaly_reason = :anomaly_reason, status = :status, last_seen = NOW()
                    WHERE id = :id
                """)
                conn.execute(update_sql, {
                    "id": anomaly_id,
                    "occurrences": occurrences,
                    "raw_value": str(raw_value),
                    "anomaly_reason": anomaly_reason,
                    "status": new_status
                })
                logging.info(f"🔄 Anomalia deduplicada para {symbol_or_event} (Ocorrências: {occurrences}, Status: {new_status})")
                
                # Regra de Escalonamento Urgente (ESCALATION_THRESHOLD = 5)
                if occurrences >= 5:
                    urgent_msg = f"🚨 ESCALATED_RECURRING: [{symbol_or_event}] falhou {occurrences} vezes consecutivas — requer intervenção urgente na origem."
                    logging.error(urgent_msg)
                    try:
                        from app.services.alert_service import send_alert_notification
                        send_alert_notification(urgent_msg)
                    except Exception:
                        pass
            else:
                # Inserir nova anomalia com occurrences = 1, first_seen = NOW(), last_seen = NOW()
                insert_sql = text("""
                    INSERT INTO data_anomalies_log 
                    (target_table, symbol_or_event, raw_value, expected_range, anomaly_type, anomaly_reason, status, occurrences, repeat_count, first_seen, last_seen)
                    VALUES (:target_table, :symbol_or_event, :raw_value, :expected_range, :anomaly_type, :anomaly_reason, 'PENDING', 1, 1, NOW(), NOW())
                """)
                conn.execute(insert_sql, {
                    "target_table": target_table,
                    "symbol_or_event": symbol_or_event,
                    "raw_value": str(raw_value),
                    "expected_range": expected_range,
                    "anomaly_type": anomaly_type,
                    "anomaly_reason": anomaly_reason
                })
                trans.commit()
                
                # Disparar notificação imediata por email de entrada em quarentena
                try:
                    from app.services.alert_service import send_alert_notification
                    quarantine_alert = f"⚠️ NOVO DADO EM QUARENTENA: [{symbol_or_event}] | Tipo: {anomaly_type} | Motivo: {anomaly_reason}"
                    send_alert_notification(quarantine_alert)
                except Exception as alert_err:
                    logging.warning(f"Não foi possível enviar email de alerta de quarentena: {alert_err}")
    except Exception as e:
        logging.error(f"Erro ao gravar na tabela data_anomalies_log: {e}")


def validate_ohlc_record(record: Dict[str, Any]) -> Tuple[bool, Dict[str, Any], str]:
    """
    Valida a integridade de um registo de cotação de mercado (indicator_values).
    Retorna (is_valid, sanitized_record, error_message).
    """
    symbol = record.get("symbol")
    value = float(record.get("value", 0.0))
    open_val = float(record.get("open_val", value)) if record.get("open_val") is not None else value
    high_val = float(record.get("high_val", value)) if record.get("high_val") is not None else value
    low_val = float(record.get("low_val", value)) if record.get("low_val") is not None else value

    # 1. Verificação de Plausibilidade Rígida (Sem mutação silenciosa de escala - Regra estrita do Consultor 1)
    if symbol in PLAUSIBILITY_LIMITS:
        limits = PLAUSIBILITY_LIMITS[symbol]
        if not (limits["min"] <= value <= limits["max"]):
            msg = f"Valor lido {value} fora do intervalo de plausibilidade esperado [{limits['min']}, {limits['max']}]. Possível anomalia de escala da fonte (requer revisão manual)."
            log_anomaly("indicator_values", symbol, value, f"[{limits['min']}, {limits['max']}]", "OUTOFBOUNDS_PLAUSIBILITY", msg)
            return False, record, msg

    # 3. Regra Matemática Universal OHLC (low <= min(open, close) <= max(open, close) <= high)
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


def promover_para_producao(
    ticker: str, 
    data: str, 
    novo_open: Optional[float], 
    novo_high: Optional[float], 
    novo_low: Optional[float], 
    novo_close: float, 
    volume: int = 0,
    novo_adj_close: Optional[float] = None
) -> Dict[str, Any]:
    """
    Promove uma cotação para a tabela de produção `indicator_values` com lógica de Upsert.
    Garante integridade matemática estrita de OHLC (High >= max(Open, Close) e Low <= min(Open, Close)).
    Garante que se uma abertura sintética (O=H=L=C) for inserida inicialmente, pode ser atualizada por um Open real de mercado.
    """
    ts = str(data)[:10] + " 00:00:00" if len(str(data)) <= 10 else str(data)
    
    try:
        with engine.connect() as conn:
            trans = conn.begin()
            try:
                cat_row = conn.execute(text("SELECT id FROM indicators_catalog WHERE ticker = :ticker LIMIT 1"), {"ticker": ticker}).fetchone()
                if not cat_row:
                    trans.rollback()
                    return {"status": "error", "message": f"Ticker {ticker} não encontrado no catálogo."}
                indicator_id = cat_row[0]

                query_prod = text("SELECT id, open_val, high_val, low_val, value, volume, adj_close FROM indicator_values WHERE symbol = :symbol AND timestamp = :ts")
                existing = conn.execute(query_prod, {"symbol": ticker, "ts": ts}).fetchone()

                novo_open_f = float(novo_open) if novo_open is not None else float(novo_close)
                novo_high_f = float(novo_high) if novo_high is not None else float(novo_close)
                novo_low_f = float(novo_low) if novo_low is not None else float(novo_close)
                novo_close_f = float(novo_close)
                novo_adj_close_f = float(novo_adj_close) if novo_adj_close is not None else None

                if existing:
                    ext_id, ext_open, ext_high, ext_low, ext_val, ext_vol, ext_adj = existing
                    
                    ext_open_f = float(ext_open) if ext_open is not None else novo_open_f
                    ext_high_f = float(ext_high) if ext_high is not None else novo_high_f
                    ext_low_f = float(ext_low) if ext_low is not None else novo_low_f
                    ext_val_f = float(ext_val) if ext_val is not None else novo_close_f

                    # Deteção de Abertura Sintética Provisória (O = H = L = C na inserção original)
                    is_prev_synthetic = (ext_open_f == ext_high_f == ext_low_f == ext_val_f)
                    is_new_real = (novo_open_f != novo_close_f or novo_high_f != novo_low_f)

                    # Se a abertura gravada era sintética e agora recebemos dados reais de mercado, atualizamos o Open!
                    if is_prev_synthetic and is_new_real:
                        open_final = novo_open_f
                        logging.info(f"🔄 Abertura sintética de {ticker} ({ext_open_f}) atualizada para Open real de mercado ({novo_open_f}).")
                    else:
                        open_final = ext_open_f

                    close_final = novo_close_f
                    adj_close_final = novo_adj_close_f if novo_adj_close_f is not None else (float(ext_adj) if ext_adj is not None else None)
                    
                    # Regra Matemática Absoluta: High é sempre o máximo de todos os pontos conhecidos da sessão
                    raw_high = max(ext_high_f, novo_high_f)
                    high_final = max(raw_high, open_final, close_final)
                    
                    # Regra Matemática Absoluta: Low é sempre o mínimo de todos os pontos conhecidos da sessão
                    raw_low = min(ext_low_f, novo_low_f) if (ext_low_f > 0 and novo_low_f > 0) else (novo_low_f if novo_low_f > 0 else ext_low_f)
                    low_final = min(raw_low, open_final, close_final)
                    
                    vol_final = max(int(ext_vol or 0), int(volume or 0))

                    sql_update = text("""
                        UPDATE indicator_values 
                        SET value = :value, adj_close = :adj_close, open_val = :open_val, high_val = :high_val, low_val = :low_val, volume = :volume
                        WHERE id = :id
                    """)
                    conn.execute(sql_update, {
                        "id": ext_id,
                        "value": close_final,
                        "adj_close": adj_close_final,
                        "open_val": open_final,
                        "high_val": high_final,
                        "low_val": low_final,
                        "volume": vol_final
                    })
                    trans.commit()
                    auto_resolve_anomalies("indicator_values", ticker)
                    return {
                        "status": "updated",
                        "open_val": open_final,
                        "high_val": high_final,
                        "low_val": low_final,
                        "value": close_final,
                        "adj_close": adj_close_final,
                        "volume": vol_final
                    }
                else:
                    open_final = novo_open_f
                    close_final = novo_close_f
                    high_final = max(novo_high_f, open_final, close_final)
                    low_final = min(novo_low_f, open_final, close_final)
                    adj_close_final = novo_adj_close_f

                    sql_insert = text("""
                        INSERT INTO indicator_values 
                        (indicator_id, symbol, timestamp, value, adj_close, open_val, high_val, low_val, volume)
                        VALUES (:indicator_id, :symbol, :ts, :value, :adj_close, :open_val, :high_val, :low_val, :volume)
                    """)
                    conn.execute(sql_insert, {
                        "indicator_id": indicator_id,
                        "symbol": ticker,
                        "ts": ts,
                        "value": close_final,
                        "adj_close": adj_close_final,
                        "open_val": open_final,
                        "high_val": high_final,
                        "low_val": low_final,
                        "volume": int(volume or 0)
                    })
                    trans.commit()
                    auto_resolve_anomalies("indicator_values", ticker)
                    return {
                        "status": "inserted",
                        "open_val": open_final,
                        "high_val": high_final,
                        "low_val": low_final,
                        "value": close_final,
                        "adj_close": adj_close_final,
                        "volume": int(volume or 0)
                    }
            except Exception as e:
                trans.rollback()
                logging.error(f"Erro ao promover {ticker} para produção: {e}")
                return {"status": "error", "message": str(e)}
    except Exception as e:
        logging.error(f"Erro de conexão ao promover {ticker} para produção: {e}")
        return {"status": "error", "message": str(e)}


def calcular_percentil(vix_hoje: float, historico_vix: List[float]) -> float:
    """Calcula a posição percentil (0 a 100) do valor VIX em relação ao histórico fornecido."""
    if not historico_vix:
        return 50.0
    abaixo = sum(1 for v in historico_vix if v < vix_hoje)
    iguais = sum(1 for v in historico_vix if v == vix_hoje)
    percentil = ((abaixo + 0.5 * iguais) / len(historico_vix)) * 100.0
    return max(0.0, min(100.0, percentil))


def classificar_vix_percentil(vix_hoje: float, historico_vix: List[float], minimo_sessoes: int = 60) -> Tuple[str, str]:
    """
    Classificação do Regime de VIX por Percentil (Especificação Consultor 1).
    - Janela expansível até 252 sessões.
    - Se len(historico_vix) < minimo_sessoes: Cold-Start com limiares fixos.
    - percentil < 40: "Baixa Vol"
    - percentil <= 85: "Transição"
    - percentil > 85: "Pânico"
    """
    if len(historico_vix) < minimo_sessoes:
        note = "Cold-Start (Percentil Indisponível)"
        if vix_hoje < 15.0:
            return "Baixa Vol", note
        elif vix_hoje <= 20.0:
            return "Transição", note
        else:
            return "Pânico", note

    percentil = calcular_percentil(vix_hoje, historico_vix[:252])
    label_percentil = f"Percentil {percentil:.0f}"
    
    if percentil < 40.0:
        return "Baixa Vol", label_percentil
    elif percentil <= 85.0:
        return "Transição", label_percentil
    else:
        return "Pânico", label_percentil
