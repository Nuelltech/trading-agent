"""
Testes unitários para news_collector.py (Camada 1 — Feed de Notícias)

Cobrem:
  - Categorização determinística (4 categorias: Earnings, Geopolítico, Empresa Específica, Macro Geral)
  - Filtro de exclusão de ruído (finanças pessoais: mortgage rates, HELOC, etc.)
  - Tagging determinístico de tickers por correspondência de texto no título
  - Extração mecânica de meta-descrição (HTML parsing com falha tolerante)
  - Deduplicação por URL e hash de fallback
  - Filtro anti-mock (fontes não identificadas)
  - Guardrails do pipeline sem NOTION_TOKEN
"""

import sys
import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta

sys.path.append('backend')


class TestFiltroExclusaoRuido(unittest.TestCase):
    """Testa rejeição de artigos de finanças pessoais (Problema 1)."""

    def test_rejeita_mortgage_rates(self):
        from app.services.news_collector import eh_conteudo_irrelevante
        self.assertTrue(eh_conteudo_irrelevante("Mortgage and refinance interest rates today"))

    def test_rejeita_cd_rates(self):
        from app.services.news_collector import eh_conteudo_irrelevante
        self.assertTrue(eh_conteudo_irrelevante("Best CD rates today: Earn up to 5.25% APY"))

    def test_rejeita_heloc(self):
        from app.services.news_collector import eh_conteudo_irrelevante
        self.assertTrue(eh_conteudo_irrelevante("How HELOC interest rates are calculated"))

    def test_rejeita_savings_account(self):
        from app.services.news_collector import eh_conteudo_irrelevante
        self.assertTrue(eh_conteudo_irrelevante("Best high-yield savings account rates for August"))

    def test_aceita_noticia_de_mercado_real(self):
        from app.services.news_collector import eh_conteudo_irrelevante
        self.assertFalse(eh_conteudo_irrelevante("Stock Market Today: Nasdaq Advances Despite Spiking Yields"))

    def test_aceita_noticia_de_empresa(self):
        from app.services.news_collector import eh_conteudo_irrelevante
        self.assertFalse(eh_conteudo_irrelevante("Apple reports Q3 revenue growth beating analyst estimates"))


class TestDetecaoTickers(unittest.TestCase):
    """Testa tagging de tickers por correspondência de texto no título (Problema 3)."""

    def setUp(self):
        self.watchlist_map = {
            "^NDX": "Nasdaq",
            "COIN": "Coinbase",
            "AAPL": "Apple",
            "^GSPC": "S&P 500",
            "^VIX": "VIX",
            "^SOX": "Semiconductor",
            "GC=F": "Gold",
        }

    def test_deteta_nasdaq_e_coinbase_no_titulo(self):
        from app.services.news_collector import detetar_tickers_mencionados
        titulo = "Stock Market Today: Nasdaq Advances Despite Spiking Yields; Coinbase Tumbles"
        mencionados = detetar_tickers_mencionados(titulo, self.watchlist_map)
        self.assertIn("^NDX", mencionados)
        self.assertIn("COIN", mencionados)

    def test_deteta_apple(self):
        from app.services.news_collector import detetar_tickers_mencionados
        titulo = "Apple stock rises after strong iPhone sales report"
        mencionados = detetar_tickers_mencionados(titulo, self.watchlist_map)
        self.assertEqual(mencionados, ["AAPL"])

    def test_deteta_sox_em_chip_stocks_com_resumo(self):
        from app.services.news_collector import detetar_tickers_mencionados
        texto = "Chip Stocks Are on Pace for Worst Month. The Philadelphia Semiconductor Index (SOX) dropped 4% today."
        mencionados = detetar_tickers_mencionados(texto, self.watchlist_map)
        self.assertIn("^SOX", mencionados)
        self.assertNotIn("^GSPC", mencionados)

    def test_deteta_gold_sem_coinbase(self):
        from app.services.news_collector import detetar_tickers_mencionados
        texto = "Tether adds 14 tons of gold to its reserves in Q3"
        mencionados = detetar_tickers_mencionados(texto, self.watchlist_map)
        self.assertIn("GC=F", mencionados)
        self.assertNotIn("COIN", mencionados)

    def test_deteta_coinbase_literal(self):
        from app.services.news_collector import detetar_tickers_mencionados
        texto = "Coinbase shares tumble 8% after SEC update"
        mencionados = detetar_tickers_mencionados(texto, self.watchlist_map)
        self.assertIn("COIN", mencionados)

    def test_titulo_sem_tickers_devolve_lista_vazia(self):
        from app.services.news_collector import detetar_tickers_mencionados
        texto = "Federal Reserve keeps interest rates unchanged at 5.25%"
        mencionados = detetar_tickers_mencionados(texto, self.watchlist_map)
        self.assertEqual(mencionados, [])


