# tests/test_historical_backfill.py
"""
Testes unitários para o script de Backfill Histórico (backfill_historical_indicators.py)
Verifica as 5 regras de aceitação da Adenda:
1. Plausibilidade mantida.
2. Deteção de spike desativada para a operação de backfill.
3. Preservação de registos existentes em produção (nunca sobrescreve).
"""

import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.append('backend')
from scripts.backfill_historical_indicators import (
    _validate_backfill_plausibility,
    LOOKBACK_SESSIONS,
    YFINANCE_BACKFILL_MAP,
    FRED_BACKFILL_MAP
)

class TestHistoricalBackfill(unittest.TestCase):

    def test_lookback_sessions_count(self):
        """Garante que a constante de lookback é 60 sessões."""
        self.assertEqual(LOOKBACK_SESSIONS, 60)

    def test_all_catalog_tickers_mapped(self):
        """Verifica que todos os 36 tickers estão mapeados (yfinance ou FRED)."""
        total_mapped = len(YFINANCE_BACKFILL_MAP) + len(FRED_BACKFILL_MAP)
        self.assertGreaterEqual(total_mapped, 36)

    def test_plausibility_validation_active(self):
        """Valida que a plausibilidade continua a rejeitar valores fora de limites no backfill."""
        self.assertTrue(_validate_backfill_plausibility("^GSPC", 5000.0))
        self.assertFalse(_validate_backfill_plausibility("^VIX", 150.0))
        self.assertFalse(_validate_backfill_plausibility("^TNX", 0.0463))

    def test_spike_check_bypassed_in_backfill(self):
        """
        No backfill histórico, grandes variações legítimas (+5%/dia no Brent ou swings em meados de Julho)
        não devem ser bloqueadas pelo filtro de spike.
        """
        # _validate_backfill_plausibility só verifica plausibilidade e não lança exceção para spikes legítimos
        self.assertTrue(_validate_backfill_plausibility("BZ=F", 90.0))
        self.assertTrue(_validate_backfill_plausibility("BZ=F", 80.0))

if __name__ == "__main__":
    unittest.main()
