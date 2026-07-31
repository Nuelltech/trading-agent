# tests/test_data_integrity.py
"""
Suíte de Testes Automatizados de Integridade de Dados (Data Quality Engine)
Executada pós-ingestão no CI/CD
"""

import sys
import unittest
from datetime import datetime, timedelta

sys.path.append('backend')
from app.services.data_validator import (
    validate_ohlc_record,
    validate_economic_calendar_records,
    promover_para_producao,
    PLAUSIBILITY_LIMITS,
    SPIKE_THRESHOLDS
)

class TestDataIntegrity(unittest.TestCase):


    def test_valid_ohlc_record(self):
        record = {
            "symbol": "TEST_FOREX",
            "value": 1.0850,
            "open_val": 1.0840,
            "high_val": 1.0870,
            "low_val": 1.0820,
            "volume": 1000
        }
        is_valid, sanitized, msg = validate_ohlc_record(record)
        self.assertTrue(is_valid)
        self.assertEqual(sanitized["high_val"], 1.0870)
        self.assertEqual(sanitized["low_val"], 1.0820)

    def test_yield_scale_quarantine_rejection(self):
        record = {
            "symbol": "^TNX",
            "value": 0.0463,  # Escala errada (dividida por 100) -> Deve ser rejeitada para quarentena sem mutação silenciosa
            "open_val": 0.0463,
            "high_val": 0.0463,
            "low_val": 0.0463,
            "volume": 0
        }
        is_valid, sanitized, msg = validate_ohlc_record(record)
        self.assertFalse(is_valid)
        self.assertIn("fora do intervalo de plausibilidade", msg)

    def test_out_of_bounds_rejection(self):
        record = {
            "symbol": "^VIX",
            "value": 150.0,  # Impossível (> 90.0)
            "open_val": 150.0,
            "high_val": 150.0,
            "low_val": 150.0,
            "volume": 0
        }
        is_valid, sanitized, msg = validate_ohlc_record(record)
        self.assertFalse(is_valid)
        self.assertIn("fora do intervalo de plausibilidade", msg)

    def test_ohlc_mathematical_sanitization(self):
        record = {
            "symbol": "GC=F",
            "value": 4127.4,
            "open_val": 4020.9,  # Open < Low (incompatível)
            "high_val": 4149.4,
            "low_val": 4053.9,
            "volume": 50000
        }
        is_valid, sanitized, msg = validate_ohlc_record(record)
        self.assertTrue(is_valid)
        self.assertLessEqual(sanitized["low_val"], sanitized["open_val"])  # Sanitizado!

    def test_unverified_mock_rejection(self):
        records = [
            {
                "event_name": "US Core CPI",
                "country": "EUA",
                "currency": "USD",
                "event_timestamp": "2026-08-12 12:30:00",
                "impact_level": "HIGH",
                "source_provider": "SYSTEM_FEED"  # Mock não assinalado
            }
        ]
        valid, rejected = validate_economic_calendar_records(records)
        self.assertEqual(len(valid), 0)
        self.assertEqual(len(rejected), 1)

    def test_central_bank_cluster_rejection(self):
        records = [
            {
                "event_name": "Fed Interest Rate Decision",
                "country": "EUA",
                "currency": "USD",
                "event_timestamp": "2026-09-16 19:00:00",
                "impact_level": "HIGH",
                "source_provider": "FED_OFFICIAL"
            },
            {
                "event_name": "ECB Interest Rate Decision",
                "country": "Zona Euro",
                "currency": "EUR",
                "event_timestamp": "2026-09-16 13:15:00",  # Mesma data que a Fed (Cluster proibido)
                "impact_level": "HIGH",
                "source_provider": "ECB_OFFICIAL"
            }
        ]
        valid, rejected = validate_economic_calendar_records(records)
        self.assertEqual(len(valid), 1)
        self.assertEqual(len(rejected), 1)

    def test_promocao_atualiza_producao_ao_longo_do_dia(self):
        """
        Reproduz o incidente do ^GSPC em 30/07/2026: a produção deve
        acompanhar atualizações da staging ao longo do dia, não ficar
        presa ao primeiro valor.
        """
        res1 = promover_para_producao("^GSPC", "2026-07-30", 7390.45, 7417.36, 7370.98, 7375.80)
        if res1.get("status") == "error" and ("Can't connect" in str(res1.get("message")) or "timed out" in str(res1.get("message"))):
            self.skipTest("MySQL inacessível durante a execução do teste unitário em CI/CD.")
            return

        self.assertIn(res1.get("status"), ["inserted", "updated"])

        res2 = promover_para_producao("^GSPC", "2026-07-30", 7390.45, 7448.75, 7370.98, 7437.63)
        self.assertEqual(res2.get("status"), "updated")
        self.assertAlmostEqual(res2["high_val"], 7448.75, places=2)
        self.assertAlmostEqual(res2["value"], 7437.63, places=2)   # close atualizado, não preso ao primeiro valor
        self.assertAlmostEqual(res2["open_val"], 7390.45, places=2) # open nunca muda


if __name__ == "__main__":
    unittest.main()

