# tests/test_gemini_pandas_enrichment.py
"""
Testes automatizados do Motor de Enriquecimento Analítico (Gemini Trader / Pandas)
Valida os cálculos de SMA20, SMA50, Z-Score 20D e ATR_14D.
"""

import unittest
import pandas as pd
import numpy as np
from backend.app.services.gemini_trader_service import compute_pandas_enrichment_metrics

class TestGeminiPandasEnrichment(unittest.TestCase):

    def test_compute_pandas_enrichment_metrics(self):
        # Criar 50 sessões sintéticas
        dates = pd.date_range("2026-01-01", periods=50, freq="D")
        closes = [100.0 + i * 0.5 for i in range(50)]
        highs = [c + 2.0 for c in closes]
        lows = [c - 2.0 for c in closes]
        opens = closes.copy()

        df = pd.DataFrame({
            "timestamp": dates,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": [10000] * 50
        })

        metrics = compute_pandas_enrichment_metrics(df)

        self.assertIn("variacao_5d", metrics)
        self.assertIn("sma20", metrics)
        self.assertIn("sma50", metrics)
        self.assertIn("z_score_20d", metrics)
        self.assertIn("atr_14d", metrics)
        self.assertIn("amplitude_vs_atr", metrics)

        self.assertGreater(metrics["sma20"], 0)
        self.assertGreater(metrics["sma50"], 0)
        self.assertGreater(metrics["atr_14d"], 0)

if __name__ == "__main__":
    unittest.main()
