# tests/test_liquidity_engine.py
"""
Suíte de Testes Automatizados para o Módulo Liquidity Engine & VPVR On-Demand
Especificação Técnica v1.0 - Secções 3 e 4
"""

import sys
import unittest
from datetime import datetime, timedelta
import pandas as pd
import numpy as np

sys.path.append('backend')
from app.services.liquidity_engine import (
    is_swing_high,
    is_swing_low,
    detect_swing_fractals,
    analyze_liquidity_sweeps
)
from app.services.vpvr_ondemand import calculate_vpvr

class TestLiquidityEngine(unittest.TestCase):

    def setUp(self):
        # Gerar DataFrame sintético de 70 sessões
        dates = [datetime(2026, 5, 1) + timedelta(days=i) for i in range(70)]
        np.random.seed(42)
        prices = 100.0 + np.cumsum(np.random.randn(70))
        
        self.df = pd.DataFrame({
            "timestamp": dates,
            "open": prices,
            "high": prices + 1.5,
            "low": prices - 1.5,
            "close": prices + 0.2,
            "volume": np.random.randint(1000, 50000, size=70)
        })

    def test_cold_start_safeguard(self):
        """Verifica se DataFrames com menos de 60 linhas acionam a salvaguarda ATR_60_INCOMPLETO"""
        short_df = self.df.iloc[:30].copy()
        res = analyze_liquidity_sweeps("BZ=F", short_df)
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["status"], "ATR_60_INCOMPLETO")

    def test_forex_vpvr_blocking(self):
        """Verifica se o cálculo de VPVR é estritamente bloqueado para ativos Forex"""
        vpvr_res = calculate_vpvr("EURUSD=X", self.df)
        self.assertIsNone(vpvr_res)

    def test_vpvr_real_volume_asset(self):
        """Verifica se o cálculo de VPVR funciona normalmente para ativos com volume real (ex: Brent BZ=F)"""
        vpvr_res = calculate_vpvr("BZ=F", self.df)
        self.assertIsNotNone(vpvr_res)
        self.assertEqual(vpvr_res["status"], "VPVR_CALCULATED")
        self.assertIn("poc_price", vpvr_res)

    def test_swing_fractal_detection(self):
        """Verifica se a função detect_swing_fractals identifica topos e fundos fractais"""
        highs, lows = detect_swing_fractals(self.df, n=3)
        self.assertIsInstance(highs, list)
        self.assertIsInstance(lows, list)

if __name__ == "__main__":
    unittest.main()
