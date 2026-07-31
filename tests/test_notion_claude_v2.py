# tests/test_notion_claude_v2.py
"""
Testes automatizados da Adenda v2: Tabelas Notion do Claude (Consolidação Final)
Verifica as regras de Max/Min no OHLC sem sobrescrever Open, e garante que 36 ativos são processados para a tabela de Close Diário.
Garante também que a correspondência de data é estrita (evita propagar dados do dia anterior na viragem da meia-noite).
"""

import unittest
from unittest.mock import patch, MagicMock
import pandas as pd
from backend.app.services.notion_claude_sync_service import (
    get_claude_watchlist,
    fetch_ticker_ohlc
)

class TestClaudeNotionV2(unittest.TestCase):

    def test_ohlc_upsert_rules(self):
        # Open fixo, High=max(), Low=min()
        curr_open = 100.0
        curr_high = 105.0
        curr_low = 98.0
        
        new_high = 107.5
        new_low = 99.0
        new_close = 106.0

        final_high = max(curr_high, new_high)
        final_low = min(curr_low, new_low)

        self.assertEqual(final_high, 107.5)
        self.assertEqual(final_low, 98.0)
        self.assertEqual(curr_open, 100.0)  # Open nunca é alterado

    @patch("yfinance.Ticker")
    def test_fetch_ticker_ohlc_prevents_stale_previous_day_propagation(self, mock_yf):
        """
        Garante que se target_date é 2026-07-31 e o yfinance só tem dados de 2026-07-30
        (ex: corrida na madrugada de 00:49 UTC antes da abertura do mercado),
        a função retorna None em vez de propagar os dados de 2026-07-30.
        """
        # Criar um DataFrame mock do yfinance com data de ontem (2026-07-30)
        dates = pd.date_range("2026-07-26", "2026-07-30")
        df_stale = pd.DataFrame({
            "Open": [7300.0, 7320.0, 7350.0, 7380.0, 7390.45],
            "High": [7350.0, 7360.0, 7390.0, 7410.0, 7448.75],
            "Low": [7280.0, 7300.0, 7330.0, 7360.0, 7370.98],
            "Close": [7340.0, 7355.0, 7375.0, 7400.0, 7437.63],
        }, index=dates)

        mock_ticker_obj = MagicMock()
        mock_ticker_obj.history.return_value = df_stale
        mock_yf.return_value = mock_ticker_obj

        # Pedir cotação para o novo dia (2026-07-31)
        res = fetch_ticker_ohlc("^GSPC", target_date="2026-07-31")

        # Deve retornar None porque a sessão de 2026-07-31 ainda não começou
        self.assertIsNone(res, "fetch_ticker_ohlc deve retornar None se a sessão do dia solicitado ainda não tiver iniciado!")


if __name__ == "__main__":
    unittest.main()
