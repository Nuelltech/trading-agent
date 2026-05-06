from fastapi import FastAPI
import yfinance as yf
import pandas as pd
import requests
import xml.etree.ElementTree as ET
from datetime import datetime

app = FastAPI(title="Agente Trading Multi-Radar")

# --- LISTAS DE ALTO VOLUME ---

RADAR_USA = [
    "AAPL", "MSFT", "AMZN", "NVDA", "GOOGL", "META", "TSLA", "BRK-B", "UNH", "JPM",
    "JNJ", "V", "PG", "MA", "HD", "CVX", "ABBV", "LLY", "AVGO", "PFE", "XOM",
    "BAC", "KO", "PEP", "TMO", "COST", "ADBE", "WMT", "DIS", "CSCO", "ACN",
    "ABT", "VZ", "CRM", "DHR", "NFLX", "LIN", "TXN", "PM", "UPS", "NEE",
    "AMGN", "HON", "LOW", "RTX", "ORCL", "IBM", "INTC", "CAT", "QCOM"
]

RADAR_EUROPA = [
    "ASML", "MC.PA", "OR.PA", "SAP", "SIE.DE", "TTE", "SAN.MC", "ADS.DE", "ALV.DE",
    "AIR.PA", "BMW.DE", "VOW3.DE", "MBG.DE", "BAYN.DE", "BAS.DE", "DTE.DE", "BNP.PA",
    "INGA.AMS", "ISP.MI", "ENI.MI", "RACE", "STLAM.MI", "BBVA.MC", "ITX.MC", "IBE.MC",
    "RMS.PA", "KER.PA", "DAI.DE", "CBK.DE", "RWE.DE", "DPW.DE", "MUV2.DE"
]

RADAR_COMMODITIES_ETFS = [
    "GC=F", "SI=F", "CL=F", "BZ=F", "NG=F", "HG=F", "KC=F", "CC=F", "CT=F", # Commo
    "SPY", "QQQ", "IWM", "EEM", "GDX", "XLF", "XLE", "XLI", "XLK", "XLV", "XLP" # ETFs
]

RADAR_ASIA_EMERGENTES = [
    "BABA", "JD", "NIO", "TSM", "SONY", "VALE", "PBR", "BIDU", "PDD", "TCEHY",
    "HMC", "TM", "INFY", "SE", "MELI", "CPNG", "BUD", "BTI"
]

# --- LÓGICA DE PROCESSAMENTO ---

def analisar_lista(lista, nome_radar):
    oportunidades = []
    for simbolo in lista:
        try:
            ticker = yf.Ticker(simbolo)
            hist = ticker.history(period="6mo")
            if hist.empty: continue
            
            preco_atual = hist['Close'].iloc[-1]
            # Filtro: Ações > 450€ fora, commodities/ETFs sempre dentro
            if preco_atual > 450 and not any(x in simbolo for x in ["=F", "SPY", "QQQ"]):
                continue
            
            max_p = hist['High'].max()
            desc = ((max_p - preco_atual) / max_p) * 100
            
            # Filtro de Desconto: 10% para ações, 7% para ETFs/Commodities
            limiar = 7 if any(x in simbolo for x in ["=F", "SPY", "QQQ"]) else 10
            
            if desc > limiar:
                oportunidades.append({
                    "ativo": simbolo,
                    "preco": round(preco_atual, 2),
                    "desconto": f"{round(desc, 1)}%",
                    "link": f"https://webtrader.fortrade.com/"
                })
        except: continue
    
    return sorted(oportunidades, key=lambda x: float(x['desconto'].replace('%','')), reverse=True)

# --- ENDPOINTS (Onde vais clicar) ---

@app.get("/radar/usa")
def get_usa():
    return {"radar": "EUA (S&P 500 / NASDAQ)", "data": analisar_lista(RADAR_USA, "USA")}

@app.get("/radar/europa")
def get_europa():
    return {"radar": "EUROPA (Stoxx / DAX)", "data": analisar_lista(RADAR_EUROPA, "EUROPA")}

@app.get("/radar/commodities")
def get_commodities():
    return {"radar": "COMMODITIES & ETFs", "data": analisar_lista(RADAR_COMMODITIES_ETFS, "COMMODITIES")}

@app.get("/radar/asia")
def get_asia():
    return {"radar": "ÁSIA & EMERGENTES", "data": analisar_lista(RADAR_ASIA_EMERGENTES, "ASIA")}

@app.get("/")
def home():
    return {
        "msg": "Agente Multi-Radar Ativo",
        "links": ["/radar/usa", "/radar/europa", "/radar/commodities", "/radar/asia"]
    }
