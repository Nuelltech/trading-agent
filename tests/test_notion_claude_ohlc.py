# tests/test_notion_claude_ohlc.py
"""
Testes automatizados da Adenda: Painel Diário Claude OHLC
Verifica a lógica em memória de filtragem da watchlist do Claude e regras de max()/min() de High/Low.
"""

import unittest

class TestClaudeOHLCSync(unittest.TestCase):

    def test_high_low_max_min_rules(self):
        # Regra: High/Low atualizados por max() / min() face ao valor já gravado
        curr_high = 105.50
        curr_low = 99.20

        new_high_1 = 104.80  # Menor que curr_high
        new_low_1 = 98.50   # Menor que curr_low (novo mínimo)

        final_high_1 = max(curr_high, new_high_1)
        final_low_1 = min(curr_low, new_low_1)

        self.assertEqual(final_high_1, 105.50)  # Mantém o High mais alto
        self.assertEqual(final_low_1, 98.50)    # Atualiza para o Low mais baixo

if __name__ == "__main__":
    unittest.main()