class TestCategorizacaoNoticia(unittest.TestCase):
    """Testa categorização determinística por regra (Problema 2)."""

    def setUp(self):
        self.patcher = patch('app.services.news_collector._tem_earnings_proximos')
        self.mock_earnings = self.patcher.start()
        self.mock_earnings.return_value = False

    def tearDown(self):
        self.patcher.stop()

    def test_empresa_especifica_quando_tem_ticker(self):
        from app.services.news_collector import categorizar_noticia
        resultado = categorizar_noticia("^NDX, COIN", "Nasdaq Advances; Coinbase Tumbles")
        self.assertEqual(resultado, "Empresa Específica")

    def test_earnings_relacionado_por_palavra_chave(self):
        from app.services.news_collector import categorizar_noticia
        resultado = categorizar_noticia("AAPL", "Apple beats earnings estimates by 15%")
        self.assertEqual(resultado, "Earnings-Relacionado")

    def test_earnings_relacionado_por_calendario_mysql(self):
        from app.services.news_collector import categorizar_noticia
        self.mock_earnings.return_value = True
        resultado = categorizar_noticia("MSFT", "Microsoft cloud growth accelerates")
        self.assertEqual(resultado, "Earnings-Relacionado")

    def test_geopolitico_por_palavra_chave(self):
        from app.services.news_collector import categorizar_noticia
        resultado = categorizar_noticia("", "Russia launches military attack on Ukraine border")
        self.assertEqual(resultado, "Geopolítico")

    def test_geopolitico_sanctions(self):
        from app.services.news_collector import categorizar_noticia
        resultado = categorizar_noticia("", "US imposes new sanctions on Iran oil exports")
        self.assertEqual(resultado, "Geopolítico")

    def test_macro_geral_sem_ticker_nem_keywords_tematicas(self):
        from app.services.news_collector import categorizar_noticia
        resultado = categorizar_noticia("", "Fed signals potential rate cut in September")
        self.assertEqual(resultado, "Macro Geral")

    def test_categoria_nunca_vazia(self):
        from app.services.news_collector import categorizar_noticia
        for titulo in ["", "   ", "xyz", "any headline at all"]:
            resultado = categorizar_noticia("", titulo)
            self.assertIn(resultado, ["Empresa Específica", "Macro Geral", "Geopolítico", "Earnings-Relacionado"])


class TestExtracaoResumoMecanico(unittest.TestCase):
    """Testa extração mecânica de meta-descrição HTML."""

    @patch('requests.get')
    def test_extrai_meta_description(self, mock_get):
        from app.services.news_collector import extrair_resumo_mecanico
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = '<html><head><meta name="description" content="Meta summary content here"></head></html>'
        mock_get.return_value = mock_resp

        resumo = extrair_resumo_mecanico("https://finance.yahoo.com/news/123")
        self.assertEqual(resumo, "Meta summary content here")

    @patch('requests.get')
    def test_extrai_og_description_fallback(self, mock_get):
        from app.services.news_collector import extrair_resumo_mecanico
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = '<html><head><meta property="og:description" content="OG description fallback"></head></html>'
        mock_get.return_value = mock_resp

        resumo = extrair_resumo_mecanico("https://finance.yahoo.com/news/123")
        self.assertEqual(resumo, "OG description fallback")

    @patch('requests.get')
    def test_tolerante_a_falha_de_http(self, mock_get):
        from app.services.news_collector import extrair_resumo_mecanico
        mock_get.side_effect = Exception("Timeout")

        resumo = extrair_resumo_mecanico("https://finance.yahoo.com/news/123")
        self.assertIsNone(resumo)


