from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
import yfinance as yf
import httpx
import asyncio
import xml.etree.ElementTree as ET
from datetime import datetime
from functools import lru_cache
import time

# ─────────────────────────────────────────
# 1. INICIALIZAÇÃO
# ─────────────────────────────────────────
app = FastAPI(title="Agente Trading V7 - Professional Grade")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

templates = Jinja2Templates(directory="templates")

# ─────────────────────────────────────────
# 2. CACHE SIMPLES COM TTL (5 minutos)
# ─────────────────────────────────────────
_cache = {}
CACHE_TTL = 300  # segundos

def cache_get(key):
    entry = _cache.get(key)
    if entry and (time.time() - entry["ts"] < CACHE_TTL):
        return entry["data"]
    return None

def cache_set(key, data):
    _cache[key] = {"data": data, "ts": time.time()}

# ─────────────────────────────────────────
# 3. LISTAS DE ATIVOS
# ─────────────────────────────────────────
RADAR_USA = [
    "AAPL","MSFT","AMZN","NVDA","GOOGL","META","TSLA","NFLX","CRM","UBER",
    "ADBE","ORCL","PLTR","AMD","INTC","PYPL","DIS","NKE","SBUX","F",
    "GM","WMT","T","VZ","BAC","JPM","GS","MS","PFE","JNJ","ABT",
    "MRK","UNH","RTX","BA","CAT","DE","HON","IBM","CSCO","QCOM",
    "MU","LRCX","PANW","SNOW","SHOP","ABNB","SQ","NOW","AMGN"
]
RADAR_COMMODITIES = [
    "GC=F","SI=F","CL=F","BZ=F","NG=F","HG=F","KC=F","CC=F","CT=F",
    "SPY","QQQ","GDX","XLE","XLF","XLK","EEM","IWM"
]
RADAR_EUROPA = [
    "ASML","MC.PA","OR.PA","SAP","SIE.DE","TTE","SAN.MC","ADS.DE","BMW.DE",
    "VOW3.DE","MBG.DE","BAYN.DE","BAS.DE","AIR.PA","BNP.PA","INGA.AMS",
    "STLAM.MI","RACE","KER.PA","ITX.MC","IBE.MC","DB1.DE","CBK.DE"
]

# ─────────────────────────────────────────
# 4. FILTRO MACRO — VIX GATE
# Regra: VIX > 25 → defensivo | VIX > 35 → modo cash
# ─────────────────────────────────────────
def get_vix():
    cached = cache_get("vix")
    if cached:
        return cached
    try:
        vix = round(yf.Ticker("^VIX").history(period="1d")['Close'].iloc[-1], 2)
    except:
        vix = 0
    cache_set("vix", vix)
    return vix

def avaliar_contexto_macro(vix: float) -> dict:
    if vix > 35:
        return {
            "modo": "CASH 🔴",
            "descricao": "Mercado em pânico. Sem novas entradas.",
            "permitir_entradas": False
        }
    elif vix > 25:
        return {
            "modo": "DEFENSIVO 🟡",
            "descricao": "Stress elevado. Apenas setores defensivos (saúde, utilities, ouro).",
            "permitir_entradas": "defensivo"
        }
    else:
        return {
            "modo": "NORMAL 🟢",
            "descricao": "Condições normais de mercado.",
            "permitir_entradas": True
        }

# ─────────────────────────────────────────
# 5. INDICADORES TÉCNICOS
# ─────────────────────────────────────────
def calcular_atr(hist, periodo=14) -> float:
    """Average True Range — mede volatilidade real do ativo."""
    high = hist['High']
    low  = hist['Low']
    close_prev = hist['Close'].shift(1)
    tr = (high - low).combine(
        (high - close_prev).abs(), max
    ).combine(
        (low - close_prev).abs(), max
    )
    return tr.rolling(window=periodo).mean().iloc[-1]

def calcular_rsi(hist, periodo=14) -> float:
    delta = hist['Close'].diff()
    gain  = delta.where(delta > 0, 0).rolling(periodo).mean()
    loss  = (-delta.where(delta < 0, 0)).rolling(periodo).mean()
    rs    = gain / loss
    return round(100 - (100 / (1 + rs.iloc[-1])), 2)

