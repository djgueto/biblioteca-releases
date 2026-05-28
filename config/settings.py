import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[1]
ENV_PATH = BASE_DIR / ".env"
load_dotenv(dotenv_path=ENV_PATH)

def _resolve_path(value: str) -> str:
    p = Path(value).expanduser()
    if not p.is_absolute():
        p = (BASE_DIR / p).resolve()
    return str(p)

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
    
    # Rutas para otros tipos de contenido
    MOVIES_PATH = os.getenv("MOVIES_PATH", "")
    MOVIES_PATHS = os.getenv("MOVIES_PATHS", "")  # Múltiples rutas separadas por ;
    
    DOCUMENTARIES_PATH = os.getenv("DOCUMENTARIES_PATH", "")
    DOCUMENTARIES_PATHS = os.getenv("DOCUMENTARIES_PATHS", "")  # Múltiples rutas separadas por ;
    
    MUSIC_PATH = os.getenv("MUSIC_PATH", "")
    MUSIC_PATHS = os.getenv("MUSIC_PATHS", "")  # Múltiples rutas separadas por ;
    
    DB_PATH = _resolve_path(os.getenv("DB_PATH", "series_manager.db"))
    
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
    
    @classmethod
    def get_movies_paths(cls):
        """Obtener todas las rutas de películas configuradas"""
        paths = []
        
        # Agregar ruta principal si existe
        if cls.MOVIES_PATH and cls.MOVIES_PATH.strip():
            paths.append(cls.MOVIES_PATH.strip())
        
        # Agregar rutas adicionales si existen
        if cls.MOVIES_PATHS and cls.MOVIES_PATHS.strip():
            additional_paths = [path.strip() for path in cls.MOVIES_PATHS.split(';') if path.strip()]
            paths.extend(additional_paths)
        
        # Eliminar duplicados manteniendo el orden
        unique_paths = []
        for path in paths:
            if path not in unique_paths:
                unique_paths.append(path)
        
        return unique_paths
    
    @classmethod
    def get_documentaries_paths(cls):
        """Obtener todas las rutas de documentales configuradas"""
        paths = []
        
        # Agregar ruta principal si existe
        if cls.DOCUMENTARIES_PATH and cls.DOCUMENTARIES_PATH.strip():
            paths.append(cls.DOCUMENTARIES_PATH.strip())
        
        # Agregar rutas adicionales si existen
        if cls.DOCUMENTARIES_PATHS and cls.DOCUMENTARIES_PATHS.strip():
            additional_paths = [path.strip() for path in cls.DOCUMENTARIES_PATHS.split(';') if path.strip()]
            paths.extend(additional_paths)
        
        # Eliminar duplicados manteniendo el orden
        unique_paths = []
        for path in paths:
            if path not in unique_paths:
                unique_paths.append(path)
        
        return unique_paths
    
    @classmethod
    def get_music_paths(cls):
        """Obtener todas las rutas de música configuradas"""
        paths = []
        
        # Agregar ruta principal si existe
        if cls.MUSIC_PATH and cls.MUSIC_PATH.strip():
            paths.append(cls.MUSIC_PATH.strip())
        
        # Agregar rutas adicionales si existen
        if cls.MUSIC_PATHS and cls.MUSIC_PATHS.strip():
            additional_paths = [path.strip() for path in cls.MUSIC_PATHS.split(';') if path.strip()]
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
    
    # Contenido
    CONTENT_SCAN_ENABLED = os.getenv("CONTENT_SCAN_ENABLED", "True").lower() == "true"
    CONTENT_DISCOVERY_PAGES = int(os.getenv("CONTENT_DISCOVERY_PAGES", "3"))  # Número de páginas a buscar en descubrimiento
    
    # Calidad y preferencias
    WAIT_FOR_4K = os.getenv("WAIT_FOR_4K", "True").lower() == "true"  # Esperar por 4K/2160p si está disponible
    WAIT_FOR_4K_MAX_WAIT_DAYS = int(os.getenv("WAIT_FOR_4K_MAX_WAIT_DAYS", "7"))
    PREFERRED_MIN_QUALITY = os.getenv("PREFERRED_MIN_QUALITY", "1080p")  # Calidad mínima preferida
    MINIMUM_MOVIE_SIZE_GB = float(os.getenv("MINIMUM_MOVIE_SIZE_GB", "4.0"))  # Tamaño mínimo para películas en GB
    # Tamaño máximo opcional para películas (0 o vacío deshabilita el filtro máximo)
    MAXIMUM_MOVIE_SIZE_GB = float(os.getenv("MAXIMUM_MOVIE_SIZE_GB", "0"))

    # Búsqueda en biblioteca
    LIBRARY_SEARCH_MAX_DEPTH = int(os.getenv("LIBRARY_SEARCH_MAX_DEPTH", "2"))  # Profundidad máxima de subcarpetas para detectar contenido existente
    
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
        with open(ENV_PATH, 'w', encoding='utf-8') as f:
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
            with open(ENV_PATH, 'r', encoding='utf-8') as f:
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
    
    @classmethod
    def save_movies_paths(cls, paths_list):
        """Guarda las rutas de películas en el archivo .env"""
        if not paths_list:
            return
        
        # La primera ruta va a MOVIES_PATH, las demás a MOVIES_PATHS
        primary_path = paths_list[0] if paths_list else ""
        additional_paths = ";".join(paths_list[1:]) if len(paths_list) > 1 else ""
        
        # Leer configuración actual
        current_settings = {}
        try:
            with open(ENV_PATH, 'r', encoding='utf-8') as f:
                for line in f:
                    if '=' in line and not line.strip().startswith('#'):
                        key, value = line.strip().split('=', 1)
                        current_settings[key] = value
        except FileNotFoundError:
            pass
        
        # Actualizar rutas
        current_settings['MOVIES_PATH'] = primary_path
        current_settings['MOVIES_PATHS'] = additional_paths
        
        # Guardar configuración actualizada
        cls.save_settings(current_settings)
    
    @classmethod
    def save_documentaries_paths(cls, paths_list):
        """Guarda las rutas de documentales en el archivo .env"""
        if not paths_list:
            return
        
        # La primera ruta va a DOCUMENTARIES_PATH, las demás a DOCUMENTARIES_PATHS
        primary_path = paths_list[0] if paths_list else ""
        additional_paths = ";".join(paths_list[1:]) if len(paths_list) > 1 else ""
        
        # Leer configuración actual
        current_settings = {}
        try:
            with open(ENV_PATH, 'r', encoding='utf-8') as f:
                for line in f:
                    if '=' in line and not line.strip().startswith('#'):
                        key, value = line.strip().split('=', 1)
                        current_settings[key] = value
        except FileNotFoundError:
            pass
        
        # Actualizar rutas
        current_settings['DOCUMENTARIES_PATH'] = primary_path
        current_settings['DOCUMENTARIES_PATHS'] = additional_paths
        
        # Guardar configuración actualizada
        cls.save_settings(current_settings)
    
    @classmethod
    def save_music_paths(cls, paths_list):
        """Guarda las rutas de música en el archivo .env"""
        if not paths_list:
            return
        
        # La primera ruta va a MUSIC_PATH, las demás a MUSIC_PATHS
        primary_path = paths_list[0] if paths_list else ""
        additional_paths = ";".join(paths_list[1:]) if len(paths_list) > 1 else ""
        
        # Leer configuración actual
        current_settings = {}
        try:
            with open(ENV_PATH, 'r', encoding='utf-8') as f:
                for line in f:
                    if '=' in line and not line.strip().startswith('#'):
                        key, value = line.strip().split('=', 1)
                        current_settings[key] = value
        except FileNotFoundError:
            pass
        
        # Actualizar rutas
        current_settings['MUSIC_PATH'] = primary_path
        current_settings['MUSIC_PATHS'] = additional_paths
        
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
