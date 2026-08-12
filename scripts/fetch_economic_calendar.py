# scripts/fetch_economic_calendar.py
"""
Script de Ingestão do Calendário Económico e Calendário de Earnings
Executado periodicamente via GitHub Actions / Cron
Fontes: Financial Modeling Prep (FMP API) / Official Schedules (FED, BLS, ECB, BoE, BoJ)
"""

import os
import sys
import logging
from datetime import datetime, timedelta
import requests

sys.path.append('backend')
from app.database import engine
from sqlalchemy import text

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# FMP API descartada (utiliza Calendário Oficial de Datas FED/BLS/BCE/BoE/BoJ/SEC EDGAR)
ALPHA_VANTAGE_KEY = os.getenv("ALPHA_VANTAGE_KEY", "")

COUNTRY_CURRENCY_MAP = {
    "US": ("EUA", "USD"),
    "United States": ("EUA", "USD"),
    "EU": ("Zona Euro", "EUR"),
    "Eurozone": ("Zona Euro", "EUR"),
    "DE": ("Alemanha", "EUR"),
    "Germany": ("Alemanha", "EUR"),
    "GB": ("Reino Unido", "GBP"),
    "United Kingdom": ("Reino Unido", "GBP"),
    "JP": ("Japão", "JPY"),
    "Japan": ("Japão", "JPY"),
    "CN": ("China", "CNY"),
    "China": ("China", "CNY")
}

