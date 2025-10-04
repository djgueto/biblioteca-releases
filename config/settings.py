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
    
    # eMule
    EMULE_URL = os.getenv("EMULE_URL", "http://localhost:4711")
    EMULE_USERNAME = os.getenv("EMULE_USERNAME", "admin")
    EMULE_PASSWORD = os.getenv("EMULE_PASSWORD", "")
    EMULE_AUTO_SEND = os.getenv("EMULE_AUTO_SEND", "False").lower() == "true"
    
    # Rutas
    SERIES_PATH = os.getenv("SERIES_PATH", "")
    SERIES_PATHS = os.getenv("SERIES_PATHS", "")  # Múltiples rutas separadas por ;
    DB_PATH = "./series_manager.db"
    
    @classmethod
    def get_series_paths(cls):
        """Obtener todas las rutas de series configuradas"""
        paths = []
        
        # Agregar ruta principal si existe
        if cls.SERIES_PATH and cls.SERIES_PATH.strip():
            paths.append(cls.SERIES_PATH.strip())
        
        # Agregar rutas adicionales si existen
        if cls.SERIES_PATHS and cls.SERIES_PATHS.strip():
            additional_paths = [path.strip() for path in cls.SERIES_PATHS.split(';') if path.strip()]
            paths.extend(additional_paths)
        
        # Eliminar duplicados manteniendo el orden
        unique_paths = []
        for path in paths:
            if path not in unique_paths:
                unique_paths.append(path)
        
        return unique_paths
    
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
    
    @classmethod
    def save_series_paths(cls, paths_list):
        """Guarda las rutas de series en el archivo .env"""
        if not paths_list:
            return
        
        # La primera ruta va a SERIES_PATH, las demás a SERIES_PATHS
        primary_path = paths_list[0] if paths_list else ""
        additional_paths = ";".join(paths_list[1:]) if len(paths_list) > 1 else ""
        
        # Leer configuración actual
        current_settings = {}
        try:
            with open('.env', 'r') as f:
                for line in f:
                    if '=' in line and not line.strip().startswith('#'):
                        key, value = line.strip().split('=', 1)
                        current_settings[key] = value
        except FileNotFoundError:
            pass
        
        # Actualizar rutas
        current_settings['SERIES_PATH'] = primary_path
        current_settings['SERIES_PATHS'] = additional_paths
        
        # Guardar configuración actualizada
        cls.save_settings(current_settings)
    
    def get_emule_config(self):
        """Obtener configuración de eMule"""
        return {
            'url': self.EMULE_URL,
            'username': self.EMULE_USERNAME,
            'password': self.EMULE_PASSWORD,
            'auto_send': self.EMULE_AUTO_SEND
        }