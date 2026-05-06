from fastapi import FastAPI
import yfinance as yf
import pandas as pd
import requests
import xml.etree.ElementTree as ET
from datetime import datetime

app = FastAPI(title="Agente Trading V6 - Full Intelligence")

# --- LISTAS REPOSTAS E AMPLIADAS ---

RADAR_USA = [
    "AAPL", "MSFT", "AMZN", "NVDA", "GOOGL", "META", "TSLA", "NFLX", "CRM", "UBER", 
    "ADBE", "ORCL", "PLTR", "AMD", "INTC", "PYPL", "DIS", "NKE", "SBUX", "F", 
    "GM", "WMT", "T", "VZ", "BAC", "JPM", "GS", "MS", "PFE", "JNJ", "ABT", 
    "MRK", "UNH", "RTX", "BA", "CAT", "DE", "HON", "IBM", "CSCO", "QCOM", 
    "MU", "LRCX", "PANW", "SNOW", "SHOP", "ABNB", "SQ", "NOW", "AMGN"
]

RADAR_COMMODITIES = [
    "GC=F", "SI=F", "CL=F", "BZ=F", "NG=F", "HG=F", "KC=F", "CC=F", "CT=F", 
    "SPY", "QQQ", "GDX", "XLE", "XLF", "XLK", "EEM", "IWM"
]

RADAR_EUROPA = [
    "ASML", "MC.PA", "OR.PA", "SAP", "SIE.DE", "TTE", "SAN.MC", "ADS.DE", "BMW.DE", 
    "VOW3.DE", "MBG.DE", "BAYN.DE", "BAS.DE", "AIR.PA", "BNP.PA", "INGA.AMS", 
    "STLAM.MI", "RACE", "KER.PA", "ITX.MC", "IBE.MC", "DB1.DE", "CBK.DE"
]

# --- MOTOR DE INTELIGÊNCIA ---

def buscar_noticias_e_classificar(simbolo):
    try:
        url = f"https://news.google.com/rss/search?q={simbolo}+stock+market+analysis+when:7d&hl=en-US&gl=US&ceid=US:en"
        response = requests.get(url, timeout=4)
        root = ET.fromstring(response.content)
        noticias = [item.find('title').text for item in root.findall('.//item')[:2]]
        texto = " ".join(noticias).lower()
        
        # Lógica de Classificação
        if any(w in texto for w in ["fed", "rates", "inflation", "market", "sector", "dollar", "economy"]):
            tipo = "✅ CONJUNTURAL (Oportunidade)"
        elif any(w in texto for w in ["lawsuit", "investigation", "fraud", "miss", "negative", "cuts"]):
            tipo = "⚠️ ESTRUTURAL (Risco)"
        else:
            tipo = "🟡 ANALISAR"
            
        return tipo, noticias[0] if noticias else "Sem notícias recentes."
    except:
        return "🟡 ANALISAR", "Erro ao carregar notícias."

def processar_radar(lista):
    oportunidades = []
    for simbolo in lista:
        try:
            t = yf.Ticker(simbolo)
            hist = t.history(period="6mo")
            if hist.empty: continue
            
            p_atual = hist['Close'].iloc[-1]
            max_p = hist['High'].max()
            desc = ((max_p - p_atual) / max_p) * 100
            
            # Filtro: Apenas ativos em "Saldos" (>10%)
            if desc > 10:
                tipo, news = buscar_noticias_e_classificar(simbolo)
                oportunidades.append({
                    "ativo": simbolo,
                    "preco": round(p_atual, 2),
                    "desconto": f"{round(desc, 1)}%",
                    "tipo": tipo,
                    "noticia": news,
                    "link": "https://webtrader.fortrade.com/"
                })
        except: continue
    return sorted(oportunidades, key=lambda x: float(x['desconto'].replace('%','')), reverse=True)

# --- ENDPOINTS ---

@app.get("/radar/usa")
def get_usa():
    return {"radar": "EUA Full Intel", "data": processar_radar(RADAR_USA)}

@app.get("/radar/commodities")
def get_commodities():
    return {"radar": "Commo & ETF Full Intel", "data": processar_radar(RADAR_COMMODITIES)}

@app.get("/radar/europa")
def get_europa():
    return {"radar": "Europa Full Intel", "data": processar_radar(RADAR_EUROPA)}

@app.get("/")
def home():
    return {"status": "Online", "v": "6.0", "endpoints": ["/radar/usa", "/radar/europa", "/radar/commodities"]}