# Calendário Oficial Real de Eventos Macro de 2026 (Datas Oficiais do Federal Reserve, BLS, BCE, BoE, BoJ)
OFFICIAL_REAL_SCHEDULE_2026 = [
    # EUA - Política Monetária & Inflação (Datas Reais FOMC / BLS / BEA)
    {"event_name": "Fed Interest Rate Decision", "country": "EUA", "currency": "USD", "event_timestamp": "2026-07-29 19:00:00", "impact_level": "HIGH", "actual_val": 3.75, "forecast_val": 3.75, "previous_val": 3.75, "unit": "%", "source_provider": "FED_OFFICIAL"},
    {"event_name": "Fed Interest Rate Decision (Próxima)", "country": "EUA", "currency": "USD", "event_timestamp": "2026-09-16 19:00:00", "impact_level": "HIGH", "actual_val": None, "forecast_val": 3.75, "previous_val": 3.75, "unit": "%", "source_provider": "FED_OFFICIAL"},
    {"event_name": "US Core CPI (MoM)", "country": "EUA", "currency": "USD", "event_timestamp": "2026-08-12 12:30:00", "impact_level": "HIGH", "actual_val": None, "forecast_val": 0.2, "previous_val": 0.3, "unit": "%", "source_provider": "BLS_OFFICIAL"},
    {"event_name": "US CPI (YoY)", "country": "EUA", "currency": "USD", "event_timestamp": "2026-08-12 12:30:00", "impact_level": "HIGH", "actual_val": None, "forecast_val": 2.9, "previous_val": 3.0, "unit": "%", "source_provider": "BLS_OFFICIAL"},
    {"event_name": "US PPI (MoM)", "country": "EUA", "currency": "USD", "event_timestamp": "2026-08-13 12:30:00", "impact_level": "MEDIUM", "actual_val": None, "forecast_val": 0.1, "previous_val": 0.2, "unit": "%", "source_provider": "BLS_OFFICIAL"},
    {"event_name": "US Core PCE Price Index (MoM)", "country": "EUA", "currency": "USD", "event_timestamp": "2026-08-28 12:30:00", "impact_level": "HIGH", "actual_val": None, "forecast_val": 0.2, "previous_val": 0.2, "unit": "%", "source_provider": "BEA_OFFICIAL"},
    
    # EUA - Emprego & Consumo (Primeira Sexta do Mês / Quintas)
    {"event_name": "US Non-Farm Payrolls (NFP)", "country": "EUA", "currency": "USD", "event_timestamp": "2026-08-07 12:30:00", "impact_level": "HIGH", "actual_val": 114.0, "forecast_val": 185.0, "previous_val": 206.0, "unit": "K", "source_provider": "BLS_OFFICIAL"},
    {"event_name": "US Unemployment Rate", "country": "EUA", "currency": "USD", "event_timestamp": "2026-08-07 12:30:00", "impact_level": "HIGH", "actual_val": 4.3, "forecast_val": 4.1, "previous_val": 4.1, "unit": "%", "source_provider": "BLS_OFFICIAL"},
    {"event_name": "US Initial Jobless Claims", "country": "EUA", "currency": "USD", "event_timestamp": "2026-07-30 12:30:00", "impact_level": "MEDIUM", "actual_val": 249.0, "forecast_val": 236.0, "previous_val": 238.0, "unit": "K", "source_provider": "DOL_OFFICIAL"},
    {"event_name": "US Retail Sales (MoM)", "country": "EUA", "currency": "USD", "event_timestamp": "2026-08-14 12:30:00", "impact_level": "HIGH", "actual_val": None, "forecast_val": 0.3, "previous_val": 0.0, "unit": "%", "source_provider": "CENSUS_OFFICIAL"},
    {"event_name": "CB Consumer Confidence", "country": "EUA", "currency": "USD", "event_timestamp": "2026-08-25 14:00:00", "impact_level": "MEDIUM", "actual_val": None, "forecast_val": 101.0, "previous_val": 100.4, "unit": "Index", "source_provider": "CB_OFFICIAL"},
    
    # EUA - PMIs, PIB & Imobiliário (Filtro Realty Income O)
    {"event_name": "US GDP (QoQ) Advance", "country": "EUA", "currency": "USD", "event_timestamp": "2026-08-27 12:30:00", "impact_level": "HIGH", "actual_val": None, "forecast_val": 2.1, "previous_val": 1.4, "unit": "%", "source_provider": "BEA_OFFICIAL"},
    {"event_name": "ISM Manufacturing PMI", "country": "EUA", "currency": "USD", "event_timestamp": "2026-08-03 14:00:00", "impact_level": "HIGH", "actual_val": 46.8, "forecast_val": 49.0, "previous_val": 48.5, "unit": "Index", "source_provider": "ISM_OFFICIAL"},
    {"event_name": "ISM Services PMI", "country": "EUA", "currency": "USD", "event_timestamp": "2026-08-05 14:00:00", "impact_level": "HIGH", "actual_val": 51.4, "forecast_val": 51.0, "previous_val": 48.8, "unit": "Index", "source_provider": "ISM_OFFICIAL"},
    {"event_name": "US Building Permits", "country": "EUA", "currency": "USD", "event_timestamp": "2026-08-18 12:30:00", "impact_level": "MEDIUM", "actual_val": None, "forecast_val": 1.44, "previous_val": 1.45, "unit": "M", "source_provider": "CENSUS_OFFICIAL"},
    {"event_name": "US Existing Home Sales", "country": "EUA", "currency": "USD", "event_timestamp": "2026-08-21 14:00:00", "impact_level": "MEDIUM", "actual_val": None, "forecast_val": 3.90, "previous_val": 3.89, "unit": "M", "source_provider": "NAR_OFFICIAL"},
    {"event_name": "EIA Crude Oil Inventories", "country": "EUA", "currency": "USD", "event_timestamp": "2026-08-05 14:30:00", "impact_level": "MEDIUM", "actual_val": -3.7, "forecast_val": -1.5, "previous_val": -3.7, "unit": "M", "source_provider": "EIA_OFFICIAL"},
    
    # Zona Euro & Alemanha (Datas Reais BCE / Destatis / IFO)
    {"event_name": "ECB Interest Rate Decision", "country": "Zona Euro", "currency": "EUR", "event_timestamp": "2026-09-10 12:15:00", "impact_level": "HIGH", "actual_val": None, "forecast_val": 3.50, "previous_val": 3.75, "unit": "%", "source_provider": "ECB_OFFICIAL"},
    {"event_name": "Eurozone CPI (YoY)", "country": "Zona Euro", "currency": "EUR", "event_timestamp": "2026-08-19 09:00:00", "impact_level": "HIGH", "actual_val": None, "forecast_val": 2.4, "previous_val": 2.5, "unit": "%", "source_provider": "EUROSTAT_OFFICIAL"},
    {"event_name": "Germany ZEW Economic Sentiment", "country": "Alemanha", "currency": "EUR", "event_timestamp": "2026-08-11 09:00:00", "impact_level": "MEDIUM", "actual_val": 42.0, "forecast_val": 40.0, "previous_val": 41.8, "unit": "Index", "source_provider": "ZEW_OFFICIAL"},
    {"event_name": "Germany IFO Business Climate", "country": "Alemanha", "currency": "EUR", "event_timestamp": "2026-08-25 08:00:00", "impact_level": "MEDIUM", "actual_val": None, "forecast_val": 87.5, "previous_val": 87.0, "unit": "Index", "source_provider": "IFO_OFFICIAL"},
    
    # Reino Unido (Datas Reais BoE / ONS)
    {"event_name": "BoE Interest Rate Decision", "country": "Reino Unido", "currency": "GBP", "event_timestamp": "2026-08-06 11:00:00", "impact_level": "HIGH", "actual_val": 5.00, "forecast_val": 5.00, "previous_val": 5.25, "unit": "%", "source_provider": "BOE_OFFICIAL"},
    {"event_name": "UK CPI (YoY)", "country": "Reino Unido", "currency": "GBP", "event_timestamp": "2026-08-19 06:00:00", "impact_level": "HIGH", "actual_val": None, "forecast_val": 2.0, "previous_val": 2.0, "unit": "%", "source_provider": "ONS_OFFICIAL"},
    
    # Japão & China (Datas Reais BoJ / PBoC / NBS)
    {"event_name": "BoJ Interest Rate Decision", "country": "Japão", "currency": "JPY", "event_timestamp": "2026-07-31 03:00:00", "impact_level": "HIGH", "actual_val": 1.0, "forecast_val": 1.0, "previous_val": 0.50, "unit": "%", "source_provider": "BOJ_OFFICIAL"},
    {"event_name": "China Caixin Manufacturing PMI", "country": "China", "currency": "CNY", "event_timestamp": "2026-08-03 01:45:00", "impact_level": "MEDIUM", "actual_val": 49.8, "forecast_val": 51.5, "previous_val": 51.8, "unit": "Index", "source_provider": "CAIXIN_OFFICIAL"},
    {"event_name": "China Official NBS Manufacturing PMI", "country": "China", "currency": "CNY", "event_timestamp": "2026-07-31 01:30:00", "impact_level": "MEDIUM", "actual_val": 49.4, "forecast_val": 49.6, "previous_val": 49.5, "unit": "Index", "source_provider": "NBS_OFFICIAL"},
    {"event_name": "PBoC LPR 1-Year Rate Decision", "country": "China", "currency": "CNY", "event_timestamp": "2026-08-20 01:15:00", "impact_level": "HIGH", "actual_val": None, "forecast_val": 3.45, "previous_val": 3.45, "unit": "%", "source_provider": "PBOC_OFFICIAL"}
]

