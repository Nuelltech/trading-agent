from fastapi import FastAPI
import yfinance as yf
import pandas as pd
import requests
import xml.etree.ElementTree as ET
from datetime import datetime

app = FastAPI(title="Agente Trading V4 - Transparency Mode")

def obter_simbolos_sp500():
    """Obtém a lista do S&P 500 disfarçando o bot para evitar Erro 403."""
    try:
        url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
        # DISFARCE: Simulamos ser um browser real
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        response = requests.get(url, headers=headers)
        tabelas = pd.read_html(response.text)
        df = tabelas[0]
        return [s.replace('.', '-') for s in df['Symbol'].tolist()]
    except Exception as e:
        print(f"Erro ao obter lista: {e}")
        # Fallback caso o bloqueio persista
        return ["TSLA", "STLA", "NKE", "F", "DIS", "PYPL", "INTC", "GOLD", "PFE", "T", "BAC", "WMT", "JNJ"]

def buscar_noticias_recentes(simbolo):
    try:
        url = f"https://news.google.com/rss/search?q={simbolo}+stock+when:7d&hl=en-US&gl=US&ceid=US:en"
        response = requests.get(url, timeout=5)
        root = ET.fromstring(response.content)
        return [item.find('title').text for item in root.findall('.//item')[:3]]
    except:
        return ["Sem notícias recentes."]

@app.get("/radar")
def get_radar():
    lista_completa = obter_simbolos_sp500()
    # No Render Free, vamos processar 80 ativos por pedido para garantir que não há timeout
    lista_pesquisa = lista_completa[:80] 
    
    oportunidades = []
    descarte = {"preco_alto": 0, "desconto_insuficiente": 0, "erros": 0}
    
    for simbolo in lista_pesquisa:
        try:
            ticker = yf.Ticker(simbolo)
            hist = ticker.history(period="6mo")
            if hist.empty:
                descarte["erros"] += 1
                continue
            
            preco_atual = hist['Close'].iloc[-1]
            
            # FILTRO 1: Preço (Banca de 2.500€)
            if preco_atual > 250:
                descarte["preco_alto"] += 1
                continue
                
            max_p = hist['High'].max()
            desc = ((max_p - preco_atual) / max_p) * 100
            
            # FILTRO 2: Desconto mínimo de 15%
            if desc < 15:
                descarte["desconto_insuficiente"] += 1
                continue
            
            # Se passou, busca notícias e justifica
            headlines = buscar_noticias_recentes(simbolo)
            analise_texto = " ".join(headlines).lower()
            
            tipo = "🟡 ANALISAR"
            if any(w in analise_texto for w in ["lawsuit", "investigation", "fraud", "miss", "negative"]):
                tipo = "⚠️ ESTRUTURAL (Risco)"
            elif any(w in analise_texto for w in ["fed", "inflation", "market", "sector", "rates"]):
                tipo = "✅ CONJUNTURAL (Oportunidade)"

            oportunidades.append({
                "ativo": simbolo,
                "preco": round(preco_atual, 2),
                "desconto": f"{round(desc, 1)}%",
                "tipo": tipo,
                "justificacao_real": headlines[0] if headlines else "Sem manchetes",
                "link_fortrade": "https://webtrader.fortrade.com/"
            })
        except:
            descarte["erros"] += 1
            continue

    return {
        "metadados": {
            "data": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "total_analisado": len(lista_pesquisa),
            "rejeitados_preco_alto": descarte["preco_alto"],
            "rejeitados_desconto_baixo": descarte["desconto_insuficiente"],
            "erros_tecnicos": descarte["erros"]
        },
        "sugestoes": sorted(oportunidades, key=lambda x: float(x['desconto'].replace('%','')), reverse=True)[:20]
    }
