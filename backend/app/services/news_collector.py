"""
Módulo: news_collector.py
Data: 31 Julho 2026

Camada 1 — Recolha determinística de headlines financeiras via Yahoo Finance RSS.
Sem chave de API, sem custo. Dados reais, fonte identificada.
Nenhuma interpretação de causalidade ou análise de sentimento calculada por nós.

Fonte: Yahoo Finance RSS (stdlib xml.etree.ElementTree)
  Por ticker: https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}
  Macro geral: https://finance.yahoo.com/rss/topstories (top stories gerais)

Base de dados Notion:
    Feed de Notícias: e1c8d3ab-a151-499f-8931-4537f29933ec

Melhorias & Correções Aplicadas:
    1. Filtro de exclusão de ruído (finanças pessoais: mortgage rates, HELOC, etc.)
    2. Categorização determinística refinada (Earnings, Geopolítico, Empresa Específica, Macro Geral)
    3. Tagging determinístico de tickers examinando TÍTULO + META-DESCRIÇÃO com mapa expandido de aliases
    4. Remoção da associação forçada de ^GSPC em notícias de feeds genéricos/índice
    5. Extração mecânica de meta-descrição (zero LLM / BeautifulSoup) com falha tolerante
"""

import os
import re
import hashlib
import logging
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime  # Parse de datas RFC 2822 do RSS
from typing import Dict, List, Optional, Any

import requests
from sqlalchemy import text

try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False

from app.database import engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# ─── Constantes ─────────────────────────────────────────────────────────────

# Yahoo Finance RSS — sem API key, dados reais
YF_RSS_TICKER_URL = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US"
YF_RSS_MACRO_URLS = [
    "https://finance.yahoo.com/rss/topstories",  # Top stories gerais
]
YF_RSS_TIMEOUT = 12  # segundos por chamada RSS

NOTION_TOKEN = os.getenv("NOTION_TOKEN") or os.getenv("NOTION_API_KEY", "")
NOTION_NEWS_DB_ID = os.getenv("NOTION_NEWS_DB_ID", "e1c8d3ab-a151-499f-8931-4537f29933ec")
NOTION_CONFIG_DB_ID = os.getenv("NOTION_CONFIG_DB_ID", "fb3a2102-c785-46c9-b2b4-5adecd9d5482")

NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28"
}

# Máximo de dias para filtrar notícias (backfill inicial = 7 dias)
BACKFILL_DAYS = 7

# Filtro de Exclusão — Ruído de Finanças Pessoais & Cartões de Crédito
PALAVRAS_EXCLUSAO = [
    "mortgage rate", "cd rate", "savings rate", "heloc",
    "refinance", "home equity loan", "apy return", "best high-yield",
    "refinance interest rate", "mortgage rate predictions", "best cd rates",
    "home equity", "personal finance", "savings account", "checking account",
    "chase sapphire", "credit card", "credit cards", "rewards card", "no savings",
    "financial advice", "budgeting", "student loan", "car loan", "rewards credit card",
    "cash back card", "best credit card", "travel card"
]

PALAVRAS_GEOPOLITICAS = [
    "war", "conflict", "sanctions", "military", "attack", "invasion",
    "geopolitical", "nuclear", "nato", "missile", "troops", "ceasefire",
    "embargo", "terrorism", "coup", "assassination"
]

PALAVRAS_EARNINGS = [
    "earnings", "results", "revenue", "profit", "eps", "guidance",
    "quarterly", "beat", "miss", "outlook", "forecast"
]

# Tickers de Índices e Macro que NUNCA são "Empresa Específica"
MACRO_INDEX_TICKERS = {
    "^NDX", "^GSPC", "^VIX", "^SOX", "^STOXX50E", "^TNX", "^TYX", "^DJI", "^RUT",
    "DX-Y.NYB", "DGS2", "GC=F", "BZ=F", "CL=F", "HG=F", "SI=F", "NG=F", "TLT",
    "EURUSD=X", "USDJPY=X", "GBPUSD=X", "USDCNH=X", "USDCHF=X"
}