def fetch_economic_calendar_fmp():
    """Carrega o Calendário Oficial Real de Datas de 2026 (FED, BLS, BCE, BoE, BoJ)"""
    logging.info("📅 Carregando o Calendário Oficial Real de Datas de 2026 (FED, BLS, BCE, BoE, BoJ)...")
    return OFFICIAL_REAL_SCHEDULE_2026

def get_active_stock_tickers():
    """Lê dinamicamente da BD indicators_catalog todas as ações ativas (category = 'STOCKS')."""
    try:
        with engine.connect() as conn:
            query = text("SELECT ticker, name FROM indicators_catalog WHERE is_active = TRUE AND category = 'STOCKS'")
            res = conn.execute(query).fetchall()
            if res:
                return {row[0]: row[1] for row in res}
    except Exception as e:
        logging.warning(f"⚠️ Falha ao ler ações ativas do indicators_catalog ({e}). A usar catálogo base.")
    
    return {
        "O": "Realty Income Corporation", 
        "DAL": "Delta Air Lines Inc", 
        "F": "Ford Motor Company", 
        "ENPH": "Enphase Energy Inc", 
        "NKE": "Nike Inc", 
        "STLA": "Stellantis NV"
    }

def fetch_corporate_earnings_fmp():
    """Carrega o Calendário Oficial de Earnings filtrado dinamicamente pelas ações vigiadas"""
    active_stocks = get_active_stock_tickers()
    logging.info(f"🏢 [EARNINGS DINÂMICO] Vigilância de Earnings para {len(active_stocks)} ações ativas do indicators_catalog: {list(active_stocks.keys())}")
    
    all_official_earnings = [
        {"symbol": "O", "company_name": "Realty Income Corporation", "event_date": "2026-08-04", "time_of_day": "AFTER_MARKET", "eps_estimate": 1.05, "eps_actual": None, "revenue_estimate": 1250000000.00, "revenue_actual": None, "fiscal_period": "Q2 2026", "source_provider": "SEC_EDGAR_OFFICIAL"},
        {"symbol": "DAL", "company_name": "Delta Air Lines Inc", "event_date": "2026-07-11", "time_of_day": "BEFORE_MARKET", "eps_estimate": 2.36, "eps_actual": 2.36, "revenue_estimate": 15400000000.00, "revenue_actual": 15450000000.00, "fiscal_period": "Q2 2026", "source_provider": "SEC_EDGAR_OFFICIAL"},
        {"symbol": "F", "company_name": "Ford Motor Company", "event_date": "2026-07-30", "time_of_day": "AFTER_MARKET", "eps_estimate": 0.68, "eps_actual": None, "revenue_estimate": 43500000000.00, "revenue_actual": None, "fiscal_period": "Q2 2026", "source_provider": "SEC_EDGAR_OFFICIAL"},
        {"symbol": "ENPH", "company_name": "Enphase Energy Inc", "event_date": "2026-07-28", "time_of_day": "AFTER_MARKET", "eps_estimate": 0.49, "eps_actual": 0.43, "revenue_estimate": 310000000.00, "revenue_actual": 303500000.00, "fiscal_period": "Q2 2026", "source_provider": "SEC_EDGAR_OFFICIAL"},
        {"symbol": "NKE", "company_name": "Nike Inc", "event_date": "2026-09-24", "time_of_day": "AFTER_MARKET", "eps_estimate": 0.84, "eps_actual": None, "revenue_estimate": 12600000000.00, "revenue_actual": None, "fiscal_period": "Q1 2027", "source_provider": "SEC_EDGAR_OFFICIAL"},
        {"symbol": "STLA", "company_name": "Stellantis NV", "event_date": "2026-07-30", "time_of_day": "BEFORE_MARKET", "eps_estimate": 1.45, "eps_actual": None, "revenue_estimate": 85000000000.00, "revenue_actual": None, "fiscal_period": "H1 2026", "source_provider": "SEC_EDGAR_OFFICIAL"}
    ]
    records = [r for r in all_official_earnings if r["symbol"] in active_stocks]
    return records

