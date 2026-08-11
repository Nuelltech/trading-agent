# tests/test_catalysts_cron.py
import unittest
from unittest.mock import patch, MagicMock
import json

from app.services.notion_page_reader_service import parse_blocks_to_markdown, _extract_text_from_rich_text
from app.services.catalysts_service import EVENTO_PARA_MAPA, validar_previsao_pos_evento


class TestNotionPageReaderService(unittest.TestCase):

    def test_extract_text_from_rich_text(self):
        rt = [
            {"plain_text": "Mecanismo 1: "},
            {"plain_text": "Pressão de inflação "}
        ]
        self.assertEqual(_extract_text_from_rich_text(rt), "Mecanismo 1: Pressão de inflação ")

    def test_parse_blocks_to_markdown(self):
        blocks = [
            {
                "type": "heading_1",
                "heading_1": {"rich_text": [{"plain_text": "Mapa de Transmissão CPI"}]}
            },
            {
                "type": "paragraph",
                "paragraph": {"rich_text": [{"plain_text": "Se CPI > Projetado, yields sobem."}]}
            },
            {
                "type": "bulleted_list_item",
                "bulleted_list_item": {"rich_text": [{"plain_text": "Mecanismo 4: Fed mantendo juros altos."}]}
            }
        ]
        md = parse_blocks_to_markdown(blocks)
        self.assertIn("# Mapa de Transmissão CPI", md)
        self.assertIn("Se CPI > Projetado, yields sobem.", md)
        self.assertIn("- Mecanismo 4: Fed mantendo juros altos.", md)


class TestCatalystsService(unittest.TestCase):

    def test_evento_para_mapa_strict_mapping(self):
        # Nomes exatos válidos
        self.assertEqual(EVENTO_PARA_MAPA.get("US Non-Farm Payrolls (NFP)"), "NFP")
        self.assertEqual(EVENTO_PARA_MAPA.get("Fed Interest Rate Decision"), "Fed")
        self.assertEqual(EVENTO_PARA_MAPA.get("US Core CPI (MoM)"), "CPI")
        self.assertEqual(EVENTO_PARA_MAPA.get("US CPI (YoY)"), "CPI")
        self.assertEqual(EVENTO_PARA_MAPA.get("US Retail Sales (MoM)"), "RetailSales")

        # Nomes inventados ou inexistentes NUNCA devem ter correspondência
        self.assertIsNone(EVENTO_PARA_MAPA.get("Non Farm Payrolls General"))
        self.assertIsNone(EVENTO_PARA_MAPA.get("US CPI Random Event"))
        self.assertIsNone(EVENTO_PARA_MAPA.get("Apple Earnings Report"))

    @patch("app.services.notion_calendar_sync_service._notion_find_existing_page", return_value="test-page-id")
    @patch("requests.patch")
    def test_validar_previsao_pos_evento(self, mock_patch, mock_find):
        event_acima = {
            "id": 1,
            "event_name": "US Core CPI (MoM)",
            "actual_val": 0.4,
            "forecast_val": 0.2,
            "previsao_condicional": "Se Real > Projetado (acima) → DXY sobe"
        }
        res = validar_previsao_pos_evento(event_acima)
        self.assertEqual(res, "Acertou")

        event_errou = {
            "id": 2,
            "event_name": "US Core CPI (MoM)",
            "actual_val": 0.1,
            "forecast_val": 0.2,
            "previsao_condicional": "Se Real > Projetado (acima) → DXY sobe"
        }
        res_errou = validar_previsao_pos_evento(event_errou)
        self.assertEqual(res_errou, "Errou")


if __name__ == "__main__":
    unittest.main()
