# tests/test_gemini_trader.py
"""
Testes automatizados da Camada 2: Gemini Trader ETL & Categorical Split
Verifica a lógica em memória de filtragem e separação entre Lista_Macro e Lista_Operavel.
"""

import unittest
from backend.app.services.gemini_trader_service import phase2_split_categorical

class TestGeminiTraderETL(unittest.TestCase):

    def test_phase2_split_categorical(self):
        sample_config = [
            {"ticker": "BZ=F", "nome": "Brent Crude", "categoria": "Operável"},
            {"ticker": "^VIX", "nome": "Volatility Index", "categoria": "Contexto Macro"},
            {"ticker": "GC=F", "nome": "Gold Futures", "categoria": "Operável"},
            {"ticker": "US10Y", "nome": "US Treasury 10Y", "categoria": "Contexto Macro"},
            {"ticker": "EURUSD=X", "nome": "EUR/USD", "categoria": "Operável"}
        ]

        lista_macro, lista_operavel = phase2_split_categorical(sample_config)

        # Validar tamanhos das listas
        self.assertEqual(len(lista_macro), 2)
        self.assertEqual(len(lista_operavel), 3)

        # Validar tickers nas listas
        macro_tickers = [item["ticker"] for item in lista_macro]
        operavel_tickers = [item["ticker"] for item in lista_operavel]

        self.assertIn("^VIX", macro_tickers)
        self.assertIn("US10Y", macro_tickers)
        self.assertIn("BZ=F", operavel_tickers)
        self.assertIn("GC=F", operavel_tickers)
        self.assertIn("EURUSD=X", operavel_tickers)

if __name__ == "__main__":
    unittest.main()