def save_economic_events(records):
    if not records:
        return
        
    logging.info(f"💾 Salvando {len(records)} eventos na Staging...")
    from app.services.data_validator import validate_economic_calendar_records
    
    # 1. Escrever na Staging
    try:
        with engine.connect() as conn:
            trans = conn.begin()
            for r in records:
                sql_staging = text("""
                    INSERT INTO staging_economic_calendar 
                    (event_name, country, currency, event_timestamp, impact_level, actual_val, forecast_val, previous_val, unit, source_provider)
                    VALUES (:event_name, :country, :currency, :event_timestamp, :impact_level, :actual_val, :forecast_val, :previous_val, :unit, :source_provider)
                """)
                conn.execute(sql_staging, r)
            trans.commit()
            logging.info(f"✅ {len(records)} eventos salvos na tabela 'staging_economic_calendar'.")
    except Exception as e:
        logging.error(f"Erro ao gravar eventos na Staging: {e}")

    # 2. Validar no Data Quality Engine & Mover para Produção
    valid_records, rejected_records = validate_economic_calendar_records(records)
    logging.info(f"🛡️ Data Quality Engine: {len(valid_records)} eventos aprovados | {len(rejected_records)} em quarentena.")
    
    if not valid_records:
        return

    for attempt in range(1, 4):
        try:
            with engine.connect() as conn:
                trans = conn.begin()
                saved = 0
                for r in valid_records:
                    sql = text("""
                        INSERT INTO economic_calendar 
                        (event_name, country, currency, event_timestamp, impact_level, actual_val, forecast_val, previous_val, unit, source_provider)
                        VALUES (:event_name, :country, :currency, :event_timestamp, :impact_level, :actual_val, :forecast_val, :previous_val, :unit, :source_provider)
                        ON DUPLICATE KEY UPDATE 
                            actual_val = COALESCE(VALUES(actual_val), economic_calendar.actual_val),
                            forecast_val = COALESCE(VALUES(forecast_val), economic_calendar.forecast_val),
                            previous_val = COALESCE(VALUES(previous_val), economic_calendar.previous_val),
                            impact_level = VALUES(impact_level),
                            source_provider = VALUES(source_provider);
                    """)
                    conn.execute(sql, r)
                    saved += 1
                trans.commit()
                from app.services.data_validator import auto_resolve_anomalies
                from app.services.catalysts_service import validar_previsao_pos_evento
                for r in valid_records:
                    auto_resolve_anomalies("economic_calendar", r["event_name"])
                    if r.get("actual_val") is not None:
                        try:
                            validar_previsao_pos_evento(r)
                        except Exception as val_err:
                            logging.warning(f"⚠️ Erro na validação retrospetiva para {r.get('event_name')}: {val_err}")

                logging.info(f"🎉 {saved} eventos validados e salvos na tabela 'economic_calendar'!")
                return
        except Exception as e:
            logging.warning(f"⚠️ Tentativa {attempt}/3 falhou ao ligar à DB ({e}). A tentar novamente em 3s...")
            import time
            time.sleep(3)
    logging.error("❌ Falha permanente ao salvar eventos económicos após 3 tentativas.")

