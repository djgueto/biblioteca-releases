import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[1]
# En Docker el .env vive en el volumen de configuración (APP_ENV_FILE=/config/.env).
ENV_PATH = Path(os.getenv("APP_ENV_FILE") or BASE_DIR / ".env")
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
    
    CARTOONS_PATH = os.getenv("CARTOONS_PATH", "")
    CARTOONS_PATHS = os.getenv("CARTOONS_PATHS", "")  # Múltiples rutas separadas por ;

    ANIMATED_MOVIES_PATH = os.getenv("ANIMATED_MOVIES_PATH", "")
    ANIMATED_MOVIES_PATHS = os.getenv("ANIMATED_MOVIES_PATHS", "")  # Múltiples rutas separadas por ;

    DOCUSERIES_PATH = os.getenv("DOCUSERIES_PATH", "")
    DOCUSERIES_PATHS = os.getenv("DOCUSERIES_PATHS", "")  # Múltiples rutas separadas por ;

    CONCERTS_PATH = os.getenv("CONCERTS_PATH", "")
    CONCERTS_PATHS = os.getenv("CONCERTS_PATHS", "")  # Múltiples rutas separadas por ;

    MUSIC_PATH = os.getenv("MUSIC_PATH", "")
    MUSIC_PATHS = os.getenv("MUSIC_PATHS", "")  # Múltiples rutas separadas por ;
    
    DB_PATH = _resolve_path(os.getenv("DB_PATH", "series_manager.db"))
    COVER_CACHE_DIR = _resolve_path(os.getenv("COVER_CACHE_DIR", "cache/covers"))

    LIBRARY_CATEGORIES = {
        'series': {
            'label': 'Series',
            'primary_key': 'SERIES_PATH',
            'additional_key': 'SERIES_PATHS',
            'content_mode': 'episodic',
        },
        'movies': {
            'label': 'Películas',
            'primary_key': 'MOVIES_PATH',
            'additional_key': 'MOVIES_PATHS',
            'content_mode': 'single_file',
        },
        'documentaries': {
            'label': 'Documentales',
            'primary_key': 'DOCUMENTARIES_PATH',
            'additional_key': 'DOCUMENTARIES_PATHS',
            'content_mode': 'single_file',
        },
        'cartoons': {
            'label': 'Dibujos',
            'primary_key': 'CARTOONS_PATH',
            'additional_key': 'CARTOONS_PATHS',
            'content_mode': 'episodic',
        },
        'animated_movies': {
            'label': 'Películas Animación',
            'primary_key': 'ANIMATED_MOVIES_PATH',
            'additional_key': 'ANIMATED_MOVIES_PATHS',
            'content_mode': 'single_file',
        },
        'docuseries': {
            'label': 'Documentales Series',
            'primary_key': 'DOCUSERIES_PATH',
            'additional_key': 'DOCUSERIES_PATHS',
            'content_mode': 'episodic',
        },
        'concerts': {
            'label': 'Conciertos',
            'primary_key': 'CONCERTS_PATH',
            'additional_key': 'CONCERTS_PATHS',
            'content_mode': 'single_file',
        },
        'music': {
            'label': 'Música',
            'primary_key': 'MUSIC_PATH',
            'additional_key': 'MUSIC_PATHS',
            'content_mode': 'single_file',
        },
    }

    @classmethod
    def get_library_categories(cls):
        return cls.LIBRARY_CATEGORIES

    @classmethod
    def get_category_label(cls, category):
        config = cls.LIBRARY_CATEGORIES.get(category, {})
        return config.get('label', category)

    @classmethod
    def get_category_content_mode(cls, category):
        config = cls.LIBRARY_CATEGORIES.get(category, {})
        return config.get('content_mode', 'single_file')

    @classmethod
    def get_categories_by_mode(cls, content_mode):
        return [
            category
            for category, config in cls.LIBRARY_CATEGORIES.items()
            if config.get('content_mode') == content_mode
        ]

    @classmethod
    def get_single_file_categories(cls):
        return cls.get_categories_by_mode('single_file')

    @classmethod
    def get_episodic_categories(cls):
        return cls.get_categories_by_mode('episodic')

    @classmethod
    def get_paths_for_category(cls, category):
        """Obtener todas las rutas configuradas para una categoría."""
        config = cls.LIBRARY_CATEGORIES.get(category)
        if not config:
            return []

        primary_value = getattr(cls, config['primary_key'], "")
        additional_value = getattr(cls, config['additional_key'], "")
        paths = []

        if primary_value and primary_value.strip():
            paths.append(primary_value.strip())

        if additional_value and additional_value.strip():
            paths.extend(
                path.strip()
                for path in additional_value.split(';')
                if path.strip()
            )

        unique_paths = []
        for path in paths:
            if path not in unique_paths:
                unique_paths.append(path)

        return unique_paths

    @classmethod
    def save_paths_for_category(cls, category, paths_list):
        """Guardar rutas de una categoría de biblioteca en el archivo .env."""
        config = cls.LIBRARY_CATEGORIES.get(category)
        if not config or paths_list is None:
            return

        primary_path = paths_list[0] if paths_list else ""
        additional_paths = ";".join(paths_list[1:]) if len(paths_list) > 1 else ""

        current_settings = {}
        try:
            with open(ENV_PATH, 'r', encoding='utf-8') as f:
                for line in f:
                    if '=' in line and not line.strip().startswith('#'):
                        key, value = line.strip().split('=', 1)
                        current_settings[key] = value
        except FileNotFoundError:
            pass

        current_settings[config['primary_key']] = primary_path
        current_settings[config['additional_key']] = additional_paths
        cls.save_settings(current_settings)
    
    @classmethod
    def get_series_paths(cls):
        """Obtener todas las rutas de series configuradas"""
        return cls.get_paths_for_category('series')
    
    @classmethod
    def get_movies_paths(cls):
        """Obtener todas las rutas de películas configuradas"""
        return cls.get_paths_for_category('movies')
    
    @classmethod
    def get_documentaries_paths(cls):
        """Obtener todas las rutas de documentales configuradas"""
        return cls.get_paths_for_category('documentaries')

    @classmethod
    def get_cartoons_paths(cls):
        """Obtener todas las rutas de dibujos configuradas"""
        return cls.get_paths_for_category('cartoons')

    @classmethod
    def get_animated_movies_paths(cls):
        """Obtener todas las rutas de películas de animación configuradas"""
        return cls.get_paths_for_category('animated_movies')

    @classmethod
    def get_docuseries_paths(cls):
        """Obtener todas las rutas de documentales de series configuradas"""
        return cls.get_paths_for_category('docuseries')

    @classmethod
    def get_concerts_paths(cls):
        """Obtener todas las rutas de conciertos configuradas"""
        return cls.get_paths_for_category('concerts')
    
    @classmethod
    def get_music_paths(cls):
        """Obtener todas las rutas de música configuradas"""
        return cls.get_paths_for_category('music')
    
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

    # Reintento de enlaces ed2k atascados (enviados a eMule pero nunca completados)
    ED2K_RETRY_ENABLED = os.getenv("ED2K_RETRY_ENABLED", "True").lower() == "true"
    ED2K_RETRY_AFTER_DAYS = int(os.getenv("ED2K_RETRY_AFTER_DAYS", "3"))

    UPGRADE_ENABLED = os.getenv("UPGRADE_ENABLED", "False").lower() == "true"
    UPGRADE_TARGET_QUALITY = os.getenv("UPGRADE_TARGET_QUALITY", "2160p")
    UPGRADE_MAX_SERIES_PER_CYCLE = int(os.getenv("UPGRADE_MAX_SERIES_PER_CYCLE", "15"))
    UPGRADE_MAX_LINKS_PER_CYCLE = int(os.getenv("UPGRADE_MAX_LINKS_PER_CYCLE", "80"))

    # Wolfmax4k
    WOLFMAX_UPGRADE_ENABLED = os.getenv("WOLFMAX_UPGRADE_ENABLED", "False").lower() == "true"
    WOLFMAX_STAGING_DIR = os.getenv("WOLFMAX_STAGING_DIR", "")
    WOLFMAX_MAX_DOWNLOADS_PER_CYCLE = int(os.getenv("WOLFMAX_MAX_DOWNLOADS_PER_CYCLE", "2"))
    WOLFMAX_MATCH_THRESHOLD = float(os.getenv("WOLFMAX_MATCH_THRESHOLD", "0.70"))

    # Cliente torrent tipo "blackhole": los .torrent se dejan en esta carpeta
    # y el cliente torrent (vigilándola) se encarga de descargarlos.
    TORRENT_WATCH_DIR = os.getenv("TORRENT_WATCH_DIR", "")

    @classmethod
    def get_torrent_watch_dir(cls) -> str:
        # WOLFMAX_STAGING_DIR era esa misma carpeta antes de que el blackhole
        # fuera común a todos los proveedores torrent.
        return (cls.TORRENT_WATCH_DIR or cls.WOLFMAX_STAGING_DIR or "").strip()

    # Búsqueda en biblioteca
    LIBRARY_SEARCH_MAX_DEPTH = int(os.getenv("LIBRARY_SEARCH_MAX_DEPTH", "2"))  # Profundidad máxima de subcarpetas para detectar contenido existente

    CONTENT_SCAN_MIN_RATING = float(os.getenv("CONTENT_SCAN_MIN_RATING", "5.5"))

    # Biblioteca - renombrado automático
    AUTO_RENAME_FOLDER_ON_TMDB_ASSIGN = os.getenv("AUTO_RENAME_FOLDER_ON_TMDB_ASSIGN", "True").lower() == "true"
    
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
            
        episodic_paths = (
            cls.get_series_paths() +
            cls.get_cartoons_paths() +
            cls.get_docuseries_paths()
        )
        if not episodic_paths:
            errors.append("No hay rutas episódicas configuradas (series, dibujos o documentales series)")
            
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
        cls.save_paths_for_category('series', paths_list)
    
    @classmethod
    def save_movies_paths(cls, paths_list):
        """Guarda las rutas de películas en el archivo .env"""
        cls.save_paths_for_category('movies', paths_list)
    
    @classmethod
    def save_documentaries_paths(cls, paths_list):
        """Guarda las rutas de documentales en el archivo .env"""
        cls.save_paths_for_category('documentaries', paths_list)

    @classmethod
    def save_cartoons_paths(cls, paths_list):
        """Guarda las rutas de dibujos en el archivo .env"""
        cls.save_paths_for_category('cartoons', paths_list)

    @classmethod
    def save_animated_movies_paths(cls, paths_list):
        """Guarda las rutas de películas de animación en el archivo .env"""
        cls.save_paths_for_category('animated_movies', paths_list)

    @classmethod
    def save_docuseries_paths(cls, paths_list):
        """Guarda las rutas de documentales series en el archivo .env"""
        cls.save_paths_for_category('docuseries', paths_list)

    @classmethod
    def save_concerts_paths(cls, paths_list):
        """Guarda las rutas de conciertos en el archivo .env"""
        cls.save_paths_for_category('concerts', paths_list)
    
    @classmethod
    def save_music_paths(cls, paths_list):
        """Guarda las rutas de música en el archivo .env"""
        cls.save_paths_for_category('music', paths_list)

    def get_emule_config(self):
        """Obtener configuración de eMule"""
        return {
            'url': self.EMULE_URL,
            'username': self.EMULE_USERNAME,
            'password': self.EMULE_PASSWORD,
            'auto_send': self.EMULE_AUTO_SEND
        }
