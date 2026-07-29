-- scripts/setup_quality_tables.sql
-- DDL para Tabelas de Staging e Quarentena (Data Quality Pipeline)

USE habimark_trading_agent_db;

-- 1. Tabela Staging para Indicadores de Mercado (Cotações Brutas)
CREATE TABLE IF NOT EXISTS staging_indicator_values (
    id INT AUTO_INCREMENT PRIMARY KEY,
    symbol VARCHAR(30) NOT NULL,
    timestamp DATETIME NOT NULL,
    open_val DECIMAL(14,4),
    high_val DECIMAL(14,4),
    low_val DECIMAL(14,4),
    value DECIMAL(14,4) NOT NULL,
    volume BIGINT DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_symbol_time (symbol, timestamp)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 2. Tabela Staging para Eventos do Calendário Económico
CREATE TABLE IF NOT EXISTS staging_economic_calendar (
    id INT AUTO_INCREMENT PRIMARY KEY,
    event_name VARCHAR(150) NOT NULL,
    country VARCHAR(50) NOT NULL,
    currency VARCHAR(10) NOT NULL,
    event_timestamp DATETIME NOT NULL,
    impact_level ENUM('HIGH', 'MEDIUM', 'LOW') DEFAULT 'HIGH',
    actual_val DECIMAL(14,4),
    forecast_val DECIMAL(14,4),
    previous_val DECIMAL(14,4),
    unit VARCHAR(20) DEFAULT '%',
    source_provider VARCHAR(50) DEFAULT 'STAGING_FEED',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_event_time (event_name, event_timestamp)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 3. Tabela de Quarentena e Log de Anomalias de Dados
CREATE TABLE IF NOT EXISTS data_anomalies_log (
    id INT AUTO_INCREMENT PRIMARY KEY,
    target_table VARCHAR(50) NOT NULL,
    symbol_or_event VARCHAR(150) NOT NULL,
    raw_value VARCHAR(255),
    expected_range VARCHAR(100),
    anomaly_type ENUM('OHLC_VIOLATION', 'OUTOFBOUNDS_PLAUSIBILITY', 'PERCENT_SPIKE', 'CALENDAR_CLUSTER', 'UNVERIFIED_MOCK') NOT NULL,
    anomaly_reason TEXT NOT NULL,
    status ENUM('PENDING', 'RESOLVED_APPROVED', 'RESOLVED_REJECTED') DEFAULT 'PENDING',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_status_time (status, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
