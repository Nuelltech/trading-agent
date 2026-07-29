# KNOWN ISSUES & API PECULIARITIES

Documento de registo de peculiaridades, anomalias de formato e limitações conhecidas das APIs de ingestão de dados financeiros e macroeconómicos do Trading Agent.

---

## 1. Yahoo Finance (`yfinance`)

### A. Tickers de Sovereign Yields (`^TNX`, `^TYX`, `^IRX`)
* **Problema**: O Yahoo Finance altera ocasionalmente a escala de saída das taxas de juro soberanas entre atualizações de versão sem aviso prévio. Em certas versões devolve a taxa multiplicada por 10 (ex: `46.37` para 4.637%), noutras devolve a taxa em % direto (ex: `4.637`).
* **Mitigação**: O `data_validator.py` impõe um intervalo rígido de plausibilidade (`[0.5, 10.0]`). Se um valor for recebido como `0.4637` ou `46.37`, a rotina ajusta a escala ou envia o registo para a tabela `data_anomalies_log`.

### B. Contratos Futuros de Commodities (`GC=F`, `CL=F`, `BZ=F`)
* **Problema**: As velas diárias (EOD) da Yahoo Finance para futuros juntam o preço de abertura da sessão *Globex* (pré-mercado noturno) com os máximos/mínimos da sessão regular de negociação (RTH). Isso pode fazer com que o `open_val` pareça fora do intervalo `[low_val, high_val]`.
* **Mitigação**: O `data_validator.py` executa a sanitização matemática rígida:
  - `high_val = max(high_val, open_val, close_val)`
  - `low_val = min(low_val, open_val, close_val)`

---

## 2. Federal Reserve & Economic Data (FRED / FMP API)

### A. CME FedWatch Tool & Futures de Fed Funds
* **Problema**: O CME FedWatch Tool não disponibiliza uma API REST pública e gratuita.
* **Mitigação**: Os *forecasts* de decisões do FOMC em datas futuras são calculados via curva de contratos futuros de *Fed Funds* (`FF=F`) ou atualizados via calendários institucionais verificados (`FED_OFFICIAL`), evitando dependência de mocks genéricos.

### B. Gestão de Ingestões em Fallback / Contingência
* **Problema**: Fallbacks silenciosos com dados mockados genéricos mascaram a ausência de conectividade.
* **Mitigação**: É expressamente proibido usar `SYSTEM_FEED` genérico em dados simulados. Todos os dados de contingência são etiquetados obrigatoriamente como `MOCK_DATA_FALLBACK` ou com fontes oficiais reais verificadas (`FED_OFFICIAL`, `BLS_OFFICIAL`, `ECB_OFFICIAL`).

---

## 3. Data Quality Engine & Pipeline Rules

1. **Limiares Adaptativos por Classe de Ativo**:
   - `^VIX`: Max variation threshold = 35%
   - `BZ=F` / `CL=F`: Max variation threshold = 12%
   - `^TNX`: Max variation threshold = 8%
   - `DX-Y.NYB`: Max variation threshold = 3%
   - `EURUSD=X` / `GBPUSD=X`: Max variation threshold = 2.5%
   - Stock / ETF Default: Max variation threshold = 15%

2. **Regra de Clusters de Calendário**:
   - Nenhum par de bancos centrais (`Fed`, `BCE`, `BoE`, `BoJ`, `PBoC`) pode coincidir na mesma data.
   - Máximo de 3 eventos de impacto `HIGH` por dia.