def calcular_macd(hist) -> dict:
    """MACD(12,26,9) — confirmação de tendência."""
    ema12  = hist['Close'].ewm(span=12).mean()
    ema26  = hist['Close'].ewm(span=26).mean()
    macd   = ema12 - ema26
    signal = macd.ewm(span=9).mean()
    hist_m = macd - signal
    return {
        "macd":    round(macd.iloc[-1], 4),
        "signal":  round(signal.iloc[-1], 4),
        "histo":   round(hist_m.iloc[-1], 4),
        "cruzamento": "BULLISH 🚀" if macd.iloc[-1] > signal.iloc[-1] else "BEARISH 📉"
    }

def confirmar_volume(hist) -> dict:
    """Volume actual vs média 20 dias."""
    vol_media = hist['Volume'].tail(20).mean()
    vol_atual = hist['Volume'].iloc[-1]
    ratio     = round(vol_atual / vol_media, 2) if vol_media > 0 else 0
    return {
        "confirmado": ratio >= 1.2,
        "ratio": ratio,
        "label": f"{'✅ Volume confirmado' if ratio >= 1.2 else '⚠️ Volume fraco'} ({ratio}x média)"
    }

# ─────────────────────────────────────────
# 6. GESTÃO DE RISCO PROFISSIONAL
# Banca: 2500€ | Risco por trade: 1% = 25€
# ─────────────────────────────────────────
BANCA     = 2500
RISCO_PCT = 0.01  # 1%
RISCO_EUR = BANCA * RISCO_PCT  # 25€

def calcular_posicao(p_atual: float, atr: float) -> dict:
    """
    Stop baseado em ATR (respeita ruído natural do ativo).
    3 TPs com saída parcial — como traders profissionais gerem posições.
    """
    stop   = round(p_atual - (atr * 2), 2)      # 2x ATR abaixo
    risco  = p_atual - stop

    if risco <= 0:
        return {"erro": "Stop inválido"}

    tp1 = round(p_atual + risco * 1.5, 2)   # sair 50% — garantir lucro
    tp2 = round(p_atual + risco * 2.5, 2)   # sair 30% — deixar correr
    tp3 = round(p_atual + risco * 4.0, 2)   # sair 20% — alvo máximo

    lotes       = round(RISCO_EUR / risco, 4)
    risco_real  = round(lotes * risco, 2)
    reward_tp1  = round(lotes * (tp1 - p_atual), 2)

    return {
        "stop":         stop,
        "tp1":          tp1,   "tp1_peso": "50%",
        "tp2":          tp2,   "tp2_peso": "30%",
        "tp3":          tp3,   "tp3_peso": "20%",
        "lotes":        lotes,
        "risco_eur":    risco_real,
        "reward_tp1":   reward_tp1,
        "rr_ratio":     round((tp1 - p_atual) / risco, 2),
        "atr_usado":    round(atr, 4)
    }

# ─────────────────────────────────────────
# 7. NOTÍCIAS — classificação melhorada
# ─────────────────────────────────────────
PALAVRAS_POSITIVAS = [
    "beat", "record", "growth", "upgrade", "buyback", "dividend",
    "profit", "outperform", "rally", "surge", "strong", "bullish"
]
PALAVRAS_NEGATIVAS = [
    "lawsuit", "investigation", "fraud", "miss", "cut", "recall",
    "loss", "downgrade", "crash", "warning", "risk", "bearish", "layoff"
]
PALAVRAS_MACRO = [
    "fed", "rates", "inflation", "gdp", "recession", "economy",
    "dollar", "treasury", "fomc", "jobs", "cpi", "pce"
]

def classificar_noticias(texto: str) -> dict:
    score_pos  = sum(1 for w in PALAVRAS_POSITIVAS if w in texto)
    score_neg  = sum(1 for w in PALAVRAS_NEGATIVAS if w in texto)
    score_mac  = sum(1 for w in PALAVRAS_MACRO     if w in texto)

    if score_neg > score_pos:
        sentimento = "⚠️ NEGATIVO"
        acao       = "Evitar entrada. Risco estrutural identificado."
    elif score_pos > score_neg and score_pos >= 2:
        sentimento = "✅ POSITIVO"
        acao       = "Catalisador favorável. Confirmar com volume."
    elif score_mac > 0:
        sentimento = "🟡 MACRO"
        acao       = "Evento macroeconómico. Aguardar reação do mercado."
    else:
        sentimento = "⬜ NEUTRO"
        acao       = "Sem catalisador claro. Decisão puramente técnica."

    return {
        "sentimento": sentimento,
        "acao":       acao,
        "score_pos":  score_pos,
        "score_neg":  score_neg
    }

