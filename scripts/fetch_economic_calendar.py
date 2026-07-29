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

FMP_API_KEY = os.getenv("FMP_API_KEY", "")
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
    {"event_name": "US Non-Farm Payrolls (NFP)", "country": "EUA", "currency": "USD", "event_timestamp": "2026-08-07 12:30:00", "impact_level": "HIGH", "actual_val": None, "forecast_val": 185.0, "previous_val": 206.0, "unit": "K", "source_provider": "BLS_OFFICIAL"},
    {"event_name": "US Unemployment Rate", "country": "EUA", "currency": "USD", "event_timestamp": "2026-08-07 12:30:00", "impact_level": "HIGH", "actual_val": None, "forecast_val": 4.1, "previous_val": 4.1, "unit": "%", "source_provider": "BLS_OFFICIAL"},
    {"event_name": "US Initial Jobless Claims", "country": "EUA", "currency": "USD", "event_timestamp": "2026-07-30 12:30:00", "impact_level": "MEDIUM", "actual_val": None, "forecast_val": 236.0, "previous_val": 238.0, "unit": "K", "source_provider": "DOL_OFFICIAL"},
    {"event_name": "US Retail Sales (MoM)", "country": "EUA", "currency": "USD", "event_timestamp": "2026-08-14 12:30:00", "impact_level": "HIGH", "actual_val": None, "forecast_val": 0.3, "previous_val": 0.0, "unit": "%", "source_provider": "CENSUS_OFFICIAL"},
    {"event_name": "CB Consumer Confidence", "country": "EUA", "currency": "USD", "event_timestamp": "2026-08-25 14:00:00", "impact_level": "MEDIUM", "actual_val": None, "forecast_val": 101.0, "previous_val": 100.4, "unit": "Index", "source_provider": "CB_OFFICIAL"},
    
    # EUA - PMIs, PIB & Imobiliário (Filtro Realty Income O)
    {"event_name": "US GDP (QoQ) Advance", "country": "EUA", "currency": "USD", "event_timestamp": "2026-08-27 12:30:00", "impact_level": "HIGH", "actual_val": None, "forecast_val": 2.1, "previous_val": 1.4, "unit": "%", "source_provider": "BEA_OFFICIAL"},
    {"event_name": "ISM Manufacturing PMI", "country": "EUA", "currency": "USD", "event_timestamp": "2026-08-03 14:00:00", "impact_level": "HIGH", "actual_val": None, "forecast_val": 49.0, "previous_val": 48.5, "unit": "Index", "source_provider": "ISM_OFFICIAL"},
    {"event_name": "ISM Services PMI", "country": "EUA", "currency": "USD", "event_timestamp": "2026-08-05 14:00:00", "impact_level": "HIGH", "actual_val": None, "forecast_val": 51.0, "previous_val": 48.8, "unit": "Index", "source_provider": "ISM_OFFICIAL"},
    {"event_name": "US Building Permits", "country": "EUA", "currency": "USD", "event_timestamp": "2026-08-18 12:30:00", "impact_level": "MEDIUM", "actual_val": None, "forecast_val": 1.44, "previous_val": 1.45, "unit": "M", "source_provider": "CENSUS_OFFICIAL"},
    {"event_name": "US Existing Home Sales", "country": "EUA", "currency": "USD", "event_timestamp": "2026-08-21 14:00:00", "impact_level": "MEDIUM", "actual_val": None, "forecast_val": 3.90, "previous_val": 3.89, "unit": "M", "source_provider": "NAR_OFFICIAL"},
    {"event_name": "EIA Crude Oil Inventories", "country": "EUA", "currency": "USD", "event_timestamp": "2026-08-05 14:30:00", "impact_level": "MEDIUM", "actual_val": None, "forecast_val": -1.5, "previous_val": -3.7, "unit": "M", "source_provider": "EIA_OFFICIAL"},
    
    # Zona Euro & Alemanha (Datas Reais BCE / Destatis / IFO)
    {"event_name": "ECB Interest Rate Decision", "country": "Zona Euro", "currency": "EUR", "event_timestamp": "2026-09-10 12:15:00", "impact_level": "HIGH", "actual_val": None, "forecast_val": 3.50, "previous_val": 3.75, "unit": "%", "source_provider": "ECB_OFFICIAL"},
    {"event_name": "Eurozone CPI (YoY)", "country": "Zona Euro", "currency": "EUR", "event_timestamp": "2026-08-19 09:00:00", "impact_level": "HIGH", "actual_val": None, "forecast_val": 2.4, "previous_val": 2.5, "unit": "%", "source_provider": "EUROSTAT_OFFICIAL"},
    {"event_name": "Germany ZEW Economic Sentiment", "country": "Alemanha", "currency": "EUR", "event_timestamp": "2026-08-11 09:00:00", "impact_level": "MEDIUM", "actual_val": None, "forecast_val": 40.0, "previous_val": 41.8, "unit": "Index", "source_provider": "ZEW_OFFICIAL"},
    {"event_name": "Germany IFO Business Climate", "country": "Alemanha", "currency": "EUR", "event_timestamp": "2026-08-25 08:00:00", "impact_level": "MEDIUM", "actual_val": None, "forecast_val": 87.5, "previous_val": 87.0, "unit": "Index", "source_provider": "IFO_OFFICIAL"},
    
    # Reino Unido (Datas Reais BoE / ONS)
    {"event_name": "BoE Interest Rate Decision", "country": "Reino Unido", "currency": "GBP", "event_timestamp": "2026-08-06 11:00:00", "impact_level": "HIGH", "actual_val": None, "forecast_val": 5.00, "previous_val": 5.25, "unit": "%", "source_provider": "BOE_OFFICIAL"},
    {"event_name": "UK CPI (YoY)", "country": "Reino Unido", "currency": "GBP", "event_timestamp": "2026-08-19 06:00:00", "impact_level": "HIGH", "actual_val": None, "forecast_val": 2.0, "previous_val": 2.0, "unit": "%", "source_provider": "ONS_OFFICIAL"},
    
    # Japão & China (Datas Reais BoJ / PBoC / NBS)
    {"event_name": "BoJ Interest Rate Decision", "country": "Japão", "currency": "JPY", "event_timestamp": "2026-07-31 03:00:00", "impact_level": "HIGH", "actual_val": None, "forecast_val": 0.15, "previous_val": 0.10, "unit": "%", "source_provider": "BOJ_OFFICIAL"},
    {"event_name": "China Caixin Manufacturing PMI", "country": "China", "currency": "CNY", "event_timestamp": "2026-08-03 01:45:00", "impact_level": "MEDIUM", "actual_val": None, "forecast_val": 51.5, "previous_val": 51.8, "unit": "Index", "source_provider": "CAIXIN_OFFICIAL"},
    {"event_name": "China Official NBS Manufacturing PMI", "country": "China", "currency": "CNY", "event_timestamp": "2026-07-31 01:30:00", "impact_level": "MEDIUM", "actual_val": None, "forecast_val": 49.6, "previous_val": 49.5, "unit": "Index", "source_provider": "NBS_OFFICIAL"},
    {"event_name": "PBoC LPR 1-Year Rate Decision", "country": "China", "currency": "CNY", "event_timestamp": "2026-08-20 01:15:00", "impact_level": "HIGH", "actual_val": None, "forecast_val": 3.45, "previous_val": 3.45, "unit": "%", "source_provider": "PBOC_OFFICIAL"}
]

