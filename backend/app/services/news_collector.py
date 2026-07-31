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

Fluxo:
    1. Ler tickers ativos da Configuração de Vigilância (Ativo=True)
    2. Recolher RSS por ticker
    3. Recolher RSS macro (top stories)
    4. Categorizar por regra determinística (nunca por LLM)
    5. Deduplicar por URL
    6. Upsert no Notion — nunca sobrescrever notícias existentes
"""

import os
import hashlib
import logging
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime  # Parse de datas RFC 2822 do RSS
from typing import Dict, List, Optional, Any

import requests
from sqlalchemy import text

from app.database import engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# ─── Constantes ─────────────────────────────────────────────────────────────

# Yahoo Finance RSS — sem API key, dados reais
YF_RSS_TICKER_URL = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US"
YF_RSS_MACRO_URLS = [
    "https://finance.yahoo.com/rss/topstories",           # Top stories gerais
    "https://finance.yahoo.com/rss/2.0/headline?s=%5EGSPC",  # S&P 500 news
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

PALAVRAS_GEOPOLITICAS = [
    "war", "conflict", "sanctions", "military", "attack", "invasion",
    "geopolitical", "nuclear", "nato", "missile", "troops", "ceasefire",
    "embargo", "terrorism", "coup", "assassination"
]

PALAVRAS_EARNINGS = [
    "earnings", "results", "revenue", "profit", "eps", "guidance",
    "quarterly", "beat", "miss", "outlook", "forecast"
]


# ─── Helpers — Yahoo Finance RSS ──────────────────────────────────────────

def _parse_rss_date(date_str: str) -> datetime:
    """Parse de data RSS (RFC 2822) para datetime UTC. Ex: 'Thu, 31 Jul 2026 10:00:00 +0000'."""
    try:
        return parsedate_to_datetime(date_str).astimezone(timezone.utc)
    except Exception:
        return datetime.min.replace(tzinfo=timezone.utc)


def _fetch_rss(url: str) -> List[Dict]:
    """
    Faz GET ao feed RSS e devolve lista de artigos normalizados.
    Cada artigo tem: title, url, publishedDate, publisher, symbol (opcional).
    """
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
            # 'source' pode ser tag direta ou atributo
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
                "site": publisher,  # compat com upsert_noticia que lê 'site' como fallback
            })

        return artigos

    except ET.ParseError as e:
        logging.warning(f"⚠️ Erro ao parsear XML do RSS {url[:60]}: {e}")
        return []
    except Exception as e:
        logging.error(f"❌ Erro ao obter RSS {url[:60]}: {e}")
        return []


def fetch_stock_news_batch(from_date: str, to_date: str, page: int = 0) -> List[Dict]:
    """
    Recolhe notícias de todos os tickers ativos via Yahoo Finance RSS.
    Chamada por ticker individualmente (RSS não tem batch), mas gratuito e sem limites de quota.
    Nota: 'page' ignorado (RSS não pagina), mantido por compatibilidade de assinatura.
    """
    return []


def fetch_stock_news(ticker: str, from_date: str, to_date: str) -> List[Dict]:
    """Recolhe notícias de um ticker via Yahoo Finance RSS."""
    url = YF_RSS_TICKER_URL.format(ticker=requests.utils.quote(ticker, safe=""))
    logging.info(f"  📰 RSS Yahoo Finance para [{ticker}]...")
    artigos = _fetch_rss(url)
    time.sleep(0.3)  # Cortesia ao servidor

    # Filtrar por intervalo de datas
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

def get_active_tickers() -> List[str]:
    """
    Lê a Configuração de Vigilância e devolve tickers com Ativo=True.
    Não filtra por 'Vigiado Por' — notícias são partilhadas entre Claude e Gemini.
    """
    if not NOTION_TOKEN:
        logging.error("❌ NOTION_TOKEN não configurado.")
        return []

    url = f"https://api.notion.com/v1/databases/{NOTION_CONFIG_DB_ID}/query"
    try:
        res = requests.post(url, headers=NOTION_HEADERS, json={}, timeout=10)
        if res.status_code != 200:
            logging.error(f"❌ Erro ao consultar Configuração de Vigilância: {res.status_code}")
            return []

        tickers = []
        for item in res.json().get("results", []):
            props = item.get("properties", {})
            ativo = props.get("Ativo", {}).get("checkbox", False)
            if not ativo:
                continue
            ticker_list = props.get("Ticker", {}).get("title", []) or props.get("Name", {}).get("title", [])
            ticker = ticker_list[0].get("text", {}).get("content", "").strip() if ticker_list else ""
            if ticker:
                tickers.append(ticker)

        logging.info(f"✅ {len(tickers)} tickers ativos lidos: {tickers}")
        return tickers
    except Exception as e:
        logging.error(f"❌ Falha ao ler Configuração de Vigilância: {e}")
        return []


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
    Retorna uma das 4 categorias do schema Notion.
    """
    if ticker_relacionado:
        titulo_lower = texto_titulo.lower()
        if any(p in titulo_lower for p in PALAVRAS_EARNINGS):
            return "Earnings-Relacionado"
        if _tem_earnings_proximos(ticker_relacionado, dias=3):
            return "Earnings-Relacionado"
        return "Empresa Específica"

    titulo_lower = texto_titulo.lower()
    if any(p in titulo_lower for p in PALAVRAS_GEOPOLITICAS):
        return "Geopolítico"

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