# Nomes de fallback e aliases expandidos para correspondência de texto em títulos e resumos
DEFAULT_TICKER_NAMES = {
    "^NDX": "Nasdaq",
    "^GSPC": "S&P 500",
    "^VIX": "VIX",
    "^SOX": "Semiconductor",
    "^STOXX50E": "Euro Stoxx 50",
    "^TNX": "10-Year Treasury Yield",
    "GC=F": "Gold",
    "BZ=F": "Brent Crude Oil",
    "HG=F": "Copper",
    "TLT": "Treasury Bond",
    "EURUSD=X": "EUR/USD",
    "USDJPY=X": "USD/JPY",
    "O": "Realty Income",
    "DX-Y.NYB": "US Dollar Index",
    "DGS2": "2-Year Treasury Yield",
    "AAPL": "Apple",
    "MSFT": "Microsoft",
    "NVDA": "Nvidia",
    "TSLA": "Tesla",
    "AMZN": "Amazon",
    "GOOGL": "Google",
    "META": "Meta",
}

# Mapa de aliases por palavras-chave para correspondência determinística
TICKER_KEYWORD_ALIASES: Dict[str, List[str]] = {
    "^NDX": ["nasdaq", "ndx", "tech stocks", "qqq"],
    "^GSPC": ["s&p", "s&p 500", "sp500", "sp 500", "gspc"],
    "^VIX": ["vix", "volatility index", "fear index"],
    "^SOX": ["sox", "semiconductor", "semiconductors", "chip stocks", "chips", "phlx semiconductor"],
    "^STOXX50E": ["stoxx", "euro stoxx", "stoxx 50", "european stocks"],
    "^TNX": ["10-year yield", "10-year treasury", "treasury yield", "spiking yields", "spiking yield", "10-yr yield"],
    "DGS2": ["2-year yield", "2-year treasury", "2-yr yield"],
    "TLT": ["tlt", "treasury bond", "treasury bonds", "bond market", "bond yields"],
    "GC=F": ["gold", "ouro", "bullion"],
    "BZ=F": ["brent", "crude oil", "oil prices", "oil price", "petróleo"],
    "HG=F": ["copper", "cobre"],
    "EURUSD=X": ["eur/usd", "eurusd", "euro dollar"],
    "USDJPY=X": ["usd/jpy", "usdjpy", "yen"],
    "O": ["realty income"],
    "DX-Y.NYB": ["dollar index", "dxy", "us dollar index"],
    "COIN": ["coinbase", "coin"],  # Tagging 100% literal — apenas menções diretas a Coinbase/COIN
    "AAPL": ["apple", "iphone"],
    "MSFT": ["microsoft", "azure"],
    "NVDA": ["nvidia", "nvda"],
    "TSLA": ["tesla", "elon musk", "spacex"],
    "AMZN": ["amazon", "aws"],
    "GOOGL": ["google", "alphabet"],
    "META": ["meta", "facebook", "instagram"],
}


# ─── Helpers — Filtro de Ruído & Extração ──────────────────────────────────

def eh_conteudo_irrelevante(titulo: str) -> bool:
    """
    Filtro de exclusão de ruído: identifica artigos de finanças pessoais sem relevância para trading.
    Retorna True para artigos irrelevantes que NÃO devem ser gravados na tabela.
    """
    if not titulo:
        return True
    titulo_lower = titulo.lower()
    return any(palavra in titulo_lower for palavra in PALAVRAS_EXCLUSAO)