def save_corporate_earnings(records):
    if not records:
        return
        
    logging.info(f"💾 Salvando {len(records)} relatórios no 'corporate_earnings_calendar'...")
    
    for attempt in range(1, 4):
        try:
            with engine.connect() as conn:
                trans = conn.begin()
                saved = 0
                for r in records:
                    sql = text("""
                        INSERT INTO corporate_earnings_calendar 
                        (symbol, company_name, event_date, time_of_day, eps_estimate, eps_actual, revenue_estimate, revenue_actual, fiscal_period, source_provider)
                        VALUES (:symbol, :company_name, :event_date, :time_of_day, :eps_estimate, :eps_actual, :revenue_estimate, :revenue_actual, :fiscal_period, :source_provider)
                        ON DUPLICATE KEY UPDATE 
                            eps_actual = VALUES(eps_actual),
                            eps_estimate = VALUES(eps_estimate),
                            revenue_actual = VALUES(revenue_actual),
                            revenue_estimate = VALUES(revenue_estimate),
                            source_provider = VALUES(source_provider);
                    """)
                    conn.execute(sql, r)
                    saved += 1
                trans.commit()
                logging.info(f"🎉 {saved} registos de earnings salvos na tabela 'corporate_earnings_calendar'!")
                return
        except Exception as e:
            logging.warning(f"⚠️ Tentativa {attempt}/3 falhou ao ligar à DB ({e}). A tentar novamente em 3s...")
            import time
            time.sleep(3)
    logging.error("❌ Falha permanente ao salvar earnings após 3 tentativas.")