async def buscar_noticias_async(client: httpx.AsyncClient, simbolo: str) -> dict:
    cached = cache_get(f"news_{simbolo}")
    if cached:
        return cached
    try:
        url = (
            f"https://news.google.com/rss/search"
            f"?q={simbolo}+stock+when:7d&hl=en-US&gl=US&ceid=US:en"
        )
        resp  = await client.get(url, timeout=5)
        root  = ET.fromstring(resp.content)
        items = [item.find('title').text for item in root.findall('.//item')[:3]]
        texto = " ".join(items).lower()
        resultado = {
            "titulos":      items,
            "classificacao": classificar_noticias(texto)
        }
    except:
        resultado = {
            "titulos":      ["Sem notícias disponíveis."],
            "classificacao": classificar_noticias("")
        }
    cache_set(f"news_{simbolo}", resultado)
    return resultado

# ─────────────────────────────────────────
# 8. ENDPOINT PRINCIPAL — ESTRATÉGIA
# ─────────────────────────────────────────
@app.get("/estrategia/{simbolo}")
async def gerar_estrategia(simbolo: str):
    # 8.1 Gate macro
    vix    = get_vix()
    macro  = avaliar_contexto_macro(vix)
    if not macro["permitir_entradas"]:
        return {
            "ativo":  simbolo.upper(),
            "macro":  macro,
            "sinal":  "🔴 SEM SINAL — Modo cash activo",
            "vix":    vix
        }

    try:
        t          = yf.Ticker(simbolo)
        hist_2y    = t.history(period="2y")
        hist_60d   = t.history(period="60d")
        hist_30d   = t.history(period="30d")

        if hist_60d.empty:
            return {"erro": "Dados insuficientes"}

        p_atual = hist_60d['Close'].iloc[-1]

        # 8.2 Indicadores técnicos
        atr    = calcular_atr(hist_60d)
        rsi    = calcular_rsi(hist_30d)
        macd   = calcular_macd(hist_60d)
        volume = confirmar_volume(hist_60d)

        # 8.3 EMAs
        ema20  = hist_60d['Close'].ewm(span=20).mean().iloc[-1]
        ema50  = hist_60d['Close'].ewm(span=50).mean().iloc[-1]

        # 8.4 Suportes e Resistências
        suporte_10d      = hist_60d['Low'].tail(10).min()
        suporte_critico  = hist_60d['Low'].min()
        resistencia_2y   = hist_2y['High'].max()
        rompimento_alta  = round(hist_60d['High'].tail(20).max(), 2)

        # 8.5 Gestão de risco
        posicao = calcular_posicao(p_atual, atr)

        # 8.6 Notícias (async)
        async with httpx.AsyncClient() as client:
            noticias = await buscar_noticias_async(client, simbolo)

        # 8.7 Score de confluência (0-5 fatores alinhados)
        score = 0
        fatores = []

        if p_atual > ema50:
            score += 1; fatores.append("✅ Preço acima EMA50")
        else:
            fatores.append("❌ Preço abaixo EMA50")

        if macd["cruzamento"] == "BULLISH 🚀":
            score += 1; fatores.append("✅ MACD Bullish")
        else:
            fatores.append("❌ MACD Bearish")

        if 40 <= rsi <= 60:
            score += 1; fatores.append("✅ RSI neutro (zona de entrada)")
        elif rsi < 35:
            score += 1; fatores.append("✅ RSI sobrevendido")
        else:
            fatores.append(f"⚠️ RSI {rsi} (sobrecomprado)" if rsi > 65 else f"RSI {rsi}")

        if volume["confirmado"]:
            score += 1; fatores.append("✅ Volume confirma")
        else:
            fatores.append("⚠️ Volume não confirma")

        sent = noticias["classificacao"]["sentimento"]
        if "POSITIVO" in sent:
            score += 1; fatores.append("✅ Notícias positivas")
        elif "NEGATIVO" in sent:
            fatores.append("❌ Notícias negativas")
        else:
            fatores.append("🟡 Notícias neutras/macro")

        # 8.8 Decisão final baseada no score
        if score >= 4:
            decisao = "🟢 ENTRAR — Alta confluência"
        elif score == 3:
            decisao = "🟡 AGUARDAR — Confluência parcial"
        else:
            decisao = "🔴 NÃO ENTRAR — Fraca confluência"

        pullback_msg = (
            f"⚠️ Preço esticado. Aguardar recuo aos {round(suporte_10d * 1.005, 2)}."
            if p_atual > suporte_10d * 1.03
            else "✅ Ponto de entrada defensável (próximo ao suporte)."
        )

        return {
            "ativo":            simbolo.upper(),
            "preco":            round(p_atual, 2),
            "vix":              vix,
            "macro":            macro,
            "decisao":          decisao,
            "score":            f"{score}/5",
            "fatores":          fatores,
            "tendencia":        "BULLISH 🚀" if p_atual > ema50 else "BEARISH 📉",
            "rsi":              rsi,
            "macd":             macd,
            "volume":           volume,
            "atr":              round(atr, 4),
            "suporte_10d":      round(suporte_10d, 2),
            "suporte_critico":  round(suporte_critico, 2),
            "resistencia_2y":   round(resistencia_2y, 2),
            "rompimento_alta":  rompimento_alta,
            "pullback":         pullback_msg,
            "posicao":          posicao,
            "noticias":         noticias,
            "reforco_queda":    round(suporte_critico * 1.005, 2),
        }

    except Exception as e:
        return {"erro": str(e)}

