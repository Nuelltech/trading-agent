from fastapi import FastAPI
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta

app = FastAPI(title="Agente de Inteligência de Trading")

# Lista expandida para o radar (Agnóstica de setores)
WATCHLIST_BASE = [
    "TSLA", "STLA", "INTC", "PFE", "DIS", "NKE", "T", "BAC", "WMT", "JNJ", 
    "PYPL", "SQ", "XOM", "CVX", "BA", "NIO", "BMW.DE", "VOW3.DE", "VALE",
    "GOLD", "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NFLX", "AMD", "UBER",
    "BABA", "JD", "SNAP", "PLTR", "RIVN", "LCID", "F", "GM", "COIN", "HOOD"
]

def get_sector_performance(symbol):
    """Simula a comparação com o setor para justificar a queda."""
    # Em versões futuras, isto consultará índices como o SPY ou QQQ
    return "Mercado Geral"

@app.get("/")
def home():
    return {"status": "Online", "message": "Agente Informativo de Trading pronto para varredura em /radar"}

@app.get("/radar")
def get_radar():
    oportunidades = []
    
    for simbolo in WATCHLIST_BASE:
        try:
            ticker = yf.Ticker(simbolo)
            # Analisamos os últimos 6 meses para captar quedas recentes
            hist = ticker.history(period="6mo")
            if hist.empty or len(hist) < 20:
                continue
            
            preco_atual = hist['Close'].iloc[-1]
            
            # FILTRO DE ACESSIBILIDADE: Ignora ações acima de 250€ (para banca de 2500€)
            if preco_atual > 250:
                continue
                
            max_periodo = hist['High'].max()
            desconto = ((max_periodo - preco_atual) / max_periodo) * 100
            
            # Só interessa se o desconto for superior a 15%
            if desconto > 15:
                # Lógica de Justificação
                volatilidade = hist['Close'].std()
                if desconto > 30:
                    tipo = "⚠️ ESTRUTURAL / PÂNICO"
                    just = "Queda muito acentuada. Verificar notícias de falhas internas ou processos."
                else:
                    tipo = "✅ CONJUNTURAL"
                    just = f"Queda de {round(desconto,1)}% num mercado volátil. Preço historicamente atrativo face aos últimos 6 meses."

                oportunidades.append({
                    "ativo": simbolo,
                    "preco_atual": f"{round(preco_atual, 2)}",
                    "desconto_percentual": f"{round(desconto, 2)}%",
                    "analise_tipo": tipo,
                    "justificacao": just,
                    "link_fortrade": f"https://webtrader.fortrade.com/" # Atalho rápido
                })
        except Exception:
            continue

    # Ordenar por maior desconto e entregar o TOP 20
    top_20 = sorted(oportunidades, key=lambda x: float(x['desconto_percentual'].replace('%','')), reverse=True)[:20]
    
    return {
        "data_analise": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_encontrados": len(top_20),
        "sugestoes": top_20
    }

if __name__ == "__main__":
    import uvicorn
    import os
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
