from fastapi import FastAPI
import yfinance as yf
import pandas as pd
import requests
import xml.etree.ElementTree as ET
from datetime import datetime

app = FastAPI(title="Agente Trading V5 - Focado e Estável")

# LISTA CURADA (Hardcoded) - Focada em Liquidez e Acessibilidade
# Removi ativos acima de 500$ e deixei os mais interessantes da tua lista
MINHA_LISTA = [
    "NVDA", "AAPL", "TSLA", "WMT", "XOM", "V", "INTC", "JNJ", "BAC", "NFLX",
    "DIS", "PYPL", "F", "PFE", "UBER", "STLA", "NKE", "GOLD", "AMD", "GOOGL",
    "AMZN", "META", "MU", "ORCL", "CSCO", "KO", "HD", "PLTR", "MRK", "PM",
    "WFC", "RTX", "C", "PEP", "IBM", "QCOM", "MCD", "NEE", "VZ", "BA",
    "T", "TJX", "GILD", "WDC", "SCHW", "DELL", "BX", "ABT", "PANW", "CRM",
    "COP", "HON", "SBUX", "NEM", "MO", "CVS", "ACN", "MDT", "CMCSA", "USB"
]

def buscar_noticias_recentes(simbolo):
    try:
        url = f"https://news.google.com/rss/search?q={simbolo}+stock+when:7d&hl=en-US&gl=US&ceid=US:en"
        response = requests.get(url, timeout=5)
        root = ET.fromstring(response.content)
        return [item.find('title').text for item in root.findall('.//item')[:3]]
    except:
        return ["Sem notícias recentes."]

@app.get("/")
def home():
    return {"status": "Online", "modo": "Cão de Guarda (Lista Privada)"}

@app.get("/radar")
def get_radar():
    oportunidades = []
    start_time = datetime.now()
    
    for simbolo in MINHA_LISTA:
        try:
            ticker = yf.Ticker(simbolo)
            # 6 meses é o ideal para ver o "desconto" em relação ao topo recente
            hist = ticker.history(period="6mo")
            if hist.empty: continue
            
            preco_atual = hist['Close'].iloc[-1]
            
            # Filtro de Preço (Acessibilidade para 2.500€)
            if preco_atual > 300: continue
            
            max_p = hist['High'].max()
            desc = ((max_p - preco_atual) / max_p) * 100
            
            # Filtro de Desconto (Só interessa se caiu mais de 10% para ser "saldo")
            if desc > 10:
                headlines = buscar_noticias_recentes(simbolo)
                analise_texto = " ".join(headlines).lower()
                
                # Inteligência de Classificação
                if any(w in analise_texto for w in ["lawsuit", "investigation", "fraud", "miss", "negative"]):
                    tipo = "⚠️ ESTRUTURAL (Risco)"
                elif any(w in analise_texto for w in ["fed", "inflation", "market", "sector", "rates", "economy"]):
                    tipo = "✅ CONJUNTURAL (Oportunidade)"
                else:
                    tipo = "🟡 ANALISAR"

                oportunidades.append({
                    "ativo": simbolo,
                    "preco": round(preco_atual, 2),
                    "desconto": f"{round(desc, 1)}%",
                    "tipo": tipo,
                    "justificacao_real": headlines[0] if headlines else "Verificar notícias",
                    "link_fortrade": f"https://webtrader.fortrade.com/"
                })
        except:
            continue

    # Ordenar pelos melhores descontos
    top_radar = sorted(oportunidades, key=lambda x: float(x['desconto'].replace('%','')), reverse=True)

    return {
        "metadados": {
            "data": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "ativos_vigiados": len(MINHA_LISTA),
            "oportunidades_no_radar": len(top_radar),
            "tempo_execucao": f"{(datetime.now() - start_time).seconds}s"
        },
        "sugestoes": top_radar
    }
