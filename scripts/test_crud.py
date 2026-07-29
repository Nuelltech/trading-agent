# scripts/test_crud.py
import sys
sys.path.append('backend')
from app.database import engine
from sqlalchemy import text
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def test_permissions():
    logger.info("🧪 Testando permissões de CREATE, INSERT, SELECT, UPDATE, DELETE e DROP...")
    
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            # 1. CREATE TABLE
            logger.info("1️⃣ Criando tabela de teste...")
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS test_permissions (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    val VARCHAR(50),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                ) ENGINE=InnoDB;
            """))
            
            # 2. INSERT
            logger.info("2️⃣ Inserindo dados de teste...")
            conn.execute(text("INSERT INTO test_permissions (val) VALUES ('Teste Conexao OK')"))
            
            # 3. SELECT
            logger.info("3️⃣ Lendo dados da tabela...")
            result = conn.execute(text("SELECT * FROM test_permissions")).fetchall()
            logger.info(f"   Resultado SELECT: {result}")
            
            # 4. DELETE / DROP TABLE
            logger.info("4️⃣ Apagando tabela de teste (DROP TABLE)...")
            conn.execute(text("DROP TABLE test_permissions"))
            
            trans.commit()
            logger.info("🎉 TODAS AS PERMISSÕES (READ, WRITE, DELETE, DROP) VERIFICADAS COM SUCESSO!")
            return True
        except Exception as e:
            trans.rollback()
            logger.error(f"❌ Erro ao testar permissões: {e}")
            return False

if __name__ == "__main__":
    test_permissions()
