-- scripts/seed_indicators.sql
-- Catálogo Mestre de Indicadores e Ativos para Ingestão e Análise

-- 1. Tabela de Catálogo de Indicadores
CREATE TABLE IF NOT EXISTS indicators_catalog (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    category ENUM('VOLATILITY', 'BONDS', 'FOREX', 'COMMODITIES', 'INDICES', 'STOCKS') NOT NULL,
    ticker VARCHAR(50) NOT NULL UNIQUE,
    data_provider ENUM('YFINANCE', 'FRED_API', 'TRADING_ECONOMICS') NOT NULL DEFAULT 'YFINANCE',
    region VARCHAR(50) DEFAULT 'GLOBAL',
    value_multiplier DECIMAL(6, 3) DEFAULT 1.000, -- Ex: 0.100 para ^TNX ou ^TYX que vêm multiplicados por 10
    is_active BOOLEAN DEFAULT TRUE,
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 2. Tabela de Histórico de Valores/Cotações de Indicadores
CREATE TABLE IF NOT EXISTS indicator_values (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    indicator_id INT NOT NULL,
    symbol VARCHAR(50) NOT NULL,
    timestamp DATETIME NOT NULL,
    value DECIMAL(16, 6) NOT NULL, -- Preço de fecho ou valor do indicador
    open_val DECIMAL(16, 6),
    high_val DECIMAL(16, 6),
    low_val DECIMAL(16, 6),
    volume BIGINT DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (indicator_id) REFERENCES indicators_catalog(id) ON DELETE CASCADE,
    UNIQUE KEY uq_indicator_time (indicator_id, timestamp),
    INDEX idx_symbol_time (symbol, timestamp),
    INDEX idx_timestamp (timestamp)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 3. Inserção dos 42 Indicadores e Ativos Unificados dos Consultores 1 e 2

INSERT INTO indicators_catalog (name, category, ticker, data_provider, region, value_multiplier, notes) VALUES
-- Volatilidade e Sentimento
('VIX Volatility Index', 'VOLATILITY', '^VIX', 'YFINANCE', 'EUA', 1.0, 'Termómetro principal de volatilidade S&P500'),
('VSTOXX Euro Volatility', 'VOLATILITY', 'VSTOXX', 'FRED_API', 'Europa', 1.0, 'Stress e volatilidade Euro Stoxx 50'),

-- Obrigações Soberanas (Yields & ETFs)
('US 10-Year Treasury Yield', 'BONDS', '^TNX', 'YFINANCE', 'EUA', 1.0, 'Yield US 10Y'),
('US 2-Year Treasury Yield', 'BONDS', 'DGS2', 'FRED_API', 'EUA', 1.0, 'Yield US 2Y vinda da FRED API'),
('US 30-Year Treasury Yield', 'BONDS', '^TYX', 'YFINANCE', 'EUA', 1.0, 'Yield US 30Y'),
('Bund Alemão 10-Year Yield', 'BONDS', 'IRLTLT01DEM156N', 'FRED_API', 'Europa', 1.0, 'Referência dívida Zona Euro'),
('Gilt UK 10-Year Yield', 'BONDS', 'IRLTLT01GBM156N', 'FRED_API', 'Reino Unido', 1.0, 'Referência dívida UK'),
('JGB Japonês 10-Year Yield', 'BONDS', 'IRLTLT01JPM156N', 'FRED_API', 'Japão', 1.0, 'Política monetária BoJ'),
('iShares 20+ Year Treasury ETF', 'BONDS', 'TLT', 'YFINANCE', 'EUA', 1.0, 'ETF Preço de obrigações de longo prazo EUA'),

-- Moedas (Forex)
('US Dollar Index (DXY)', 'FOREX', 'DX-Y.NYB', 'YFINANCE', 'Global', 1.0, 'Índice de Força do Dólar'),
('EUR/USD', 'FOREX', 'EURUSD=X', 'YFINANCE', 'Global', 1.0, 'Euro / Dólar Americano'),
('USD/JPY', 'FOREX', 'USDJPY=X', 'YFINANCE', 'Global', 1.0, 'Dólar / Iene Japonês (Refúgio/Carry)'),
('GBP/USD', 'FOREX', 'GBPUSD=X', 'YFINANCE', 'Global', 1.0, 'Libra / Dólar Americano'),
('USD/CNH', 'FOREX', 'USDCNH=X', 'YFINANCE', 'Ásia', 1.0, 'Dólar / Yuan Offshore (Stress China)'),
('USD/CHF', 'FOREX', 'USDCHF=X', 'YFINANCE', 'Europa', 1.0, 'Dólar / Franco Suíço (Refúgio)'),

-- Commodities / Âncoras Reais
('Brent Crude Oil', 'COMMODITIES', 'BZ=F', 'YFINANCE', 'Global', 1.0, 'Futuros Petróleo Brent'),
('WTI Crude Oil', 'COMMODITIES', 'CL=F', 'YFINANCE', 'Global', 1.0, 'Futuros Petróleo WTI'),
('Gold Futures', 'COMMODITIES', 'GC=F', 'YFINANCE', 'Global', 1.0, 'Futuros de Ouro'),
('Silver Futures', 'COMMODITIES', 'SI=F', 'YFINANCE', 'Global', 1.0, 'Futuros de Prata'),
('Copper Futures', 'COMMODITIES', 'HG=F', 'YFINANCE', 'Global', 1.0, 'Futuros de Cobre ("Dr. Copper")'),
('Natural Gas Futures', 'COMMODITIES', 'NG=F', 'YFINANCE', 'Global', 1.0, 'Futuros Gás Natural'),

-- Índices de Ações (Américas)
('S&P 500', 'INDICES', '^GSPC', 'YFINANCE', 'EUA', 1.0, 'Índice Geral de Ações EUA'),
('Nasdaq 100', 'INDICES', '^NDX', 'YFINANCE', 'EUA', 1.0, 'Índice de Tecnologia EUA'),
('Dow Jones Industrial', 'INDICES', '^DJI', 'YFINANCE', 'EUA', 1.0, 'Índice Industrial EUA'),
('Russell 2000', 'INDICES', '^RUT', 'YFINANCE', 'EUA', 1.0, 'Small Caps EUA'),
('SOX Semiconductor Index', 'INDICES', '^SOX', 'YFINANCE', 'EUA', 1.0, 'Índice de Semicondutores'),

-- Índices de Ações (Europa)
('DAX 40', 'INDICES', '^GDAXI', 'YFINANCE', 'Alemanha', 1.0, 'Índice Alemanha'),
('CAC 40', 'INDICES', '^FCHI', 'YFINANCE', 'França', 1.0, 'Índice França'),
('FTSE 100', 'INDICES', '^FTSE', 'YFINANCE', 'Reino Unido', 1.0, 'Índice Reino Unido'),
('Euro Stoxx 50', 'INDICES', '^STOXX50E', 'YFINANCE', 'Europa', 1.0, '50 Maiores Ações Zona Euro'),
('IBEX 35', 'INDICES', '^IBEX', 'YFINANCE', 'Espanha', 1.0, 'Índice Espanha'),

-- Índices de Ações (Ásia / Oceania)
('Nikkei 225', 'INDICES', '^N225', 'YFINANCE', 'Japão', 1.0, 'Índice Japão'),
('Hang Seng', 'INDICES', '^HSI', 'YFINANCE', 'Hong Kong', 1.0, 'Índice Hong Kong'),
('Shanghai Composite', 'INDICES', '000001.SS', 'YFINANCE', 'China', 1.0, 'Índice China Continental'),
('Kospi Index', 'INDICES', '^KS11', 'YFINANCE', 'Coreia do Sul', 1.0, 'Índice Coreia do Sul'),
('ASX 200', 'INDICES', '^AXJO', 'YFINANCE', 'Austrália', 1.0, 'Índice Austrália'),

-- Inventário de Ações Específicas
('Realty Income', 'STOCKS', 'O', 'YFINANCE', 'EUA', 1.0, 'REIT / Imobiliário EUA'),
('Delta Air Lines', 'STOCKS', 'DAL', 'YFINANCE', 'EUA', 1.0, 'Aviação / Transportes EUA'),
('Ford Motor Company', 'STOCKS', 'F', 'YFINANCE', 'EUA', 1.0, 'Automóvel Tradicional EUA'),
('Enphase Energy', 'STOCKS', 'ENPH', 'YFINANCE', 'EUA', 1.0, 'Energia Solar / Renováveis'),
('Nike', 'STOCKS', 'NKE', 'YFINANCE', 'EUA', 1.0, 'Consumo / Retalho Global'),
('Stellantis', 'STOCKS', 'STLA', 'YFINANCE', 'EUA/Europa', 1.0, 'Automóvel Global')
ON DUPLICATE KEY UPDATE 
    name = VALUES(name),
    category = VALUES(category),
    data_provider = VALUES(data_provider),
    value_multiplier = VALUES(value_multiplier),
    notes = VALUES(notes);