def detetar_tickers_mencionados(texto: str, watchlist_map: Dict[str, str]) -> List[str]:
    """
    Verifica no texto (título + meta-descrição) quais tickers/nomes/aliases da nossa
    Configuração de Vigilância aparecem mencionados.
    Correspondência de texto determinística.
    """
    if not texto:
        return []
    texto_lower = texto.lower()
    mencionados = []

    # Se o texto é sobre Crypto (bitcoin, xrp, ethereum, etc.), ignorar match acidental de ^GSPC por frases de rodapé
    is_crypto_article = any(c_kw in texto_lower for c_kw in ["bitcoin", "xrp", "crypto", "cryptocurrency", "ethereum", "solana"])

    for ticker, nome_empresa in watchlist_map.items():
        if ticker == "^GSPC" and is_crypto_article:
            # Só aceitar ^GSPC em artigos de crypto se "s&p 500" ou "sp500" estiver no próprio título
            if "s&p 500" not in texto_lower and "sp500" not in texto_lower:
                continue

        matched = False

        # 1. Match por aliases configurados (ex: "chip stocks", "sox", "gold", "10-year yield", "nasdaq")
        aliases = TICKER_KEYWORD_ALIASES.get(ticker, [])
        for alias in aliases:
            if alias in texto_lower:
                matched = True
                break

        # 2. Match pelo nome da empresa vindo da tabela Notion (se >= 3 chars)
        if not matched and nome_empresa and len(nome_empresa.strip()) >= 3:
            if nome_empresa.lower() in texto_lower:
                matched = True

        # 3. Match pelo símbolo do ticker limpo (ex: NDX, AAPL, COIN, VIX, TLT)
        if not matched:
            ticker_clean = ticker.strip("^").replace("=X", "").replace("=F", "").replace("-Y.NYB", "")
            if ticker_clean and len(ticker_clean) >= 3:
                pattern = r'\b' + re.escape(ticker_clean) + r'\b'
                if re.search(pattern, texto, re.IGNORECASE):
                    matched = True

        if matched and ticker not in mencionados:
            mencionados.append(ticker)

    return mencionados


def extrair_resumo_mecanico(url_artigo: str) -> Optional[str]:
    """
    Extração mecânica da meta-descrição ou primeiro parágrafo da página do artigo.
    Zero interpretação por LLM — só HTML. Tolerante a falhas (timeout 4s).
    """
    if not url_artigo or not HAS_BS4:
        return None
    try:
        resp = requests.get(
            url_artigo,
            timeout=4,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        )
        if resp.status_code != 200:
            return None

        soup = BeautifulSoup(resp.text, 'html.parser')

        # 1. <meta name="description" content="...">
        meta_desc = soup.find('meta', attrs={'name': 'description'})
        if meta_desc and meta_desc.get('content'):
            desc = meta_desc['content'].strip()
            if desc:
                return desc[:2000]

        # 2. <meta property="og:description" content="...">
        og_desc = soup.find('meta', attrs={'property': 'og:description'})
        if og_desc and og_desc.get('content'):
            desc = og_desc['content'].strip()
            if desc:
                return desc[:2000]

        # 3. Primeiro parágrafo <p>
        p = soup.find('p')
        if p and p.get_text():
            text_p = p.get_text().strip()
            if text_p:
                return text_p[:2000]

        return None
    except Exception as e:
        logging.warning(f"  ⚠️ Falha ao extrair resumo de {url_artigo[:60]}: {e}")
        return None


# ─── Helpers — Yahoo Finance RSS ──────────────────────────────────────────

def _parse_rss_date(date_str: str) -> datetime:
    """Parse de data RSS (RFC 2822) para datetime UTC."""
    try:
        return parsedate_to_datetime(date_str).astimezone(timezone.utc)
    except Exception:
        return datetime.min.replace(tzinfo=timezone.utc)


def _fetch_rss(url: str) -> List[Dict]:
    """Faz GET ao feed RSS e devolve lista de artigos normalizados."""
    try:
        res = requests.get(url, timeout=YF_RSS_TIMEOUT, headers={"User-Agent": "Mozilla/5.0"})
        if res.status_code != 200:
            logging.warning(f"⚠️ RSS {url[:60]}... devolveu HTTP {res.status_code}")
            return []

        root = ET.fromstring(res.content)
        artigos = []

        for item in root.iter("item"):
            titulo = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            pub_date = (item.findtext("pubDate") or "").strip()
            source_el = item.find("source")
            publisher = ""
            if source_el is not None:
                publisher = (source_el.text or source_el.get("url", "") or "").strip()
            if not publisher:
                publisher = "Yahoo Finance"

            if not titulo or not link:
                continue

            artigos.append({
                "title": titulo,
                "url": link,
                "publishedDate": _parse_rss_date(pub_date).strftime("%Y-%m-%d %H:%M:%S") if pub_date else "",
                "publisher": publisher,
                "site": publisher,
            })

        return artigos

    except ET.ParseError as e:
        logging.warning(f"⚠️ Erro ao parsear XML do RSS {url[:60]}: {e}")
        return []
    except Exception as e:
        logging.error(f"❌ Erro ao obter RSS {url[:60]}: {e}")
        return []


