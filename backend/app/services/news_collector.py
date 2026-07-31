"""
Módulo: news_collector.py
Data: 31 Julho 2026

Camada 1 — Recolha determinística de headlines financeiras via Financial Modeling Prep (FMP).
Nenhuma interpretação de causalidade ou análise de sentimento calculada por nós.
Sentimento copiado diretamente da FMP se disponível no payload, nunca calculado internamente.

Bases de dados Notion:
    Feed de Notícias: e1c8d3ab-a151-499f-8931-4537f29933ec

Fluxo:
    1. Ler tickers ativos da Configuração de Vigilância (Ativo=True)
    2. Recolher /v3/stock_news por ticker
    3. Recolher /v4/general_news para notícias macro sem ticker
    4. Categorizar por regra determinística (nunca por LLM)
    5. Deduplicar por URL (ou hash como fallback)
    6. Upsert no Notion — nunca sobrescrever notícias existentes
"""

import os
import hashlib
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Any

import requests
from sqlalchemy import text

from app.database import engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# ─── Constantes ─────────────────────────────────────────────────────────────

FMP_API_KEY = os.getenv("FMP_API_KEY", "")
FMP_BASE_URL = "https://financialmodelingprep.com"
# Endpoints v3/v4 foram descontinuados — usar /stable/ (migração FMP pós-Agosto 2025)
# Endpoint único para toda a news: stock-latest devolve symbol em cada item
FMP_STOCK_NEWS_ENDPOINT = "/stable/news/stock-latest"
FMP_NEWS_LIMIT = 250  # Máximo por chamada confirmado pela FMP

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
FMP_NEWS_LIMIT = 250  # Máximo por chamada confirmado pela FMP (era 50, corrigido)

PALAVRAS_GEOPOLITICAS = [
    "war", "conflict", "sanctions", "military", "attack", "invasion",
    "geopolitical", "nuclear", "nato", "missile", "troops", "ceasefire",
    "embargo", "terrorism", "coup", "assassination"
]

PALAVRAS_EARNINGS = [
    "earnings", "results", "revenue", "profit", "eps", "guidance",
    "quarterly", "beat", "miss", "outlook", "forecast"
]


# ─── Helpers — FMP ────────────────────────────────────────────────────────────

def _fmp_get(endpoint: str, params: Dict[str, Any]) -> Optional[List[Dict]]:
    """Chama o FMP API com o padrão correto (apikey como query param). Retorna None em caso de falha."""
    if not FMP_API_KEY:
        logging.error("❌ FMP_API_KEY não configurada. Recolha de notícias abortada.")
        return None

    params["apikey"] = FMP_API_KEY
    url = f"{FMP_BASE_URL}{endpoint}"
    try:
        res = requests.get(url, params=params, timeout=15)
        if res.status_code == 200:
            data = res.json()
            if isinstance(data, list):
                return data
            if isinstance(data, dict) and "content" in data:
                return data["content"]
            return []
        elif res.status_code == 429:
            logging.warning(f"⚠️ FMP Rate Limit atingido para {endpoint}. Aguardando 5s...")
            time.sleep(5)
            return None
        else:
            logging.warning(f"⚠️ FMP {endpoint} devolveu HTTP {res.status_code}: {res.text[:200]}")
            return None
    except Exception as e:
        logging.error(f"❌ Erro ao chamar FMP {endpoint}: {e}")
        return None


def fetch_stock_news_batch(from_date: str, to_date: str, page: int = 0) -> List[Dict]:
    """
    Recolhe notícias de todos os tickers em UMA chamada via /stable/news/stock-latest.
    O campo 'symbol' em cada item indica o ticker. Máx 250 por chamada.
    Muito mais eficiente que 1 chamada por ticker (poupa quota diária do plano Free).
    """
    logging.info(f"  📰 Recolhendo batch de notícias (página {page}, from={from_date}, to={to_date})...")
    data = _fmp_get(FMP_STOCK_NEWS_ENDPOINT, {
        "from": from_date,
        "to": to_date,
        "limit": FMP_NEWS_LIMIT,
        "page": page,
    })
    time.sleep(0.5)  # Respeitar rate limit FMP free tier (250/dia)
    return data or []


