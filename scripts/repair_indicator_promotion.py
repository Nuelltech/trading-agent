# scripts/repair_indicator_promotion.py
"""
Script de Reparação de Dados Congelados (Adenda URGENTE)
Promove os valores mais recentes da tabela `staging_indicator_values`
para a tabela `indicator_values` aplicando a lógica de Upsert:
  - open_val é preservado (nunca alterado)
  - high_val = max(existente, novo)
  - low_val = min(existente, novo)
  - close_val (value) = novo (mais recente)
"""

import sys
import logging
from sqlalchemy import text

sys.path.append('backend')
from app.database import engine
from app.services.data_validator import validate_ohlc_record, promover_para_producao

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


def repair_frozen_indicators():
    logging.info("🛠️ Iniciando Reparação de Dados em Produção (staging → indicator_values)...")
    
    with engine.connect() as conn:
        # Puxa o registo de staging mais recente de cada (symbol, DATE(timestamp))
        sql = text("""
            SELECT s.symbol, DATE(s.timestamp) as session_date, s.open_val, s.high_val, s.low_val, s.value, s.volume
            FROM staging_indicator_values s
            INNER JOIN (
                SELECT symbol, DATE(timestamp) as session_date, MAX(id) as max_id
                FROM staging_indicator_values
                GROUP BY symbol, DATE(timestamp)
            ) latest ON s.id = latest.max_id
            ORDER BY session_date DESC, s.symbol ASC
        """)
        rows = conn.execute(sql).fetchall()
        logging.info(f"📊 {len(rows)} registos mais recentes extraídos de staging_indicator_values.")

        repaired_count = 0
        skipped_count = 0
        error_count = 0

        for r in rows:
            symbol = r[0]
            session_date = str(r[1])
            rec = {
                "symbol": symbol,
                "timestamp": f"{session_date} 00:00:00",
                "open_val": float(r[2] or 0),
                "high_val": float(r[3] or 0),
                "low_val": float(r[4] or 0),
                "value": float(r[5] or 0),
                "volume": int(r[6] or 0)
            }

            # Valida através do Data Quality Engine
            is_valid, sanitized_rec, err_msg = validate_ohlc_record(rec)
            if not is_valid:
                logging.warning(f"⚠️ [{symbol}] {session_date} ignorado por validação: {err_msg}")
                skipped_count += 1
                continue

            res = promover_para_producao(
                ticker=sanitized_rec["symbol"],
                data=sanitized_rec["timestamp"],
                novo_open=sanitized_rec["open_val"],
                novo_high=sanitized_rec["high_val"],
                novo_low=sanitized_rec["low_val"],
                novo_close=sanitized_rec["value"],
                volume=sanitized_rec.get("volume", 0)
            )

            if res.get("status") in ["inserted", "updated"]:
                repaired_count += 1
                logging.info(f"✅ [{symbol}] {session_date}: Status={res['status']} | High={res['high_val']} | Close={res['value']}")
            else:
                error_count += 1
                logging.error(f"❌ [{symbol}] {session_date} erro: {res.get('message')}")

        logging.info(f"🎉 Reparação concluída: {repaired_count} promovidos/atualizados | {skipped_count} quarentena | {error_count} erros.")


if __name__ == "__main__":
    repair_frozen_indicators()
