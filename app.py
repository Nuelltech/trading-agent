from fastapi import FastAPI
import yfinance as yf
import pandas as pd
import requests
import xml.etree.ElementTree as ET
from datetime import datetime

app = FastAPI(title="Agente Trading V3 - Dinâmico")

def obter_simbolos_sp500():
    """Obtém a lista atualizada de empresas do S&P 500 via Wikipedia."""
    try:
        url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
        # Usamos o pandas para ler a tabela da página
        tabelas = pd.read_html(url)
        df = tabelas[0]
        # Algumas empresas usam '.' (BRK.B), o yfinance prefere '-' (BRK-B)
        simbolos = [s.replace('.', '-') for s in df['Symbol'].tolist()]
        return simbolos
    except Exception as e:
        print(f"Erro ao obter lista: {e}")
        # Fallback de segurança caso a Wikipedia esteja inacessível
        return ["TSLA", "AAPL", "STLA", "NKE", "F", "DIS", "PYPL", "INTC", "GOLD"]

def buscar_noticias_recentes(simbolo):
    """Busca notícias via RSS do Google News."""
    try:
        url = f"https://news.google.com/rss/search?q={simbolo}+stock+when:7d&hl=en-US&gl=US&ceid=US:en"
        response = requests.get(url, timeout=5)
        root = ET.fromstring(response.content)
        noticias = [item.find('title').text for item in root.findall('.//item')[:3]]
        return noticias if noticias else ["Sem notícias relevantes."]
    except:
        return ["Erro ao carregar notícias."]

@app.get("/")
def home():
    return {
        "status": "Online",
        "modo": "Descobridor Dinâmico (S&P 500)",
        "endpoint": "/radar"
    }

@app.get("/radar")
def get_radar():
    # 1. Obtém a lista completa do S&P 500
    lista_completa = obter_simbolos_sp500()
    
    # No plano FREE do Render, processar 500 empresas pode dar timeout.
    # Vamos processar as primeiras 120 para garantir estabilidade.
    lista_pesquisa = lista_completa[:120]
    
    oportunidades = []
    
    for simbolo in lista_pesquisa:
        try:
            ticker = yf.Ticker(simbolo)
            # Analisamos os últimos 6 meses
            hist = ticker.history(period="6mo")
            
            if hist.empty or len(hist) < 10:
                continue
            
            preco_atual = hist['Close'].iloc[-1]
            
            # FILTRO DE ACESSIBILIDADE (Banca de 2.500€)
            # Ignoramos ações acima de 250€
            if preco_atual > 250 or preco_atual < 1:
                continue
                
            max_periodo = hist['High'].max()
            desconto = ((max_periodo - preco_atual) / max_periodo) * 100
            
            # FILTRO DE SALDOS (> 15% de queda)
            if desconto > 15:
                headlines = buscar_noticias_recentes(simbolo)
                analise_texto = " ".join(headlines).lower()
                
                # Classificação básica baseada em contexto
                if any(w in analise_texto for w in ["lawsuit", "investigation", "fraud", "miss", "negative"]):
                    tipo = "⚠️ ESTRUTURAL (Risco)"
                elif any(w in analise_texto for w in ["fed", "inflation", "market", "sector", "rates"]):
                    tipo = "✅ CONJUNTURAL (Oportunidade)"
                else:
                    tipo = "🟡 ANALISAR"

                oportunidades.append({
                    "ativo": simbolo,
                    "preco": round(preco_atual, 2),
                    "desconto": f"{round(desconto, 1)}%",
                    "tipo": tipo,
                    "headlines": headlines,
                    "link_fortrade": "https://webtrader.fortrade.com/"
                })
        except:
            continue

    # 3. RANKING FINAL TOP 20
    top_20 = sorted(oportunidades, key=lambda x: float(x['desconto'].replace('%','')), reverse=True)[:20]
    
    return {
        "data_analise": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "ativos_analisados": len(lista_pesquisa),
        "oportunidades_encontradas": len(top_20),
        "sugestoes": top_20
    }
