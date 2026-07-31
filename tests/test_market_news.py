"""
Testes unitários para news_collector.py (Camada 1 — Feed de Notícias)

Cobrem:
  - Categorização determinística (secção 4 da spec)
  - Deduplicação por URL e hash de fallback
  - Filtro anti-mock (fontes não identificadas)
  - Parsing de datas da FMP
  - run_news_collection falha silenciosa sem FMP_API_KEY
"""

import sys
import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta

sys.path.append('backend')


class TestCategorizacaoNoticia(unittest.TestCase):
    """Testa categorização determinística — nunca por LLM."""

    def setUp(self):
        # Patch _tem_earnings_proximos para controlar o resultado sem MySQL
        self.patcher = patch('app.services.news_collector._tem_earnings_proximos')
        self.mock_earnings = self.patcher.start()
        self.mock_earnings.return_value = False

    def tearDown(self):
        self.patcher.stop()

    def test_empresa_especifica_ticker_sem_earnings(self):
        from app.services.news_collector import categorizar_noticia
        resultado = categorizar_noticia("AAPL", "Apple announces new iPhone model")
        self.assertEqual(resultado, "Empresa Específica")

    def test_earnings_relacionado_por_palavras_chave(self):
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

    def test_macro_geral_sem_ticker(self):
        from app.services.news_collector import categorizar_noticia
        resultado = categorizar_noticia("", "Fed signals potential rate cut in September")
        self.assertEqual(resultado, "Macro Geral")

    def test_macro_geral_banco_central(self):
        from app.services.news_collector import categorizar_noticia
        resultado = categorizar_noticia("", "ECB keeps rates unchanged amid inflation concerns")
        self.assertEqual(resultado, "Macro Geral")

    def test_categoria_nunca_vazia(self):
        """Categorização deve sempre devolver uma string não-vazia."""
        from app.services.news_collector import categorizar_noticia
        for titulo in ["", "   ", "xyz", "any headline at all"]:
            resultado = categorizar_noticia("", titulo)
            self.assertIn(resultado, ["Empresa Específica", "Macro Geral", "Geopolítico", "Earnings-Relacionado"])


class TestDeduplicacao(unittest.TestCase):
    """Testa geração de chave de deduplicação."""

    def test_url_hash_fallback_deterministico(self):
        from app.services.news_collector import _url_hash_fallback
        h1 = _url_hash_fallback("Título A", "Reuters", "2026-07-31 10:00:00")
        h2 = _url_hash_fallback("Título A", "Reuters", "2026-07-31 10:00:00")
        self.assertEqual(h1, h2)

    def test_url_hash_fallback_diferente_para_diferentes_inputs(self):
        from app.services.news_collector import _url_hash_fallback
        h1 = _url_hash_fallback("Título A", "Reuters", "2026-07-31 10:00:00")
        h2 = _url_hash_fallback("Título B", "Reuters", "2026-07-31 10:00:00")
        self.assertNotEqual(h1, h2)

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

    def test_rejeita_fonte_unknown(self):
        from app.services.news_collector import upsert_noticia
        resultado = upsert_noticia({"title": "Test News", "site": "unknown", "publishedDate": "2026-07-31 10:00:00"})
        self.assertFalse(resultado)
        self.mock_create.assert_not_called()

    def test_rejeita_fonte_vazia(self):
        from app.services.news_collector import upsert_noticia
        resultado = upsert_noticia({"title": "Test News", "site": "", "publishedDate": "2026-07-31 10:00:00"})
        self.assertFalse(resultado)
        self.mock_create.assert_not_called()

    def test_rejeita_fonte_mock(self):
        from app.services.news_collector import upsert_noticia
        resultado = upsert_noticia({"title": "Test News", "site": "mock", "publishedDate": "2026-07-31 10:00:00"})
        self.assertFalse(resultado)
        self.mock_create.assert_not_called()


