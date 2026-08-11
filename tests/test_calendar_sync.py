# tests/test_calendar_sync.py
"""
Testes unitários para notion_calendar_sync_service.py
Verifica: mapeamento de campos, lógica upsert, filtro anti-mock,
e que Impacto nos Nossos Ativos / Tipo=Evento de Produto nunca são escritos pelo cron.
"""

import sys
import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime

sys.path.append('backend')

from app.services.notion_calendar_sync_service import (
    _infer_event_type,
    _format_value,
    upsert_economic_event,
    upsert_earnings_event,
    IMPACT_MAP,
    MOMENT_MAP,
    BLOCKED_PROVIDERS,
)


class TestEventTypeInference(unittest.TestCase):
    """Testa a inferência do Tipo a partir do nome do evento."""

    def test_cpi_is_macro(self):
        self.assertEqual(_infer_event_type("US Core CPI (MoM)"), "Macro (CPI/PMI/Juros)")

    def test_pmi_is_macro(self):
        self.assertEqual(_infer_event_type("ISM Manufacturing PMI"), "Macro (CPI/PMI/Juros)")

    def test_rate_is_macro(self):
        self.assertEqual(_infer_event_type("Fed Interest Rate Decision"), "Macro (CPI/PMI/Juros)")

    def test_nfp_is_macro(self):
        self.assertEqual(_infer_event_type("US Non-Farm Payrolls (NFP)"), "Macro (CPI/PMI/Juros)")

    def test_geopolitical_event(self):
        self.assertEqual(_infer_event_type("Geopolitical Tensions EU"), "Geopolítico")

    def test_unknown_event_is_outro(self):
        self.assertEqual(_infer_event_type("Some random announcement"), "Outro")

    def test_never_generates_evento_de_produto(self):
        """O cron nunca deve gerar 'Evento de Produto' — tipo exclusivamente manual."""
        for name in ["Apple Keynote", "Nvidia AI Day", "Tesla Product Launch", "Samsung Unpacked"]:
            result = _infer_event_type(name)
            self.assertNotEqual(result, "Evento de Produto",
                                f"'{name}' gerou Tipo='Evento de Produto' — violação da regra!")


class TestFormatValue(unittest.TestCase):
    """Testa a formatação de valores numéricos com unidade."""

    def test_float_with_unit(self):
        self.assertEqual(_format_value(2.5, "%"), "2.50 %")

    def test_none_returns_none(self):
        self.assertIsNone(_format_value(None))

    def test_zero_with_unit(self):
        self.assertEqual(_format_value(0.0, "K"), "0.00 K")

    def test_no_unit(self):
        self.assertEqual(_format_value(3.75), "3.75")


class TestImpactMapping(unittest.TestCase):
    """Testa o mapeamento HIGH/MEDIUM/LOW → Alta/Média/Baixa."""

    def test_high_maps_to_alta(self):
        self.assertEqual(IMPACT_MAP["HIGH"], "Alta")

    def test_medium_maps_to_media(self):
        self.assertEqual(IMPACT_MAP["MEDIUM"], "Média")

    def test_low_maps_to_baixa(self):
        self.assertEqual(IMPACT_MAP["LOW"], "Baixa")


class TestMomentMapping(unittest.TestCase):
    """Testa o mapeamento time_of_day → Momento."""

    def test_before_market(self):
        self.assertEqual(MOMENT_MAP.get("BEFORE_MARKET"), "Before Market")
        self.assertEqual(MOMENT_MAP.get("BMO"), "Before Market")

    def test_after_market(self):
        self.assertEqual(MOMENT_MAP.get("AFTER_MARKET"), "After Market")
        self.assertEqual(MOMENT_MAP.get("AMC"), "After Market")

    def test_unknown_defaults_to_na(self):
        self.assertEqual(MOMENT_MAP.get("UNKNOWN", "N/A"), "N/A")


class TestAntiMockFilter(unittest.TestCase):
    """Garante que providers mock/não verificados estão na lista bloqueada."""

    def test_mock_data_fallback_blocked(self):
        self.assertIn("MOCK_DATA_FALLBACK", BLOCKED_PROVIDERS)

    def test_unverified_demo_blocked(self):
        self.assertIn("UNVERIFIED_DEMO", BLOCKED_PROVIDERS)

    def test_system_feed_blocked(self):
        self.assertIn("SYSTEM_FEED", BLOCKED_PROVIDERS)

    def test_official_providers_not_blocked(self):
        official = ["FED_OFFICIAL", "BLS_OFFICIAL", "ECB_OFFICIAL", "BOE_OFFICIAL",
                    "BOJ_OFFICIAL", "FMP_API", "SEC_EDGAR_OFFICIAL"]
        for provider in official:
            self.assertNotIn(provider, BLOCKED_PROVIDERS,
                             f"Provider oficial '{provider}' está incorretamente bloqueado!")


