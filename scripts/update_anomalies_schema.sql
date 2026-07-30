-- DDL Update: Atualização do schema da tabela data_anomalies_log para suporte a deduplicação e escalonamento
ALTER TABLE data_anomalies_log 
    ADD COLUMN occurrences INT DEFAULT 1,
    ADD COLUMN first_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
    ADD COLUMN last_seen DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP;
