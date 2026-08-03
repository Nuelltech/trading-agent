# KNOWN ISSUES & PECULIARIDADES DE APIS
**Especificação Técnica v1.0 — Secção 8**

Documento oficial de registo de limitações conhecidas, comportamentos anómalos de fontes de dados e convenções de calibração do Trading Agent.

---

## 1. Yahoo Finance (`yfinance`)

- **`^TNX` / `^TYX` (Sovereign Yields)**: Histórico de mudança repentina de formato de yield sem aviso prévio (confirmados 2 formatos diferentes na mesma semana: % direto vs. base 100). Validar sempre contra `PLAUSIBILITY_LIMITS` (`[0.5, 10.0]`) antes de gravar em produção.
- **Futuros de Commodities (`GC=F`, `CL=F`, `BZ=F`)**: Podem misturar a sessão *Globex* (noturna/pre-market) e a sessão *RTH* (regular) no OHLC diário, causando `open ≈ low/high` aparentemente estranhos — nem sempre é erro, mas deve ser sanitizado matematicamente (`low <= min(open, close)` / `high >= max(open, close)`).

---

## 2. Yields Soberanas sem Ticker Yahoo Fiável

- **Bund 10Y (Alemanha) / JGB 10Y (Japão)**: Ausência de ticker diário `yfinance` 100% fiável. 
- **Fonte Alternativa**: Ingestão via FRED API (`IRLTLT01DEM156N` e `IRLTLT01JPM156N`) ou Trading Economics API (free tier) / atualização de segurança.

---

## 3. Federal Reserve & CME FedWatch Tool

- **CME FedWatch**: Sem API REST pública gratuita. O *forecast* de decisões futuras do FOMC deve ser tratado como estimativa derivada da curva de futuros de *Fed Funds* (`FF=F`), não como dado direto de mercado em tempo real, até ser obtida fonte alternativa.

---

## 4. Categoria Forex (`EURUSD=X`, `USDJPY=X`, `GBPUSD=X`, etc.)

- **Ausência de Volume Real**: Tickers de Forex em mercado *OTC* possuem volume sempre 0 ou apenas *tick volume* não consolidado.
- **Regra Rígida**: Nunca usar ativos Forex para o cálculo de Volume Profile (VPVR). O `vpvr_ondemand.py` bloqueia expressamente esta categoria (`if asset_class == "FOREX": skip_vpvr()`).
- **Futuro**: Caso seja estritamente necessário volume real centralizado para o Euro, migrar para contratos futuros da CME (`6E=F`).

---

## 5. Parâmetro de Sensibilidade de Sweeps ($K$)

- **$K = 1.5$**: Valor de referência calibrado para **S&P 500 (`^GSPC`)**, **Ouro (`GC=F`)** e **EUR/USD (`EURUSD=X`)**.
- **$K = 1.0$ (Ajuste para Commodities)**: Para **Brent (`BZ=F`)** e **WTI (`CL=F`)**, o fator $K$ ajustado é 1.0 devido à dinâmica de pavios diários e volatilidade intradiária em relação ao $ATR_{60}$.
- **Regra de Recalibração**: Recalibrar se forem adicionados novos ativos com perfis de volatilidade substancialmente diferentes.

---

## 6. Fronteira Não-Negociável

- O sistema **NUNCA** executa ordens de forma autónoma. O output máximo é um alerta analítico humano. Toda a decisão de entrada passa obrigatoriamente pelo **Briefing Pré-Trade** antes de qualquer execução.

---

## 7. Convenções e Anomalias Conhecidas em Fontes Macro (FMP / FRED / Calendário)

- **Decisões de Taxa de Juro Central (BoJ Scale Error)**:
  - O Banco do Japão (BoJ) publicou a taxa de política de 1.0%. Fontes externas/FMP podem por vezes reportar frações em formato não-normalizado (ex: 0.15%). O sistema sanitiza e valida sempre as taxas em percentagem direta (1.0% para BoJ, 3.75% para Fed, 3.50% para BCE, 5.00% para BoE, 3.45% para PBoC).
- **Proteção do Campo `Real` (`actual_val`)**:
  - O upsert no MySQL usa `COALESCE(VALUES(actual_val), economic_calendar.actual_val)` para garantir que uma leitura nula subsequente nunca apaga o valor real previamente gravado.
- **Yields Soberanas FRED (`IRLTLT01DEM156N`, `IRLTLT01GBM156N`, `IRLTLT01JPM156N`)**:
  - Séries mensais/diferidas do FRED utilizam fallback ao último valor gravado no MySQL para garantir a disponibilidade constante no painel `OHLC Ativos Vigiados`.
- **Regime de Volatilidade VIX por Percentil**:
  - O regime de risco é classificado por percentil móvel até 252 sessões (<40: Baixa Vol, 40-85: Transição, >85: Pânico). Em cold-start (<60 sessões), usam-se limiares fixos (<15, 15-20, >20) acompanhados da nota explicativa `"Cold-Start (Percentil Indisponível)"`.
