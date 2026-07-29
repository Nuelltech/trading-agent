# scripts/run_setup_and_seed.py
import sys
import os
sys.path.append('backend')

from app.database import engine
from sqlalchemy import text
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

def run_sql_file(filename):
    if not os.path.exists(filename):
        logging.error(f"Ficheiro não encontrado: {filename}")
        return False
        
    logging.info(f"📄 A executar ficheiro SQL: {filename}")
    with open(filename, "r", encoding="utf-8") as f:
        sql_content = f.read()
        
    # Divide os comandos SQL pelo ponto e vírgula ';'
    statements = [stmt.strip() for stmt in sql_content.split(";") if stmt.strip()]
    
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            executed_count = 0
            for stmt in statements:
                # Ignorar comentários de linha inteira se houver
                if stmt.startswith("--") and "\n" not in stmt:
                    continue
                conn.execute(text(stmt))
                executed_count += 1
            trans.commit()
            logging.info(f"✅ {executed_count} instruções executadas com sucesso em {filename}")
            return True
        except Exception as e:
            trans.rollback()
            logging.error(f"❌ Erro ao executar {filename}: {e}")
            return False

def verify_setup():
    logging.info("🔍 Verificando tabelas criadas no banco de dados...")
    with engine.connect() as conn:
        result = conn.execute(text("SHOW TABLES")).fetchall()
        tables = [row[0] for row in result]
        logging.info(f"📊 Tabelas encontradas na base de dados ({len(tables)}): {tables}")
        
        # Verificar contagem no catálogo de indicadores
        if "indicators_catalog" in tables:
            count = conn.execute(text("SELECT COUNT(*) FROM indicators_catalog")).scalar()
            logging.info(f"🎯 Total de indicadores cadastrados na tabela 'indicators_catalog': {count}")

def main():
    logging.info("🚀 Iniciando criação e povoamento da base de dados...")
    
    # 1. Executar setup_db.sql
    if run_sql_file("scripts/setup_db.sql"):
        logging.info("1️⃣ setup_db.sql concluído!")
    else:
        logging.error("Falha no setup_db.sql")
        return
        
    # 2. Executar seed_indicators.sql
    if run_sql_file("scripts/seed_indicators.sql"):
        logging.info("2️⃣ seed_indicators.sql concluído!")
    else:
        logging.error("Falha no seed_indicators.sql")
        return
        
    # 3. Verificar tabelas e registros
    verify_setup()

if __name__ == "__main__":
    main()