# Manter compatível com testes — alias para fetch_stock_news
def fetch_stock_news(ticker: str, from_date: str, to_date: str) -> List[Dict]:
    """Alias de compatibilidade. Em produção usar fetch_stock_news_batch."""
    return [a for a in fetch_stock_news_batch(from_date, to_date) if a.get("symbol", "") == ticker]


def fetch_general_news(page: int = 0) -> List[Dict]:
    """Alias de compatibilidade. Em produção o pipeline usa fetch_stock_news_batch."""
    return fetch_stock_news_batch(
        from_date=(datetime.now(timezone.utc) - timedelta(days=BACKFILL_DAYS)).strftime("%Y-%m-%d"),
        to_date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        page=page,
    )


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
    Pipeline completo de recolha de notícias — otimizado para o plano Free da FMP (250/dia).

    Estratégia: 1-2 chamadas batch a /stable/news/stock-latest com from/to/limit=250.
    O campo 'symbol' em cada item determina o ticker relacionado.
    Notícias de tickers ativos → 'Empresa Específica' / 'Earnings-Relacionado'.
    Notícias de outros tickers → 'Macro Geral' (contexto de mercado).

    backfill=True : últimos 7 dias, até 3 páginas (3 × 250 = 750 artigos máx)
    backfill=False: últimas 2h (cron de 30 min com margem), 1 página
    """
    if not FMP_API_KEY:
        logging.error("❌ FMP_API_KEY não definida. Abortando recolha de notícias.")
        return {"inserted": 0, "skipped": 0, "errors": 0}
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

    # Ler watchlist uma vez — usado para categorização
    tickers_ativos = set(get_active_tickers())
    # Normalizar: remover prefixos/sufixos que a FMP não usa no campo symbol
    tickers_fmp = {t.replace("^", "").replace("=X", "").replace("=F", "").replace("-Y.NYB", "") for t in tickers_ativos}
    # Mapa reverso FMP symbol → ticker original (para o campo Notion)
    ticker_map = {}
    for t in tickers_ativos:
        fmp = t.replace("^", "").replace("=X", "").replace("=F", "").replace("-Y.NYB", "")
        ticker_map[fmp] = t

    stats = {"inserted": 0, "skipped": 0, "errors": 0}
    pages_to_fetch = 3 if backfill else 1

    logging.info(f"📰 Batch fetch: {pages_to_fetch} páginas × 250 artigos = {pages_to_fetch * 250} máx. Watchlist: {len(tickers_ativos)} tickers.")

    for page in range(pages_to_fetch):
        artigos = fetch_stock_news_batch(from_str, to_str, page=page)
        if not artigos:
            logging.info(f"  Página {page}: sem artigos — parar.")
            break

        # Filtrar por data no modo cron (garantia extra mesmo que FMP filtre por from/to)
        if not backfill:
            artigos_filtrados = [a for a in artigos if _parse_pub_date(a.get("publishedDate", "")) >= desde]
            if not artigos_filtrados:
                logging.info(f"  Página {page}: sem artigos nas últimas 2h — parar.")
                break
            artigos = artigos_filtrados

        logging.info(f"  Página {page}: {len(artigos)} artigos a processar...")

        for artigo in artigos:
            try:
                # Determinar ticker relacionado pelo campo 'symbol' da FMP
                symbol_fmp = (artigo.get("symbol") or "").strip()
                ticker_relacionado = ticker_map.get(symbol_fmp, symbol_fmp)

                result = upsert_noticia(artigo, ticker_relacionado=ticker_relacionado)
                if result:
                    stats["inserted"] += 1
                else:
                    stats["skipped"] += 1
            except Exception as e:
                logging.warning(f"⚠️ Erro ao processar artigo [{artigo.get('symbol', '?')}]: {e}")
                stats["errors"] += 1

    logging.info(
        f"🎉 Recolha concluída! "
        f"Inseridas: {stats['inserted']} | Já existentes: {stats['skipped']} | Erros: {stats['errors']}"
    )
    return stats


def _parse_pub_date(date_str: str) -> datetime:
    """Parse da data de publicação da FMP para datetime UTC."""
    try:
        return datetime.strptime(date_str[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except Exception:
        return datetime.min.replace(tzinfo=timezone.utc)
