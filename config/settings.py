import os
from dotenv import load_dotenv

load_dotenv()

class Settings:
    # APIs
    TMDB_API_KEY = os.getenv("TMDB_API_KEY", "")
    
    # Hispashare
    HISPASHARE_USER = os.getenv("HISPASHARE_USER", "")
    HISPASHARE_PASS = os.getenv("HISPASHARE_PASS", "")
    HISPASHARE_BASE_URL = "https://www.hispashare.org"
    
    # Rutas
    SERIES_PATH = os.getenv("SERIES_PATH", "")
    DB_PATH = "./series_manager.db"
    
    # Selenium
    SELENIUM_HEADLESS = os.getenv("SELENIUM_HEADLESS", "False").lower() == "true"
    SELENIUM_TIMEOUT = 30
    
    # Matching
    AUTO_MATCH_THRESHOLD = 0.85
    REVIEW_THRESHOLD = 0.70
    HISPASHARE_MATCH_THRESHOLD = 0.75
    
    # Scheduler
    CHECK_INTERVAL_HOURS = int(os.getenv("CHECK_INTERVAL_HOURS", "4"))
    
    @classmethod
    def validate(cls):
        """Valida que la configuración esté completa"""
        errors = []
        
        if not cls.TMDB_API_KEY:
            errors.append("TMDB_API_KEY no configurada")
        
        if not cls.HISPASHARE_USER:
            errors.append("HISPASHARE_USER no configurado")
            
        if not cls.HISPASHARE_PASS:
            errors.append("HISPASHARE_PASS no configurada")
            
        if not cls.SERIES_PATH:
            errors.append("SERIES_PATH no configurada")
            
        return errors
    
    @classmethod
    def save_settings(cls, settings_dict):
        """Guarda configuración en archivo .env"""
        with open('.env', 'w') as f:
            for key, value in settings_dict.items():
                f.write(f"{key}={value}\n")