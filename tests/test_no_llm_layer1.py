# tests/test_no_llm_layer1.py
"""
Suíte de Teste CI: Garantia Permanente de Zero LLM na Camada 1 (Motor Determinístico)
Especificação Técnica v2.0 - Secção 5.1 (Reparo 1)
"""

import glob
import unittest

FORBIDDEN_PACKAGES = [
    "openai", 
    "anthropic", 
    "google.generativeai", 
    "langchain",
    "ollama",
    "groq"
]

LAYER1_PATH_PATTERNS = [
    "backend/app/services/liquidity*.py",
    "backend/app/services/data_validator.py",
    "backend/app/services/notion_sync_service.py",
    "backend/app/services/vpvr_ondemand.py",
    "scripts/run_liquidity_analysis.py",
    "scripts/backtest_liquidity_calibration.py",
]

class TestNoLLMInLayer1(unittest.TestCase):

    def test_no_llm_dependencies_in_layer1(self):
        """Garante que nenhum ficheiro da Camada 1 possui dependências ou chamadas a APIs de LLM"""
        matched_files = 0
        for pattern in LAYER1_PATH_PATTERNS:
            for filepath in glob.glob(pattern):
                matched_files += 1
                with open(filepath, encoding="utf-8") as f:
                    content = f.read().lower()
                for pkg in FORBIDDEN_PACKAGES:
                    self.assertNotIn(
                        pkg.lower(), 
                        content, 
                        f"❌ ERRO GRAVE: Dependência de LLM '{pkg}' encontrada em {filepath}"
                    )
        self.assertGreater(matched_files, 0, "Nenhum ficheiro da Camada 1 foi encontrado para inspeção!")

if __name__ == "__main__":
    unittest.main()
