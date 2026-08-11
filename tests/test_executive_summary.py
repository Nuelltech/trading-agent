# tests/test_executive_summary.py
import unittest
from unittest.mock import patch, MagicMock
import json

from app.services.executive_summary_service import (
    task1_scan_candidates,
    task1b_open_positions,
    task2_classify_candidates,
    get_active_tickers_from_mysql
)


class TestExecutiveSummaryAgent(unittest.TestCase):

    @patch("app.services.executive_summary_service.engine.connect")
    def test_get_active_tickers_from_mysql(self, mock_connect):
        mock_conn = MagicMock()
        mock_connect.return_value.__enter__.return_value = mock_conn
        
        # Simula resposta da query SELECT ticker FROM indicators_catalog
        mock_rows = [("AAPL",), ("NVDA",), ("^GSPC",), ("EURUSD=X",)]
        mock_conn.execute.return_value.fetchall.return_value = mock_rows

        tickers = get_active_tickers_from_mysql()
        self.assertEqual(len(tickers), 4)
        self.assertIn("NVDA", tickers)
        self.assertIn("^GSPC", tickers)

    @patch("app.services.executive_summary_service.get_active_tickers_from_mysql")
    @patch("app.services.executive_summary_service.engine.connect")
    def test_task1_scan_candidates_cutoff_10(self, mock_connect, mock_get_tickers):
        # 12 tickers fictícios com variações > 2%
        mock_get_tickers.return_value = [f"TICKER_{i}" for i in range(12)]
        
        mock_conn = MagicMock()
        mock_connect.return_value.__enter__.return_value = mock_conn

        def mock_execute(sql, params):
            ticker = params["ticker"]
            idx = int(ticker.split("_")[1])
            # Gera variações entre 2.1% e 14.1%
            close_ontem = 100.0
            close_hoje = 100.0 + (idx + 2.5)
            row0 = MagicMock(close=close_hoje)
            row1 = MagicMock(close=close_ontem)
            mock_res = MagicMock()
            mock_res.fetchall.return_value = [row0, row1]
            return mock_res

        mock_conn.execute.side_effect = mock_execute

        candidates = task1_scan_candidates("2026-08-11")
        # Deve cortar nos 10 maiores por magnitude
        self.assertEqual(len(candidates), 10)
        # O maior deve vir primeiro
        self.assertGreater(candidates[0]["variacao"], candidates[-1]["variacao"])

    def test_task1b_open_positions_empty(self):
        # Quando o secret ou db id não está configurado, regressa lista vazia sem dar erro
        positions = task1b_open_positions("2026-08-11")
        self.assertIsInstance(positions, list)


if __name__ == "__main__":
    unittest.main()