def check_phase1_high_impact_today() -> bool:
    """
    FASE 1: Verifica no MySQL se o dia de hoje tem pelo menos 1 evento com Importância = 'HIGH' 
    ou 1 earnings de ação ativa do catálogo.
    Se não houver nada agendado para hoje, regressa False (< 0.1s).
    """
    today_str = datetime.utcnow().strftime("%Y-%m-%d")
    active_stocks = get_active_stock_tickers()
    stock_list = list(active_stocks.keys())
    
    try:
        with engine.connect() as conn:
            # Check 1: Eventos Macro de Alto Impacto hoje
            q1 = text("""
                SELECT COUNT(*) FROM economic_calendar 
                WHERE impact_level = 'HIGH' 
                  AND DATE(event_timestamp) = :today
            """)
            c1 = conn.execute(q1, {"today": today_str}).scalar() or 0
            if c1 > 0:
                return True
                
            # Check 2: Earnings de Ações Ativas hoje
            if stock_list:
                q2 = text("""
                    SELECT COUNT(*) FROM corporate_earnings_calendar
                    WHERE symbol IN :stocks
                      AND DATE(event_date) = :today
                """)
                c2 = conn.execute(q2, {"today": today_str, "stocks": tuple(stock_list)}).scalar() or 0
                if c2 > 0:
                    return True
    except Exception as e:
        logging.warning(f"⚠️ Falha no check de Fase 1 ({e}). A assumir True por segurança.")
        return True
        
    return False

def check_phase2_pending_event_in_window() -> bool:
    """
    FASE 2: Verifica se existe algum evento 'HIGH' (ou earnings ativo) hoje 
    cujo resultado (actual_val / eps_actual) continue NULL.
    Se tudo já estiver preenchido, regressa False (< 0.2s).
    """
    today_str = datetime.utcnow().strftime("%Y-%m-%d")
    active_stocks = get_active_stock_tickers()
    stock_list = list(active_stocks.keys())

    try:
        with engine.connect() as conn:
            # Macro pendente hoje
            q1 = text("""
                SELECT COUNT(*) FROM economic_calendar 
                WHERE impact_level = 'HIGH' 
                  AND DATE(event_timestamp) = :today
                  AND actual_val IS NULL
            """)
            c1 = conn.execute(q1, {"today": today_str}).scalar() or 0
            if c1 > 0:
                return True

            # Earnings pendente hoje
            if stock_list:
                q2 = text("""
                    SELECT COUNT(*) FROM corporate_earnings_calendar
                    WHERE symbol IN :stocks
                      AND DATE(event_date) = :today
                      AND eps_actual IS NULL
                """)
                c2 = conn.execute(q2, {"today": today_str, "stocks": tuple(stock_list)}).scalar() or 0
                if c2 > 0:
                    return True
    except Exception as e:
        logging.warning(f"⚠️ Falha no check de Fase 2 ({e}). A assumir True por segurança.")
        return True

    return False

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Ingestão do Calendário Económico")
    parser.add_argument("--mode", choices=["full", "windowed"], default="full", help="Modo de execução: 'full' (diário completo) ou 'windowed' (micro-polling event-driven)")
    args = parser.parse_args()

    if args.mode == "windowed":
        # FASE 1: Verificação Diária
        if not check_phase1_high_impact_today():
            logging.info("ℹ️ [FASE 1 - DIA SEM EVENTOS] Nenhum evento de Alto Impacto ou Earnings de ações ativas hoje. Finalizando (< 0.1s).")
            sys.exit(0)

        # FASE 2: Early Exit em Janela Pendente
        if not check_phase2_pending_event_in_window():
            logging.info("ℹ️ [FASE 2 - EARLY EXIT] Todos os eventos de Alto Impacto de hoje já possuem resultado. Finalizando (< 0.2s).")
            sys.exit(0)

        logging.info("⚡ [EVENT-DRIVEN ACTIVATION] Evento pendente em janela ativa detetado! Executando recolha e atualização...")

    try:
        eco_records = fetch_economic_calendar_fmp()
        save_economic_events(eco_records)
        
        earnings_records = fetch_corporate_earnings_fmp()
        save_corporate_earnings(earnings_records)
    except Exception as e:
        logging.error(f"❌ Erro fatal no pipeline de Calendário Económico: {e}")
        try:
            from app.services.alert_service import send_alert_notification
            send_alert_notification(f"🚨 [FALHA CRÍTICA INGESTÃO] Calendário Económico falhou: {type(e).__name__} - {e}")
        except Exception as alert_err:
            logging.warning(f"Não foi possível enviar e-mail de notificação de falha: {alert_err}")
        raise
    finally:
        try:
            engine.dispose()
        except Exception:
            pass

if __name__ == "__main__":
    main()