def fetch_stock_news_batch(from_date: str, to_date: str, page: int = 0) -> List[Dict]:
    """Alias para compatibilidade."""
    return []


def fetch_stock_news(ticker: str, from_date: str, to_date: str) -> List[Dict]:
    """Recolhe notícias de um ticker via Yahoo Finance RSS."""
    url = YF_RSS_TICKER_URL.format(ticker=requests.utils.quote(ticker, safe=""))
    logging.info(f"  📰 RSS Yahoo Finance para [{ticker}]...")
    artigos = _fetch_rss(url)
    time.sleep(0.3)

    desde = _parse_str_date(from_date)
    ate = _parse_str_date(to_date) + timedelta(days=1)
    return [
        a for a in artigos
        if desde <= _parse_pub_date(a.get("publishedDate", "")) <= ate
    ]


def fetch_general_news(page: int = 0) -> List[Dict]:
    """Recolhe notícias macro gerais via Yahoo Finance RSS top stories."""
    if page >= len(YF_RSS_MACRO_URLS):
        return []
    url = YF_RSS_MACRO_URLS[page]
    logging.info(f"  🌍 RSS macro [{url.split('/')[-1][:40]}]...")
    artigos = _fetch_rss(url)
    time.sleep(0.3)
    return artigos


def _parse_str_date(date_str: str) -> datetime:
    """Converte string YYYY-MM-DD para datetime UTC."""
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except Exception:
        return datetime.min.replace(tzinfo=timezone.utc)


def _parse_pub_date(date_str: str) -> datetime:
    """Converte string YYYY-MM-DD HH:MM:SS para datetime UTC."""
    try:
        return datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except Exception:
        return datetime.min.replace(tzinfo=timezone.utc)


# ─── Helpers — Configuração de Vigilância ────────────────────────────────────

def get_active_watchlist_map() -> Dict[str, str]:
    """
    Lê a Configuração de Vigilância e devolve um dicionário {ticker: nome_empresa}.
    Se o nome estiver vazio na tabela Notion, usa o fallback de DEFAULT_TICKER_NAMES.
    """
    if not NOTION_TOKEN:
        logging.error("❌ NOTION_TOKEN não configurado.")
        return dict(DEFAULT_TICKER_NAMES)

    url = f"https://api.notion.com/v1/databases/{NOTION_CONFIG_DB_ID}/query"
    try:
        res = requests.post(url, headers=NOTION_HEADERS, json={}, timeout=10)
        if res.status_code != 200:
            logging.error(f"❌ Erro ao consultar Configuração de Vigilância: {res.status_code}")
            return dict(DEFAULT_TICKER_NAMES)

        mapping = {}
        for item in res.json().get("results", []):
            props = item.get("properties", {})
            ativo = props.get("Ativo", {}).get("checkbox", False)
            if not ativo:
                continue
            ticker_list = props.get("Ticker", {}).get("title", []) or props.get("Name", {}).get("title", [])
            ticker = ticker_list[0].get("text", {}).get("content", "").strip() if ticker_list else ""

            nome_list = props.get("Nome", {}).get("rich_text", [])
            nome = nome_list[0].get("text", {}).get("content", "").strip() if nome_list else ""

            if not nome and ticker in DEFAULT_TICKER_NAMES:
                nome = DEFAULT_TICKER_NAMES[ticker]

            if ticker:
                mapping[ticker] = nome or ticker

        logging.info(f"✅ {len(mapping)} tickers ativos lidos com nomes: {mapping}")
        return mapping if mapping else dict(DEFAULT_TICKER_NAMES)
    except Exception as e:
        logging.error(f"❌ Falha ao ler Configuração de Vigilância: {e}")
        return dict(DEFAULT_TICKER_NAMES)


def get_active_tickers() -> List[str]:
    """Devolve lista de tickers com Ativo=True."""
    mapping = get_active_watchlist_map()
    return list(mapping.keys())


# ─── Helpers — Categorização ─────────────────────────────────────────────────