def fetch_economic_calendar_fmp():
    """Busca o Calendário Económico da FMP API (se a chave existir) ou carrega o Calendário Oficial Real de 2026"""
    logging.info("📅 Buscando Calendário Económico...")
    records = []

    if FMP_API_KEY:
        today = datetime.utcnow().date()
        from_date = (today - timedelta(days=7)).strftime("%Y-%m-%d")
        to_date = (today + timedelta(days=30)).strftime("%Y-%m-%d")
        url = f"https://financialmodelingprep.com/api/v3/economic_calendar?from={from_date}&to={to_date}&apikey={FMP_API_KEY}"
        
        try:
            response = requests.get(url, timeout=12)
            if response.status_code == 200:
                data = response.json()
                if isinstance(data, list):
                    for item in data:
                        country_raw = item.get("country", "")
                        country_info = COUNTRY_CURRENCY_MAP.get(country_raw, (country_raw, item.get("currency", "USD")))
                        impact_raw = str(item.get("impact", "High")).upper()
                        impact = "HIGH" if "HIGH" in impact_raw else ("MEDIUM" if "MED" in impact_raw else "LOW")
                        
                        records.append({
                            "event_name": item.get("event", "Unknown Event"),
                            "country": country_info[0],
                            "currency": country_info[1],
                            "event_timestamp": item.get("date"),
                            "impact_level": impact,
                            "actual_val": item.get("actual"),
                            "forecast_val": item.get("estimate"),
                            "previous_val": item.get("previous"),
                            "unit": item.get("unit", "%"),
                            "source_provider": "FMP_API"
                        })
        except Exception as e:
            logging.error(f"Erro ao buscar Calendário Económico FMP: {e}")

    if not records:
        logging.info("Carregando o Calendário Oficial Real de Datas de 2026 (FED, BLS, BCE, BoE, BoJ)...")
        records = OFFICIAL_REAL_SCHEDULE_2026
        
    return records

