# scripts/test_connection.py
import sys
sys.path.append('backend')

from app.database import test_connection, engine
from sqlalchemy import text
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def main():
    logger.info("🔍 Testing database connection...")
    
    if test_connection():
        logger.info("✅ Connection successful!")
        
        # Test query
        try:
            with engine.connect() as conn:
                result = conn.execute(text("SHOW TABLES"))
                tables = [row[0] for row in result]
                logger.info(f"📊 Found {len(tables)} tables: {tables}")
        except Exception as e:
            logger.error(f"❌ Error querying tables: {e}")
    else:
        logger.error("❌ Connection failed!")
        sys.exit(1)

if __name__ == "__main__":
    main()