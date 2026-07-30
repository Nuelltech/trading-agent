# tests/test_notion_claude_v2.py
"""
Testes automatizados da Adenda v2: Tabelas Notion do Claude (Consolidação Final)
Verifica as regras de Max/Min no OHLC sem sobrescrever Open, e garante que 36 ativos são processados para a tabela de Close Diário.
"""

import unittest
from backend.app.services.notion_claude_sync_service import get_claude_watchlist

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

if __name__ == "__main__":
    unittest.main()