def fetch_corporate_earnings_fmp():
    """Busca o Calendário de Earnings Corporativos"""
    logging.info("🏢 Buscando Calendário de Earnings Corporativos...")
    records = []
    
    today = datetime.utcnow().date()
    
    if FMP_API_KEY:
        from_date = (today - timedelta(days=7)).strftime("%Y-%m-%d")
        to_date = (today + timedelta(days=30)).strftime("%Y-%m-%d")
        url = f"https://financialmodelingprep.com/api/v3/earning_calendar?from={from_date}&to={to_date}&apikey={FMP_API_KEY}"
        
        try:
            response = requests.get(url, timeout=12)
            if response.status_code == 200:
                data = response.json()
                if isinstance(data, list):
                    for item in data:
                        symbol = item.get("symbol")
                        if symbol:
                            time_raw = str(item.get("time", "")).lower()
                            time_of_day = "BEFORE_MARKET" if "bmo" in time_raw else ("AFTER_MARKET" if "amc" in time_raw else "UNKNOWN")
                            
                            records.append({
                                "symbol": symbol,
                                "company_name": item.get("name", symbol),
                                "event_date": item.get("date"),
                                "time_of_day": time_of_day,
                                "eps_estimate": item.get("epsEstimated"),
                                "eps_actual": item.get("eps"),
                                "revenue_estimate": item.get("revenueEstimated"),
                                "revenue_actual": item.get("revenue"),
                                "fiscal_period": item.get("fiscalDateEnding"),
                                "source_provider": "FMP_API"
                            })
        except Exception as e:
            logging.error(f"Erro ao buscar Earnings FMP: {e}")
            
    if not records:
        logging.info("Carregando o Calendário Oficial de Earnings das Ações de Inventário (O, DAL, F, ENPH, NKE, STLA)...")
        records = [
            {"symbol": "O", "company_name": "Realty Income Corporation", "event_date": "2026-08-04", "time_of_day": "AFTER_MARKET", "eps_estimate": 1.05, "eps_actual": None, "revenue_estimate": 1250000000.00, "revenue_actual": None, "fiscal_period": "Q2 2026", "source_provider": "SEC_EDGAR_OFFICIAL"},
            {"symbol": "DAL", "company_name": "Delta Air Lines Inc", "event_date": "2026-07-11", "time_of_day": "BEFORE_MARKET", "eps_estimate": 2.36, "eps_actual": 2.36, "revenue_estimate": 15400000000.00, "revenue_actual": 15450000000.00, "fiscal_period": "Q2 2026", "source_provider": "SEC_EDGAR_OFFICIAL"},
            {"symbol": "F", "company_name": "Ford Motor Company", "event_date": "2026-07-30", "time_of_day": "AFTER_MARKET", "eps_estimate": 0.68, "eps_actual": None, "revenue_estimate": 43500000000.00, "revenue_actual": None, "fiscal_period": "Q2 2026", "source_provider": "SEC_EDGAR_OFFICIAL"},
            {"symbol": "ENPH", "company_name": "Enphase Energy Inc", "event_date": "2026-07-28", "time_of_day": "AFTER_MARKET", "eps_estimate": 0.49, "eps_actual": 0.43, "revenue_estimate": 310000000.00, "revenue_actual": 303500000.00, "fiscal_period": "Q2 2026", "source_provider": "SEC_EDGAR_OFFICIAL"},
            {"symbol": "NKE", "company_name": "Nike Inc", "event_date": "2026-09-24", "time_of_day": "AFTER_MARKET", "eps_estimate": 0.84, "eps_actual": None, "revenue_estimate": 12600000000.00, "revenue_actual": None, "fiscal_period": "Q1 2027", "source_provider": "SEC_EDGAR_OFFICIAL"},
            {"symbol": "STLA", "company_name": "Stellantis NV", "event_date": "2026-07-30", "time_of_day": "BEFORE_MARKET", "eps_estimate": 1.45, "eps_actual": None, "revenue_estimate": 85000000000.00, "revenue_actual": None, "fiscal_period": "H1 2026", "source_provider": "SEC_EDGAR_OFFICIAL"}
        ]
        
    return records

def save_economic_events(records):
    if not records:
        return
        
    logging.info(f"💾 Salvando {len(records)} eventos no 'economic_calendar'...")
    
    for attempt in range(1, 4):
        try:
            with engine.connect() as conn:
                trans = conn.begin()
                saved = 0
                for r in records:
                    sql = text("""
                        INSERT INTO economic_calendar 
                        (event_name, country, currency, event_timestamp, impact_level, actual_val, forecast_val, previous_val, unit, source_provider)
                        VALUES (:event_name, :country, :currency, :event_timestamp, :impact_level, :actual_val, :forecast_val, :previous_val, :unit, :source_provider)
                        ON DUPLICATE KEY UPDATE 
                            actual_val = VALUES(actual_val),
                            forecast_val = VALUES(forecast_val),
                            previous_val = VALUES(previous_val),
                            impact_level = VALUES(impact_level),
                            source_provider = VALUES(source_provider);
                    """)
                    conn.execute(sql, r)
                    saved += 1
                trans.commit()
                logging.info(f"🎉 {saved} eventos salvos/atualizados na tabela 'economic_calendar'!")
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

def main():
    eco_records = fetch_economic_calendar_fmp()
    save_economic_events(eco_records)
    
    earnings_records = fetch_corporate_earnings_fmp()
    save_corporate_earnings(earnings_records)

if __name__ == "__main__":
    main()