class TestDeduplicacao(unittest.TestCase):
    """Testa geração de chave de deduplicação."""

    def test_url_hash_fallback_deterministico(self):
        from app.services.news_collector import _url_hash_fallback
        h1 = _url_hash_fallback("Título A", "Reuters", "2026-07-31 10:00:00")
        h2 = _url_hash_fallback("Título A", "Reuters", "2026-07-31 10:00:00")
        self.assertEqual(h1, h2)

    def test_url_hash_comeca_com_hash(self):
        from app.services.news_collector import _url_hash_fallback
        h = _url_hash_fallback("t", "f", "d")
        self.assertTrue(h.startswith("hash:"))


class TestFiltroAntiMock(unittest.TestCase):
    """Testa que artigos inválidos são rejeitados sem chamada à Notion API."""

    def setUp(self):
        self.mock_notion = patch('app.services.news_collector._notion_query_by_id_externo').start()
        self.mock_create = patch('app.services.news_collector._notion_create_news_page').start()
        self.addCleanup(patch.stopall)

    def test_rejeita_titulo_vazio(self):
        from app.services.news_collector import upsert_noticia
        resultado = upsert_noticia({"title": "", "site": "Reuters", "publishedDate": "2026-07-31 10:00:00"})
        self.assertFalse(resultado)
        self.mock_create.assert_not_called()

    def test_rejeita_financas_pessoais(self):
        from app.services.news_collector import upsert_noticia
        resultado = upsert_noticia({"title": "Best CD rates today", "site": "Yahoo Finance", "publishedDate": "2026-07-31 10:00:00"})
        self.assertFalse(resultado)
        self.mock_create.assert_not_called()

    def test_rejeita_fonte_unknown(self):
        from app.services.news_collector import upsert_noticia
        resultado = upsert_noticia({"title": "Test News", "site": "unknown", "publishedDate": "2026-07-31 10:00:00"})
        self.assertFalse(resultado)
        self.mock_create.assert_not_called()


class TestUpsertNoticia(unittest.TestCase):
    """Testa lógica de upsert com os novos campos e tagging."""

    def setUp(self):
        self.mock_query = patch('app.services.news_collector._notion_query_by_id_externo').start()
        self.mock_create = patch('app.services.news_collector._notion_create_news_page').start()
        self.mock_earnings = patch('app.services.news_collector._tem_earnings_proximos').start()
        self.mock_earnings.return_value = False
        self.addCleanup(patch.stopall)

    def _artigo_valido(self, **kwargs):
        base = {
            "title": "Fed signals rate cut in next meeting",
            "site": "Reuters",
            "publishedDate": "2026-07-31 10:00:00",
            "url": "https://reuters.com/article/123",
        }
        base.update(kwargs)
        return base

    def test_cria_noticia_com_tagging_e_categoria(self):
        from app.services.news_collector import upsert_noticia
        self.mock_query.return_value = False
        self.mock_create.return_value = True

        artigo = self._artigo_valido(title="Stock Market Today: Nasdaq Advances; Coinbase Tumbles")
        watchlist_map = {"^NDX": "Nasdaq", "COIN": "Coinbase"}
        resultado = upsert_noticia(artigo, watchlist_map=watchlist_map)
        self.assertTrue(resultado)

        props = self.mock_create.call_args[0][0]
        self.assertIn("^NDX", props["Ticker(s) Relacionado(s)"]["rich_text"][0]["text"]["content"])
        self.assertIn("COIN", props["Ticker(s) Relacionado(s)"]["rich_text"][0]["text"]["content"])
        self.assertEqual(props["Categoria"]["select"]["name"], "Empresa Específica")
        self.assertIn("Resumo (Meta-descrição)", props)


class TestRunNewsCollectionGuardrails(unittest.TestCase):
    """Testa que o pipeline falha silenciosamente sem credenciais (nunca insere mock)."""

    def test_aborta_sem_notion_token(self):
        import app.services.news_collector as nc
        with patch.object(nc, 'NOTION_TOKEN', ''):
            stats = nc.run_news_collection(backfill=False)
            self.assertEqual(stats["inserted"], 0)


if __name__ == "__main__":
    unittest.main()
