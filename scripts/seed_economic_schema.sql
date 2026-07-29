-- scripts/seed_economic_schema.sql
-- Estrutura de tabelas para Calendário Económico e Calendário de Earnings Corporativos

-- 1. Tabela do Calendário Económico (Macro)
CREATE TABLE IF NOT EXISTS economic_calendar (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    event_name VARCHAR(150) NOT NULL,
    country VARCHAR(50) NOT NULL,            -- ex: 'EUA', 'Zona Euro', 'Alemanha', 'Japão', 'China', 'UK'
    currency VARCHAR(10) NOT NULL,           -- ex: 'USD', 'EUR', 'JPY', 'GBP', 'CNY'
    event_timestamp DATETIME NOT NULL,       -- Data e Hora GMT da publicação exata
    impact_level ENUM('HIGH', 'MEDIUM', 'LOW') NOT NULL DEFAULT 'HIGH',
    actual_val DECIMAL(16, 4) DEFAULT NULL,   -- Valor Real publicado
    forecast_val DECIMAL(16, 4) DEFAULT NULL, -- Valor Projetado / Consenso do mercado
    previous_val DECIMAL(16, 4) DEFAULT NULL, -- Valor do mês/período anterior
    unit VARCHAR(20) DEFAULT NULL,           -- ex: '%', 'K', 'M', 'B', 'Index'
    source_provider VARCHAR(50) DEFAULT 'FMP',
    processed_for_analysis BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_event_time_country (event_name, event_timestamp, country),
    INDEX idx_event_timestamp (event_timestamp),
    INDEX idx_impact_currency (impact_level, currency),
    INDEX idx_country (country)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 2. Tabela do Calendário de Earnings Corporativos (Micro Bottom-Up)
CREATE TABLE IF NOT EXISTS corporate_earnings_calendar (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL,              -- ex: 'O', 'DAL', 'F', 'ENPH', 'NKE', 'STLA'
    company_name VARCHAR(150),
    event_date DATE NOT NULL,
    time_of_day ENUM('BEFORE_MARKET', 'AFTER_MARKET', 'DURING_SESSION', 'UNKNOWN') DEFAULT 'UNKNOWN',
    eps_estimate DECIMAL(10, 4) DEFAULT NULL,
    eps_actual DECIMAL(10, 4) DEFAULT NULL,
    revenue_estimate DECIMAL(18, 2) DEFAULT NULL,
    revenue_actual DECIMAL(18, 2) DEFAULT NULL,
    fiscal_period VARCHAR(20) DEFAULT NULL,   -- ex: 'Q1 2026', 'Q2 2026'
    source_provider VARCHAR(50) DEFAULT 'FMP',
    processed_for_analysis BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_symbol_event_date (symbol, event_date),
    INDEX idx_symbol (symbol),
    INDEX idx_event_date (event_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
