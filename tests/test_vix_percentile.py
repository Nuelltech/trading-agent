# tests/test_vix_percentile.py
"""
Suíte de Teste CI: Validação do Cálculo de VIX por Percentil (Adenda Consultor 1 - Ponto 6)
"""

import sys
import unittest

sys.path.append('backend')
from app.services.data_validator import classificar_vix_percentil, calcular_percentil


class TestVIXPercentile(unittest.TestCase):

    def test_cold_start_under_60_sessions(self):
        """Verifica se historico < 60 ativa o modo cold-start com os limiares fixos"""
        hist = [14.0] * 30
        label, note = classificar_vix_percentil(12.5, hist)
        self.assertEqual(label, "Baixa Vol")
        self.assertEqual(note, "Cold-Start (Percentil Indisponível)")

        label, note = classificar_vix_percentil(18.0, hist)
        self.assertEqual(label, "Transição")

        label, note = classificar_vix_percentil(25.0, hist)
        self.assertEqual(label, "Pânico")

    def test_percentile_calculation_normal(self):
        """Verifica se com >= 60 sessões o percentil é calculated corretamente"""
        hist = [float(i) for i in range(10, 110)]  # 10..109
        
        label, note = classificar_vix_percentil(20.0, hist)
        self.assertEqual(label, "Baixa Vol")
        self.assertTrue("Percentil" in note)

        label, note = classificar_vix_percentil(70.0, hist)
        self.assertEqual(label, "Transição")

        label, note = classificar_vix_percentil(100.0, hist)
        self.assertEqual(label, "Pânico")

    def test_calcular_percentil_helper(self):
        """Testa o cálculo matemático exato do percentil"""
        hist = [10.0, 20.0, 30.0, 40.0, 50.0]
        p = calcular_percentil(25.0, hist)
        self.assertEqual(p, 40.0)


if __name__ == "__main__":
    unittest.main()
