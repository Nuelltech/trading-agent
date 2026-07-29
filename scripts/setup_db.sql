-- setup_db.sql

-- Tabela de configuração do sistema
CREATE TABLE system_config (
    id INT PRIMARY KEY AUTO_INCREMENT,
    config_key VARCHAR(100) UNIQUE NOT NULL,
    config_value TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Tabela de histórico de preços
CREATE TABLE stock_prices (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    symbol VARCHAR(20) NOT NULL,
    timestamp DATETIME NOT NULL,
    open_price DECIMAL(12, 4),
    high_price DECIMAL(12, 4),
    low_price DECIMAL(12, 4),
    close_price DECIMAL(12, 4),
    volume BIGINT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_symbol_timestamp (symbol, timestamp),
    INDEX idx_timestamp (timestamp)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Tabela de notícias
CREATE TABLE news_articles (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    title TEXT NOT NULL,
    content TEXT,
    source VARCHAR(100),
    author VARCHAR(200),
    published_at DATETIME NOT NULL,
    url TEXT,
    sentiment_score DECIMAL(5, 4),
    relevance_score DECIMAL(5, 4),
    symbols JSON,
    processed BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_published (published_at),
    INDEX idx_processed (processed),
    INDEX idx_source (source)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Tabela de análise de notícias pelo Claude
CREATE TABLE news_analysis (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    news_id BIGINT NOT NULL,
    sentiment VARCHAR(20),
    confidence DECIMAL(5, 4),
    relevance_for_trading DECIMAL(5, 4),
    affected_stocks JSON,
    key_points JSON,
    potential_impact VARCHAR(50),
    reasoning TEXT,
    analyzed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (news_id) REFERENCES news_articles(id) ON DELETE CASCADE,
    INDEX idx_news_id (news_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Tabela de indicadores técnicos
CREATE TABLE technical_indicators (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    symbol VARCHAR(20) NOT NULL,
    timestamp DATETIME NOT NULL,
    rsi DECIMAL(6, 3),
    macd DECIMAL(10, 6),
    macd_signal DECIMAL(10, 6),
    macd_histogram DECIMAL(10, 6),
    bb_upper DECIMAL(12, 4),
    bb_middle DECIMAL(12, 4),
    bb_lower DECIMAL(12, 4),
    sma_20 DECIMAL(12, 4),
    sma_50 DECIMAL(12, 4),
    sma_200 DECIMAL(12, 4),
    ema_12 DECIMAL(12, 4),
    ema_26 DECIMAL(12, 4),
    volume_sma BIGINT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_symbol_timestamp (symbol, timestamp)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Tabela de decisões do agente
CREATE TABLE trading_decisions (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    decision_timestamp DATETIME NOT NULL,
    symbol VARCHAR(20) NOT NULL,
    action ENUM('BUY', 'SELL', 'HOLD') NOT NULL,
    quantity INT,
    reasoning TEXT,
    confidence_score DECIMAL(5, 4),
    news_sources JSON,
    technical_indicators JSON,
    stop_loss DECIMAL(12, 4),
    take_profit DECIMAL(12, 4),
    time_horizon VARCHAR(20),
    risk_score DECIMAL(5, 4),
    executed BOOLEAN DEFAULT FALSE,
    execution_status VARCHAR(50),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_symbol (symbol),
    INDEX idx_executed (executed),
    INDEX idx_decision_timestamp (decision_timestamp)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Tabela de trades executados
CREATE TABLE trades (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    decision_id BIGINT,
    symbol VARCHAR(20) NOT NULL,
    action ENUM('BUY', 'SELL') NOT NULL,
    quantity INT NOT NULL,
    price DECIMAL(12, 4) NOT NULL,
    executed_at DATETIME NOT NULL,
    commission DECIMAL(10, 4) DEFAULT 0,
    status VARCHAR(20) NOT NULL,
    order_id VARCHAR(100),
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (decision_id) REFERENCES trading_decisions(id) ON DELETE SET NULL,
    INDEX idx_symbol (symbol),
    INDEX idx_executed_at (executed_at),
    INDEX idx_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Tabela de portfolio atual
CREATE TABLE portfolio (
    id INT PRIMARY KEY AUTO_INCREMENT,
    symbol VARCHAR(20) UNIQUE NOT NULL,
    quantity INT NOT NULL,
    avg_purchase_price DECIMAL(12, 4) NOT NULL,
    current_price DECIMAL(12, 4),
    current_value DECIMAL(12, 2),
    unrealized_pnl DECIMAL(12, 2),
    unrealized_pnl_percent DECIMAL(6, 3),
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_symbol (symbol)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Tabela de histórico de portfolio (snapshots diários)
CREATE TABLE portfolio_history (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    snapshot_date DATE NOT NULL,
    symbol VARCHAR(20) NOT NULL,
    quantity INT NOT NULL,
    price DECIMAL(12, 4) NOT NULL,
    value DECIMAL(12, 2) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY unique_snapshot (snapshot_date, symbol),
    INDEX idx_date (snapshot_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Tabela de métricas de performance
CREATE TABLE performance_metrics (
    id INT PRIMARY KEY AUTO_INCREMENT,
    metric_date DATE UNIQUE NOT NULL,
    total_portfolio_value DECIMAL(12, 2),
    cash_balance DECIMAL(12, 2),
    invested_capital DECIMAL(12, 2),
    daily_return DECIMAL(8, 5),
    cumulative_return DECIMAL(8, 5),
    sharpe_ratio DECIMAL(6, 4),
    max_drawdown DECIMAL(6, 4),
    win_rate DECIMAL(5, 4),
    total_trades INT DEFAULT 0,
    winning_trades INT DEFAULT 0,
    losing_trades INT DEFAULT 0,
    avg_win DECIMAL(12, 2),
    avg_loss DECIMAL(12, 2),
    profit_factor DECIMAL(6, 4),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_date (metric_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Tabela de gestão de budget
CREATE TABLE budget_management (
    id INT PRIMARY KEY AUTO_INCREMENT,
    timestamp DATETIME NOT NULL,
    total_capital DECIMAL(12, 2) NOT NULL,
    available_cash DECIMAL(12, 2) NOT NULL,
    invested_capital DECIMAL(12, 2) NOT NULL,
    reserved_cash DECIMAL(12, 2) NOT NULL,
    max_position_size DECIMAL(12, 2) NOT NULL,
    max_daily_loss DECIMAL(12, 2) NOT NULL,
    current_daily_loss DECIMAL(12, 2) DEFAULT 0,
    positions_count INT DEFAULT 0,
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_timestamp (timestamp)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Tabela de regras de risco
CREATE TABLE risk_rules (
    id INT PRIMARY KEY AUTO_INCREMENT,
    rule_name VARCHAR(100) UNIQUE NOT NULL,
    rule_type VARCHAR(50) NOT NULL,
    rule_value DECIMAL(10, 4) NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    description TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Tabela de logs do sistema
CREATE TABLE system_logs (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    log_level VARCHAR(20) NOT NULL,
    component VARCHAR(100) NOT NULL,
    message TEXT NOT NULL,
    details JSON,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_level_component (log_level, component),
    INDEX idx_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Tabela de avaliação de decisões (post-mortem)
CREATE TABLE decision_evaluation (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    decision_id BIGINT NOT NULL,
    evaluation_date DATE NOT NULL,
    days_after INT NOT NULL,
    actual_price_movement DECIMAL(8, 5),
    prediction_correct BOOLEAN,
    confidence_calibrated BOOLEAN,
    lessons_learned TEXT,
    evaluated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (decision_id) REFERENCES trading_decisions(id) ON DELETE CASCADE,
    INDEX idx_decision (decision_id),
    INDEX idx_evaluation_date (evaluation_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Inserir regras de risco padrão
INSERT INTO risk_rules (rule_name, rule_type, rule_value, description) VALUES
('MAX_POSITION_SIZE_PERCENT', 'PERCENTAGE', 15.00, 'Máximo 15% do capital por posição'),
('MAX_DAILY_LOSS_PERCENT', 'PERCENTAGE', 3.00, 'Máximo 3% de perda diária'),
('MAX_OPEN_POSITIONS', 'COUNT', 10.00, 'Máximo 10 posições abertas simultaneamente'),
('EMERGENCY_RESERVE_PERCENT', 'PERCENTAGE', 20.00, 'Mínimo 20% em cash sempre'),
('STOP_LOSS_PERCENT', 'PERCENTAGE', 5.00, 'Stop loss padrão de 5%'),
('TAKE_PROFIT_PERCENT', 'PERCENTAGE', 10.00, 'Take profit padrão de 10%'),
('MIN_CONFIDENCE_SCORE', 'DECIMAL', 0.65, 'Confiança mínima para executar trade');

-- Inserir configuração inicial do sistema
INSERT INTO system_config (config_key, config_value) VALUES
('TRADING_MODE', 'PAPER'),
('INITIAL_CAPITAL', '10000.00'),
('PAPER_TRADING_ENABLED', 'TRUE'),
('NEWS_COLLECTION_INTERVAL_MINUTES', '15'),
('MARKET_DATA_UPDATE_INTERVAL_MINUTES', '5'),
('ANALYSIS_INTERVAL_MINUTES', '30');

-- Inserir budget inicial
INSERT INTO budget_management (timestamp, total_capital, available_cash, invested_capital, reserved_cash, max_position_size, max_daily_loss) 
VALUES (NOW(), 10000.00, 10000.00, 0.00, 2000.00, 1500.00, 300.00);