class TestUpsertNoticia(unittest.TestCase):
    """Testa lógica de upsert — não duplicar, criar quando necessário."""

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

    def test_skip_se_ja_existe(self):
        from app.services.news_collector import upsert_noticia
        self.mock_query.return_value = True
        resultado = upsert_noticia(self._artigo_valido())
        self.assertFalse(resultado)
        self.mock_create.assert_not_called()

    def test_cria_se_nao_existe(self):
        from app.services.news_collector import upsert_noticia
        self.mock_query.return_value = False
        self.mock_create.return_value = True
        resultado = upsert_noticia(self._artigo_valido())
        self.assertTrue(resultado)
        self.mock_create.assert_called_once()

    def test_usa_url_como_id_externo(self):
        from app.services.news_collector import upsert_noticia
        self.mock_query.return_value = False
        self.mock_create.return_value = True
        upsert_noticia(self._artigo_valido(url="https://reuters.com/unique-article"))
        call_args = self.mock_query.call_args[0][0]
        self.assertEqual(call_args, "https://reuters.com/unique-article")

    def test_usa_hash_quando_sem_url(self):
        from app.services.news_collector import upsert_noticia
        self.mock_query.return_value = False
        self.mock_create.return_value = True
        artigo = self._artigo_valido()
        del artigo["url"]
        upsert_noticia(artigo)
        call_args = self.mock_query.call_args[0][0]
        self.assertTrue(call_args.startswith("hash:"))

    def test_sentimento_positivo_copiado(self):
        """Sentimento é copiado da FMP se disponível — nunca calculado."""
        from app.services.news_collector import upsert_noticia
        self.mock_query.return_value = False
        self.mock_create.return_value = True
        upsert_noticia(self._artigo_valido(sentiment="Positive"))
        # _notion_create_news_page receives props directly (payload built inside the function)
        props = self.mock_create.call_args[0][0]
        self.assertEqual(props["Sentimento (Fonte)"]["select"]["name"], "Positivo")

    def test_sem_campo_sentimento_se_fmp_nao_devolve(self):
        """Se FMP não devolver sentimento, o campo não é enviado ao Notion."""
        from app.services.news_collector import upsert_noticia
        self.mock_query.return_value = False
        self.mock_create.return_value = True
        upsert_noticia(self._artigo_valido())  # Sem 'sentiment'
        props = self.mock_create.call_args[0][0]
        self.assertNotIn("Sentimento (Fonte)", props)

    def test_fonte_de_registo_sempre_automatico(self):
        from app.services.news_collector import upsert_noticia
        self.mock_query.return_value = False
        self.mock_create.return_value = True
        upsert_noticia(self._artigo_valido())
        props = self.mock_create.call_args[0][0]
        self.assertEqual(props["Fonte de Registo"]["select"]["name"], "Automático (Cron)")


class TestParsePubDate(unittest.TestCase):
    """Testa parsing robusto de datas da FMP."""

    def test_formato_standard_fmp(self):
        from app.services.news_collector import _parse_pub_date
        dt = _parse_pub_date("2026-07-31 10:30:00")
        self.assertEqual(dt.year, 2026)
        self.assertEqual(dt.month, 7)
        self.assertEqual(dt.day, 31)

    def test_data_invalida_devolve_datetime_min(self):
        from app.services.news_collector import _parse_pub_date
        dt = _parse_pub_date("invalid-date")
        self.assertEqual(dt, datetime.min.replace(tzinfo=timezone.utc))

    def test_data_vazia_devolve_datetime_min(self):
        from app.services.news_collector import _parse_pub_date
        dt = _parse_pub_date("")
        self.assertEqual(dt, datetime.min.replace(tzinfo=timezone.utc))


class TestRunNewsCollectionGuardrails(unittest.TestCase):
    """Testa que o pipeline falha silenciosamente sem credenciais (nunca insere mock)."""

    def test_aborta_sem_fmp_api_key(self):
        with patch.dict('os.environ', {'FMP_API_KEY': ''}, clear=False):
            # Reimportar com a variável de ambiente zerada
            import importlib
            import app.services.news_collector as nc
            importlib.reload(nc)
            # Patch após reload
            with patch.object(nc, 'FMP_API_KEY', ''):
                stats = nc.run_news_collection(backfill=False)
                self.assertEqual(stats["inserted"], 0)

    def test_aborta_sem_notion_token(self):
        import app.services.news_collector as nc
        with patch.object(nc, 'FMP_API_KEY', 'test-key'), \
             patch.object(nc, 'NOTION_TOKEN', ''):
            stats = nc.run_news_collection(backfill=False)
            self.assertEqual(stats["inserted"], 0)


if __name__ == "__main__":
    unittest.main()