class TestUpsertEconomicEvent(unittest.TestCase):
    """Testa a lógica de upsert de eventos macro."""

    def _make_eco_row(self, **kwargs):
        base = {
            "id": 1,
            "event_name": "Fed Interest Rate Decision",
            "country": "EUA",
            "event_timestamp": "2026-07-29 19:00:00",
            "impact_level": "HIGH",
            "actual_val": 3.75,
            "forecast_val": 3.75,
            "unit": "%",
            "source_provider": "FED_OFFICIAL",
        }
        base.update(kwargs)
        return base

    @patch("app.services.notion_calendar_sync_service._notion_find_existing_page", return_value=None)
    @patch("app.services.notion_calendar_sync_service._notion_create_page", return_value=True)
    def test_creates_new_page_when_not_exists(self, mock_create, mock_find):
        result = upsert_economic_event(self._make_eco_row())
        self.assertEqual(result, "created")
        mock_create.assert_called_once()

        # Verifica que "Impacto nos Nossos Ativos" nunca foi incluído no payload
        created_props = mock_create.call_args[0][0]
        self.assertNotIn("Impacto nos Nossos Ativos", created_props,
                          "O cron escreveu 'Impacto nos Nossos Ativos' — violação da regra!")

    @patch("app.services.notion_calendar_sync_service._notion_find_existing_page", return_value="existing-page-id")
    @patch("app.services.notion_calendar_sync_service._notion_update_page", return_value=True)
    def test_updates_real_and_date_fields_when_exists(self, mock_update, mock_find):
        result = upsert_economic_event(self._make_eco_row(actual_val=3.75, previous_val=3.5))
        self.assertEqual(result, "updated")

        # "Real", "Data" e "Anterior" devem ser atualizados no PATCH
        updated_props = mock_update.call_args[0][1]
        self.assertIn("Real", updated_props)
        self.assertIn("Data", updated_props)
        self.assertIn("Anterior", updated_props)
        self.assertNotIn("Evento", updated_props)
        self.assertNotIn("Tipo", updated_props)
        self.assertNotIn("Impacto nos Nossos Ativos", updated_props)

    @patch("app.services.notion_calendar_sync_service._notion_find_existing_page", return_value=None)
    @patch("app.services.notion_calendar_sync_service._notion_create_page", return_value=True)
    def test_creates_new_page_with_anterior_field(self, mock_create, mock_find):
        result = upsert_economic_event(self._make_eco_row(previous_val=3.5))
        self.assertEqual(result, "created")
        created_props = mock_create.call_args[0][0]
        self.assertIn("Anterior", created_props)
        self.assertEqual(created_props["Anterior"]["rich_text"][0]["text"]["content"], "3.50 %")


class TestUpsertEarningsEvent(unittest.TestCase):
    """Testa a lógica de upsert de earnings corporativos."""

    def _make_earn_row(self, **kwargs):
        base = {
            "id": 10,
            "symbol": "O",
            "company_name": "Realty Income Corporation",
            "event_date": "2026-08-04",
            "time_of_day": "AFTER_MARKET",
            "eps_estimate": 1.05,
            "eps_actual": None,
            "revenue_estimate": 1250000000.00,
            "revenue_actual": None,
            "fiscal_period": "Q2 2026",
            "source_provider": "SEC_EDGAR_OFFICIAL",
        }
        base.update(kwargs)
        return base

    @patch("app.services.notion_calendar_sync_service._notion_find_existing_page", return_value=None)
    @patch("app.services.notion_calendar_sync_service._notion_create_page", return_value=True)
    def test_creates_earnings_page(self, mock_create, mock_find):
        result = upsert_earnings_event(self._make_earn_row())
        self.assertEqual(result, "created")
        mock_create.assert_called_once()

        created_props = mock_create.call_args[0][0]

        # Tipo deve ser sempre "Resultados Empresa"
        self.assertEqual(created_props["Tipo"]["select"]["name"], "Resultados Empresa")

        # Impacto nos Nossos Ativos nunca deve ser escrito
        self.assertNotIn("Impacto nos Nossos Ativos", created_props)

        # Titulo deve incluir nome da empresa e período
        title_content = created_props["Evento"]["title"][0]["text"]["content"]
        self.assertIn("Realty Income", title_content)
        self.assertIn("Q2 2026", title_content)

    @patch("app.services.notion_calendar_sync_service._notion_find_existing_page", return_value="page-99")
    @patch("app.services.notion_calendar_sync_service._notion_update_page", return_value=True)
    def test_updates_only_eps_actual_and_revenue_actual(self, mock_update, mock_find):
        result = upsert_earnings_event(self._make_earn_row(eps_actual=1.10, revenue_actual=1300000000.0))
        self.assertEqual(result, "updated")

        updated_props = mock_update.call_args[0][1]
        self.assertIn("EPS Real", updated_props)
        self.assertIn("Receita Real", updated_props)
        self.assertNotIn("EPS Estimado", updated_props)
        self.assertNotIn("Evento", updated_props)
        self.assertNotIn("Impacto nos Nossos Ativos", updated_props)

    @patch("app.services.notion_calendar_sync_service._notion_find_existing_page", return_value="page-99")
    @patch("app.services.notion_calendar_sync_service._notion_update_page")
    def test_skips_when_no_actuals(self, mock_update, mock_find):
        result = upsert_earnings_event(self._make_earn_row(eps_actual=None, revenue_actual=None))
        self.assertEqual(result, "skipped")
        mock_update.assert_not_called()


if __name__ == "__main__":
    unittest.main()