# ─────────────────────────────────────────
# 9. RADAR ASSÍNCRONO — resposta em segundos
# ─────────────────────────────────────────
async def processar_ticker_async(client: httpx.AsyncClient, simbolo: str) -> dict | None:
    cached = cache_get(f"radar_{simbolo}")
    if cached:
        return cached
    try:
        loop = asyncio.get_event_loop()
        # yfinance é síncrono — correr em thread separada para não bloquear
        t    = await loop.run_in_executor(None, lambda: yf.Ticker(simbolo))
        hist = await loop.run_in_executor(None, lambda: t.history(period="6mo"))

        if hist.empty:
            return None

        p_atual = hist['Close'].iloc[-1]
        max_p   = hist['High'].max()
        desc    = ((max_p - p_atual) / max_p) * 100

        if desc < 10:
            return None

        atr     = calcular_atr(hist)
        volume  = confirmar_volume(hist)
        noticias = await buscar_noticias_async(client, simbolo)

        resultado = {
            "ativo":       simbolo,
            "preco":       round(p_atual, 2),
            "desconto":    f"{round(desc, 1)}%",
            "atr":         round(atr, 4),
            "volume":      volume["label"],
            "sentimento":  noticias["classificacao"]["sentimento"],
            "noticia":     noticias["titulos"][0],
            "link":        "https://webtrader.fortrade.com/"
        }
        cache_set(f"radar_{simbolo}", resultado)
        return resultado
    except:
        return None

async def processar_radar_async(lista: list) -> list:
    vix   = get_vix()
    macro = avaliar_contexto_macro(vix)

    async with httpx.AsyncClient() as client:
        tasks  = [processar_ticker_async(client, s) for s in lista]
        results = await asyncio.gather(*tasks)

    oportunidades = [r for r in results if r is not None]
    oportunidades.sort(
        key=lambda x: float(x['desconto'].replace('%', '')),
        reverse=True
    )
    return {"macro": macro, "vix": vix, "data": oportunidades}

# ─────────────────────────────────────────
# 10. ENDPOINTS
# ─────────────────────────────────────────
@app.get("/")
async def dashboard(request: Request):
    vix = get_vix()
    return templates.TemplateResponse("index.html", {"request": request, "vix": vix})

@app.get("/macro")
def get_macro():
    vix = get_vix()
    return {"vix": vix, "contexto": avaliar_contexto_macro(vix)}

@app.get("/radar/usa")
async def get_usa():
    return await processar_radar_async(RADAR_USA)

@app.get("/radar/commodities")
async def get_commodities():
    return await processar_radar_async(RADAR_COMMODITIES)

@app.get("/radar/europa")
async def get_europa():
    return await processar_radar_async(RADAR_EUROPA)

@app.get("/analise/{simbolo}")
async def analise_detalhada(simbolo: str):
    try:
        hist   = yf.Ticker(simbolo).history(period="30d")
        rsi    = calcular_rsi(hist)
        macd   = calcular_macd(hist)
        volume = confirmar_volume(hist)
        atr    = calcular_atr(hist)

        if rsi < 35:    rec = "🔥 SOBREVENDIDO — Potencial entrada"
        elif rsi > 65:  rec = "⚠️ SOBRECOMPRADO — Aguardar correção"
        else:           rec = "🟡 RSI Neutro"

        return {
            "rsi":         rsi,
            "recomendacao": rec,
            "macd":        macd,
            "volume":      volume,
            "atr":         round(atr, 4),
            "suporte":     round(hist['Low'].min(), 2)
        }
    except Exception as e:
        return {"erro": str(e)}
