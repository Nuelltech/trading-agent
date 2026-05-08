from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
import yfinance as yf
import requests
import xml.etree.ElementTree as ET
from datetime import datetime

# 1. INICIALIZAÇÃO
app = FastAPI(title="Agente Trading V6 - Full Intelligence")

# 2. CONFIGURAÇÃO DE SEGURANÇA (CORS)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 3. CONFIGURAÇÃO DE TEMPLATES
templates = Jinja2Templates(directory="templates")

# 4. LISTAS DE ATIVOS
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

# 5. FUNÇÕES DE INTELIGÊNCIA E ESTRATÉGIA

@app.get("/estrategia/{simbolo}")
def gerar_estrategia(simbolo: str):
    try:
        t = yf.Ticker(simbolo)
        hist_longo = t.history(period="2y")
        hist_curto = t.history(period="60d")
        
        if hist_curto.empty:
            return {"erro": "Dados insuficientes"}

        p_atual = hist_curto['Close'].iloc[-1]
        
        # --- ANÁLISE TÉCNICA AVANÇADA ---
        ema_20 = hist_curto['Close'].ewm(span=20).mean().iloc[-1]
        ema_50 = hist_curto['Close'].ewm(span=50).mean().iloc[-1]
        volatilidade = hist_curto['Close'].pct_change().std() * 100
        
        # --- LÓGICA DE PULLBACK E REFORÇO ---
        suporte_10d = hist_curto['Low'].tail(10).min()
        suporte_critico = hist_curto['Low'].min()
        resistencia_topo = hist_longo['High'].tail(250).max()
        
        # Zona de Pullback (Entrada Defensiva)
        zona_pullback = round(suporte_10d * 1.005, 2)
        
        # Mensagem de Pullback
        if p_atual > suporte_10d * 1.03:
            pullback_msg = f"⚠️ Preço esticado. Aguardar recuo aos {zona_pullback}€."
        else:
            pullback_msg = "✅ Ponto de entrada atual defensável (próximo ao suporte)."

        # Reforços
        reforco_queda = round(suporte_critico * 1.005, 2)
        rompimento_alta = round(hist_curto['High'].tail(20).max(), 2)

        # --- GESTÃO DE RISCO (Banca 2500€ | 1% Risco = 25€) ---
        stop_loss = round(p_atual - (p_atual * (volatilidade/100) * 2.5), 2)
        tp1 = round(p_atual + (p_atual - stop_loss) * 2, 2)
        
        # --- INSIGHTS ---
        tipo, noticia = buscar_noticias_e_classificar(simbolo)
        tendencia = "BULLISH 🚀" if p_atual > ema_50 else "BEARISH 📉"
        fase = "Acumulação" if abs(p_atual - ema_20)/p_atual < 0.02 else "Expansão"

        return {
            "ativo": simbolo.upper(),
            "preco": round(p_atual, 2),
            "tendencia": tendencia,
            "fase": fase,
            "zona_pullback": zona_pullback,
            "pullback_msg": pullback_msg,
            "reforco_queda": reforco_queda,
            "rompimento_alta": rompimento_alta,
            "stop": stop_loss,
            "tp1": tp1,
            "tp_final": round(resistencia_topo, 2),
            "lotes": round(25 / (p_atual - stop_loss), 2) if p_atual > stop_loss else 0,
            "insight": f"Mercado em {fase}. O preço ignora a média de 50 dias enquanto noticias indicam cenário {tipo.split()[1]}.",
            "noticia": noticia,
            "risco": "MÉDIO" if volatilidade < 2.5 else "ALTO 🔥"
        }
    except Exception as e:
        return {"erro": str(e)}

@app.get("/analise/{simbolo}")
def analise_detalhada(simbolo: str):
    try:
        t = yf.Ticker(simbolo)
        hist = t.history(period="30d")
        delta = hist['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        rsi_val = round(rsi.iloc[-1], 2)
        
        rec = "Neutral"
        if rsi_val < 35: rec = "🔥 SOBREVENDIDO (Comprar)"
        elif rsi_val > 65: rec = "⚠️ SOBRECOMPRADO (Aguardar)"
            
        return {
            "rsi": rsi_val,
            "recomendacao": rec,
            "suporte": round(hist['Low'].min(), 2)
        }
    except:
        return {"rsi": "N/A", "recomendacao": "Erro", "suporte": 0}

def buscar_noticias_e_classificar(simbolo):
    try:
        url = f"https://news.google.com/rss/search?q={simbolo}+stock+market+analysis+when:7d&hl=en-US&gl=US&ceid=US:en"
        response = requests.get(url, timeout=4)
        root = ET.fromstring(response.content)
        noticias = [item.find('title').text for item in root.findall('.//item')[:2]]
        texto = " ".join(noticias).lower()
        
        if any(w in texto for w in ["fed", "rates", "inflation", "market", "sector", "dollar", "economy"]):
            tipo = "✅ CONJUNTURAL (Oportunidade)"
        elif any(w in texto for w in ["lawsuit", "investigation", "fraud", "miss", "negative", "cuts"]):
            tipo = "⚠️ ESTRUTURAL (Risco)"
        else:
            tipo = "🟡 ANALISAR"
        return tipo, noticias[0] if noticias else "Sem notícias recentes."
    except:
        return "🟡 ANALISAR", "Erro de conexão."

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

@app.get("/")
async def dashboard(request: Request):
    try:
        vix = yf.Ticker("^VIX").history(period="1d")['Close'].iloc[-1]
    except:
        vix = 0
    return templates.TemplateResponse("index.html", {"request": request, "vix": round(vix, 2)})

@app.get("/macro")
def get_macro():
    try:
        vix = yf.Ticker("^VIX").history(period="1d")['Close'].iloc[-1]
    except:
        vix = 0
    return {"vix": round(vix, 2)}

@app.get("/radar/usa")
def get_usa(): return {"data": processar_radar(RADAR_USA)}

@app.get("/radar/commodities")
def get_commodities(): return {"data": processar_radar(RADAR_COMMODITIES)}

@app.get("/radar/europa")
def get_europa(): return {"data": processar_radar(RADAR_EUROPA)}
