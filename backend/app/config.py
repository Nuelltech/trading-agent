# backend/app/config.py
from pydantic_settings import BaseSettings
from typing import Optional

class Settings(BaseSettings):
    # Database
    MYSQL_HOST: str = "93.127.192.117"
    MYSQL_PORT: int = 3306
    MYSQL_USER: str = "habimark_trading_agent_db_admin"
    MYSQL_PASSWORD: str = "Pgrquhh81"
    MYSQL_DATABASE: str = "habimark_trading_agent_db"
    
    # Anthropic
    ANTHROPIC_API_KEY: Optional[str] = None
    
    # Alpaca
    ALPACA_API_KEY: Optional[str] = None
    ALPACA_SECRET_KEY: Optional[str] = None
    ALPACA_BASE_URL: str = "https://paper-api.alpaca.markets"
    
    # News APIs
    NEWS_API_KEY: Optional[str] = None
    ALPHA_VANTAGE_KEY: Optional[str] = None
    
    # System
    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"
    TRADING_MODE: str = "PAPER"
    SECRET_KEY: str = "default_secret_key_for_dev_mode"
    
    @property
    def database_url(self) -> str:
        return f"mysql+pymysql://{self.MYSQL_USER}:{self.MYSQL_PASSWORD}@{self.MYSQL_HOST}:{self.MYSQL_PORT}/{self.MYSQL_DATABASE}"
    
    class Config:
        env_file = ".env"
        case_sensitive = True

settings = Settings()