def _tem_earnings_proximos(ticker: str, dias: int = 3) -> bool:
    """Verifica se o ticker tem earnings nos próximos N dias via MySQL."""
    try:
        hoje = datetime.now(timezone.utc).date()
        limite = hoje + timedelta(days=dias)
        with engine.connect() as conn:
            row = conn.execute(text("""
                SELECT COUNT(*) FROM corporate_earnings_calendar
                WHERE ticker = :ticker
                  AND earnings_date BETWEEN :hoje AND :limite
            """), {"ticker": ticker, "hoje": str(hoje), "limite": str(limite)}).fetchone()
            return row[0] > 0 if row else False
    except Exception:
        return False


def categorizar_noticia(ticker_relacionado: str, texto_titulo: str) -> str:
    """
    Categorização determinística por regra — NUNCA por LLM.
    Retorna uma das 4 categorias do schema Notion:
      - 'Earnings-Relacionado'
      - 'Geopolítico'
      - 'Empresa Específica'
      - 'Macro Geral'
    """
    titulo_lower = (texto_titulo or "").lower()

    # 1. Earnings por palavras-chave
    if any(p in titulo_lower for p in PALAVRAS_EARNINGS):
        return "Earnings-Relacionado"

    # 2. Earnings por calendário MySQL se há ticker
    if ticker_relacionado:
        primeiro_ticker = ticker_relacionado.split(",")[0].strip()
        if _tem_earnings_proximos(primeiro_ticker, dias=3):
            return "Earnings-Relacionado"

    # 3. Geopolítico
    if any(p in titulo_lower for p in PALAVRAS_GEOPOLITICAS):
        return "Geopolítico"

    # 4. Empresa Específica (se tem ticker relacionado de empresa individual)
    if ticker_relacionado:
        tickers = [t.strip() for t in ticker_relacionado.split(",") if t.strip()]
        has_equity_stock = any(t not in MACRO_INDEX_TICKERS for t in tickers)
        if has_equity_stock:
            return "Empresa Específica"

    # 5. Default
    return "Macro Geral"


# ─── Helpers — Deduplicação ──────────────────────────────────────────────────

def _url_hash_fallback(titulo: str, fonte: str, timestamp_pub: str) -> str:
    """Gera hash de fallback quando não há URL disponível."""
    raw = f"{titulo}|{fonte}|{timestamp_pub}"
    return "hash:" + hashlib.sha256(raw.encode()).hexdigest()[:16]


def _notion_query_by_id_externo(id_externo: str) -> bool:
    """Verifica se já existe uma notícia com este ID Fonte Externa no Notion."""
    url = f"https://api.notion.com/v1/databases/{NOTION_NEWS_DB_ID}/query"
    payload = {
        "filter": {
            "property": "ID Fonte Externa",
            "rich_text": {"equals": id_externo}
        },
        "page_size": 1
    }
    try:
        res = requests.post(url, headers=NOTION_HEADERS, json=payload, timeout=10)
        time.sleep(0.35)
        if res.status_code == 200:
            return len(res.json().get("results", [])) > 0
        return False
    except Exception as e:
        logging.warning(f"⚠️ Erro ao verificar deduplicação para [{id_externo}]: {e}")
        return False


def _notion_create_news_page(props: Dict) -> bool:
    """Cria uma nova página na tabela Feed de Notícias do Notion."""
    url = "https://api.notion.com/v1/pages"
    payload = {
        "parent": {"database_id": NOTION_NEWS_DB_ID},
        "properties": props
    }
    for attempt in range(1, 4):
        try:
            res = requests.post(url, headers=NOTION_HEADERS, json=payload, timeout=15)
            time.sleep(0.35)
            if res.status_code in [200, 201]:
                return True
            elif res.status_code == 429:
                time.sleep(2.0 * attempt)
            else:
                logging.warning(f"⚠️ Notion create page HTTP {res.status_code}: {res.text[:200]}")
                return False
        except Exception as e:
            if attempt == 3:
                logging.warning(f"⚠️ Falhou após 3 tentativas: {e}")
            else:
                time.sleep(1.5 * attempt)
    return False