def upsert_noticia(artigo: Dict, ticker_relacionado: str = "") -> bool:
    """
    Insere uma notícia no Notion se ainda não existir (deduplicação por ID Fonte Externa).
    Nunca atualiza notícias existentes — só acumulam.
    Rejeita artigos sem título ou de fonte não identificada (filtro anti-mock).
    """
    # ── Validações Anti-Mock ─────────────────────────────────────────────────
    titulo = (artigo.get("title") or "").strip()
    # 'publisher' é o campo da nova API /stable/ ; 'site' mantido para compatibilidade
    fonte = (artigo.get("publisher") or artigo.get("site") or artigo.get("source") or "").strip()

    if not titulo:
        logging.debug("  ⏭️ Artigo sem título — ignorado.")
        return False
    if not fonte or fonte.lower() in ["unknown", "n/a", "", "mock", "test"]:
        logging.debug(f"  ⏭️ Fonte não identificada [{fonte}] — ignorado (filtro anti-mock).")
        return False

    # ── Identificador externo (chave de deduplicação) ─────────────────────────
    url_artigo = (artigo.get("url") or "").strip()
    timestamp_pub = (artigo.get("publishedDate") or "").strip()
    id_externo = url_artigo if url_artigo else _url_hash_fallback(titulo, fonte, timestamp_pub)

    # ── Verificar se já existe ────────────────────────────────────────────────
    if _notion_query_by_id_externo(id_externo):
        logging.debug(f"  ⏭️ Já existe no Notion: [{titulo[:50]}]")
        return False

    # ── Timestamp de publicação (datetime completo com timezone — Notion requer hora) ────
    try:
        dt_pub = datetime.strptime(timestamp_pub[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        ts_pub_iso = dt_pub.strftime("%Y-%m-%dT%H:%M:%S+00:00")  # Formato que o Notion exige para datetime
    except Exception:
        ts_pub_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")

    ts_ingestao_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")

    # ── Categorização determinística ──────────────────────────────────────────
    categoria = categorizar_noticia(ticker_relacionado, titulo)

    # ── Sentimento (copiado da FMP se disponível; 'Não Fornecido' caso contrário — nunca calculado) ──
    sentimento_raw = artigo.get("sentiment") or artigo.get("overallSentiment") or ""
    sentimento_map = {"Positive": "Positivo", "Negative": "Negativo", "Neutral": "Neutro"}
    sentimento = sentimento_map.get(sentimento_raw, "Não Fornecido")

    # ── Build Notion properties ───────────────────────────────────────────────
    notion_props: Dict[str, Any] = {
        "Título": {"title": [{"text": {"content": titulo[:2000]}}]},
        "Fonte": {"rich_text": [{"text": {"content": fonte[:200]}}]},
        "Timestamp Publicação": {"date": {"start": ts_pub_iso}},
        "Timestamp Ingestão": {"date": {"start": ts_ingestao_iso}},
        "Ticker(s) Relacionado(s)": {"rich_text": [{"text": {"content": ticker_relacionado}}]},
        "Categoria": {"select": {"name": categoria}},
        "Fonte de Registo": {"select": {"name": "Automático (Cron)"}},
        "ID Fonte Externa": {"rich_text": [{"text": {"content": id_externo[:2000]}}]},
        "Sentimento (Fonte)": {"select": {"name": sentimento}},  # Sempre enviado — nunca omitido
    }

    if url_artigo:
        notion_props["URL"] = {"url": url_artigo}

    if _notion_create_news_page(notion_props):
        logging.info(f"  ✅ Inserido: [{categoria}] {titulo[:60]}...")
        return True

    return False


# ─── Pipeline Principal ───────────────────────────────────────────────────────

def run_news_collection(backfill: bool = False) -> Dict[str, int]:
    """
    Pipeline completo de recolha de notícias via Yahoo Finance RSS.

    Sem chave de API. Dados reais e gratuitos.
    Estratégia:
      1. Por cada ticker ativo → fetch_stock_news (RSS Yahoo Finance)
      2. Notícias macro gerais → fetch_general_news (Yahoo Finance top stories + S&P 500)

    backfill=True : últimos 7 dias
    backfill=False: últimas 2h (cron de 30 min com margem)
    """
    if not NOTION_TOKEN:
        logging.error("❌ NOTION_TOKEN não definido. Abortando recolha de notícias.")
        return {"inserted": 0, "skipped": 0, "errors": 0}

    hoje = datetime.now(timezone.utc)
    if backfill:
        desde = hoje - timedelta(days=BACKFILL_DAYS)
        logging.info(f"📰 [MODO BACKFILL] Recolhendo notícias dos últimos {BACKFILL_DAYS} dias ({desde.date()} → {hoje.date()})...")
    else:
        desde = hoje - timedelta(hours=2)  # Margem para cron de 30 min
        logging.info(f"📰 [MODO CRON] Recolhendo notícias das últimas 2h ({desde.isoformat()[:16]} UTC)...")

    from_str = desde.strftime("%Y-%m-%d")
    to_str = hoje.strftime("%Y-%m-%d")

    stats = {"inserted": 0, "skipped": 0, "errors": 0}

    # ── 1. Notícias por ticker ativo (RSS Yahoo Finance) ─────────────────────
    tickers = get_active_tickers()
    logging.info(f"📊 1/2 Notícias por ticker ({len(tickers)} ativos via RSS)...")

    for ticker in tickers:
        try:
            artigos = fetch_stock_news(ticker, from_str, to_str)
            # Filtro de data extra no modo cron (RSS devolve últimos N itens sem garantia de data)
            if not backfill:
                artigos = [a for a in artigos if _parse_pub_date(a.get("publishedDate", "")) >= desde]
            logging.info(f"  [{ticker}]: {len(artigos)} artigo(s) no período")
            for artigo in artigos:
                result = upsert_noticia(artigo, ticker_relacionado=ticker)
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
                result = upsert_noticia(artigo, ticker_relacionado="")
                stats["inserted" if result else "skipped"] += 1
        except Exception as e:
            logging.warning(f"⚠️ Erro ao processar RSS macro [{page}]: {e}")
            stats["errors"] += 1

    logging.info(
        f"🎉 Recolha concluída! "
        f"Inseridas: {stats['inserted']} | Já existentes: {stats['skipped']} | Erros: {stats['errors']}"
    )
    return stats