# ─── Upsert Principal ─────────────────────────────────────────────────────────

def upsert_noticia(
    artigo: Dict,
    ticker_relacionado: str = "",
    watchlist_map: Optional[Dict[str, str]] = None
) -> bool:
    """
    Insere uma notícia no Notion se ainda não existir (deduplicação por ID Fonte Externa).
    Aplica filtro de ruído (finanças pessoais), tagging por correspondência de texto no título + resumo,
    e extração tolerante a falhas de meta-descrição.
    """
    titulo = (artigo.get("title") or "").strip()
    fonte = (artigo.get("publisher") or artigo.get("site") or artigo.get("source") or "").strip()

    # ── Validações Anti-Mock & Filtro de Ruído (Problema 1) ───────────────────
    if not titulo:
        logging.debug("  ⏭️ Artigo sem título — ignorado.")
        return False
    if not fonte or fonte.lower() in ["unknown", "n/a", "", "mock", "test"]:
        logging.debug(f"  ⏭️ Fonte não identificada [{fonte}] — ignorado (filtro anti-mock).")
        return False

    if eh_conteudo_irrelevante(titulo):
        logging.debug(f"  ⏭️ Finanças pessoais irrelevante ignorado: [{titulo[:50]}]")
        return False

    # ── Identificador externo (chave de deduplicação) ─────────────────────────
    url_artigo = (artigo.get("url") or "").strip()
    timestamp_pub = (artigo.get("publishedDate") or "").strip()
    id_externo = url_artigo if url_artigo else _url_hash_fallback(titulo, fonte, timestamp_pub)

    # ── Verificar se já existe ────────────────────────────────────────────────
    if _notion_query_by_id_externo(id_externo):
        logging.debug(f"  ⏭️ Já existe no Notion: [{titulo[:50]}]")
        return False

    # ── Extração Mecânica de Substância (Meta-Descrição) ─────────────────────
    resumo_meta = None
    if url_artigo:
        resumo_meta = extrair_resumo_mecanico(url_artigo)

    # Texto completo para análise de tagging (título + resumo)
    texto_completo = f"{titulo}. {resumo_meta or ''}"

    # ── Tagging (Problema 3): Detetar Tickers Mencionados no Título e Resumo ────
    if watchlist_map is None:
        watchlist_map = DEFAULT_TICKER_NAMES

    mencionados = detetar_tickers_mencionados(texto_completo, watchlist_map)

    # Se a notícia veio de um pedido de RSS de ticker específico (e NÃO de um índice genérico como ^GSPC),
    # adiciona o ticker de contexto apenas se não foi detetado nada ou se é um ticker de empresa real
    if ticker_relacionado and ticker_relacionado not in ["^GSPC", "^GSPC_MACRO", ""]:
        if ticker_relacionado not in mencionados:
            mencionados.insert(0, ticker_relacionado)

    ticker_relacionado_str = ", ".join(mencionados)

    # ── Timestamp de publicação ───────────────────────────────────────────────
    try:
        dt_pub = datetime.strptime(timestamp_pub[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        ts_pub_iso = dt_pub.strftime("%Y-%m-%dT%H:%M:%S+00:00")
    except Exception:
        ts_pub_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")

    ts_ingestao_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")

    # ── Categorização determinística (Problema 2) ──────────────────────────────
    categoria = categorizar_noticia(ticker_relacionado_str, titulo)

    # ── Sentimento (copiado da FMP se disponível; 'Não Fornecido' caso contrário) ──
    sentimento_raw = artigo.get("sentiment") or artigo.get("overallSentiment") or ""
    sentimento_map = {"Positive": "Positivo", "Negative": "Negativo", "Neutral": "Neutro"}
    sentimento = sentimento_map.get(sentimento_raw, "Não Fornecido")

    # ── Build Notion properties ───────────────────────────────────────────────
    notion_props: Dict[str, Any] = {
        "Título": {"title": [{"text": {"content": titulo[:2000]}}]},
        "Fonte": {"rich_text": [{"text": {"content": fonte[:200]}}]},
        "Timestamp Publicação": {"date": {"start": ts_pub_iso}},
        "Timestamp Ingestão": {"date": {"start": ts_ingestao_iso}},
        "Ticker(s) Relacionado(s)": {"rich_text": [{"text": {"content": ticker_relacionado_str}}]},
        "Categoria": {"select": {"name": categoria}},
        "Fonte de Registo": {"select": {"name": "Automático (Cron)"}},
        "ID Fonte Externa": {"rich_text": [{"text": {"content": id_externo[:2000]}}]},
        "Sentimento (Fonte)": {"select": {"name": sentimento}},
        "Resumo (Meta-descrição)": {"rich_text": [{"text": {"content": (resumo_meta or "")[:2000]}}] if resumo_meta else []},
    }

    if url_artigo:
        notion_props["URL"] = {"url": url_artigo}

    if _notion_create_news_page(notion_props):
        logging.info(f"  ✅ Inserido: [{categoria}] ({ticker_relacionado_str or 'Sem Ticker'}) {titulo[:50]}...")
        return True

    return False


# ─── Pipeline Principal ───────────────────────────────────────────────────────

def run_news_collection(backfill: bool = False) -> Dict[str, int]:
    """
    Pipeline completo de recolha de notícias via Yahoo Finance RSS.
    """
    if not NOTION_TOKEN:
        logging.error("❌ NOTION_TOKEN não definido. Abortando recolha de notícias.")
        return {"inserted": 0, "skipped": 0, "errors": 0}

    hoje = datetime.now(timezone.utc)
    if backfill:
        desde = hoje - timedelta(days=BACKFILL_DAYS)
        logging.info(f"📰 [MODO BACKFILL] Recolhendo notícias dos últimos {BACKFILL_DAYS} dias ({desde.date()} → {hoje.date()})...")
    else:
        desde = hoje - timedelta(hours=2)
        logging.info(f"📰 [MODO CRON] Recolhendo notícias das últimas 2h ({desde.isoformat()[:16]} UTC)...")

    from_str = desde.strftime("%Y-%m-%d")
    to_str = hoje.strftime("%Y-%m-%d")

    # Ler mapa de watchlist uma única vez
    watchlist_map = get_active_watchlist_map()
    tickers = list(watchlist_map.keys())

    stats = {"inserted": 0, "skipped": 0, "errors": 0}

    # ── 1. Notícias por ticker ativo (RSS Yahoo Finance) ─────────────────────
    logging.info(f"📊 1/2 Notícias por ticker ({len(tickers)} ativos via RSS)...")

    for ticker in tickers:
        try:
            artigos = fetch_stock_news(ticker, from_str, to_str)
            if not backfill:
                artigos = [a for a in artigos if _parse_pub_date(a.get("publishedDate", "")) >= desde]
            logging.info(f"  [{ticker}]: {len(artigos)} artigo(s) no período")
            for artigo in artigos:
                result = upsert_noticia(artigo, ticker_relacionado=ticker, watchlist_map=watchlist_map)
                stats["inserted" if result else "skipped"] += 1
        except Exception as e:
            logging.warning(f"⚠️ Erro ao processar RSS de [{ticker}]: {e}")
            stats["errors"] += 1

    # ── 2. Notícias macro gerais (Yahoo Finance top stories + S&P 500) ────────
    logging.info(f"🌍 2/2 Notícias macro gerais ({len(YF_RSS_MACRO_URLS)} feeds RSS)...")

    for page in range(len(YF_RSS_MACRO_URLS)):
        try:
            artigos = fetch_general_news(page=page)
            if not backfill:
                artigos = [a for a in artigos if _parse_pub_date(a.get("publishedDate", "")) >= desde]
            logging.info(f"  Macro feed {page}: {len(artigos)} artigo(s) no período")
            for artigo in artigos:
                result = upsert_noticia(artigo, ticker_relacionado="", watchlist_map=watchlist_map)
                stats["inserted" if result else "skipped"] += 1
        except Exception as e:
            logging.warning(f"⚠️ Erro ao processar RSS macro [{page}]: {e}")
            stats["errors"] += 1

    logging.info(
        f"🎉 Recolha concluída! "
        f"Inseridas: {stats['inserted']} | Já existentes: {stats['skipped']} | Erros: {stats['errors']}"
    )
    return stats
