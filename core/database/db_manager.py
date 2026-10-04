import sqlite3
import logging
import os
import subprocess
import threading
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse, parse_qs
from config.settings import Settings
from core.discovery.repository import ensure_placeholder_column


def _keep_measured_quality(episode: dict, previous: dict | None) -> dict:
    """Si el archivo es el mismo que en el escaneo anterior (misma ruta y
    tamaño), conserva la calidad ya medida en vez de la deducida del nombre.
    Si cambió de tamaño (sustituido, p. ej. por una mejora con el mismo
    nombre) se descarta para que se vuelva a medir."""
    if not previous:
        return episode
    old_size, new_size = previous.get('size_mb'), episode.get('size_mb')
    if old_size is not None and new_size is not None and abs(old_size - new_size) > 0.01:
        return episode
    merged = dict(episode)
    if previous.get('quality_source') == 'mediainfo':
        # Medido del propio archivo: manda sobre lo que diga el nombre.
        for key in ('resolution_height', 'codec', 'hdr', 'quality_rank', 'quality_source', 'measured_at'):
            merged[key] = previous.get(key)
    elif previous.get('quality_rank') == -1:
        # MediaInfo no pudo leerlo: se respeta la espera antes de reintentar.
        merged['quality_rank'] = -1
        merged['measured_at'] = previous.get('measured_at')
    return merged


class DatabaseManager:
    # Cada petición o job crea su propio DatabaseManager y lo cierra al
    # terminar; sin esto, cada uno pagaría una apertura de conexión. Se
    # comparte una única conexión sqlite3 por hilo (identificada por hilo +
    # ruta), nunca entre hilos distintos.
    _thread_local = threading.local()

    # cleanup_deleted_series no borra más de esto en un ciclo (protege contra
    # una ruta de biblioteca mal configurada o inaccesible).
    CLEANUP_MAX_DELETIONS = 20
    CLEANUP_MAX_DELETION_RATIO = 0.25

    # Versión del esquema que deja initialize_database (PRAGMA user_version).
    SCHEMA_VERSION = 2

    # Búsqueda en Hispashare desactivada sola (no se encontró): se reintenta
    # pasado este tiempo, por si la han subido. Nunca si la apagó el usuario.
    SEARCH_RETRY_DAYS = 30
    SEARCH_RETRY_PER_CYCLE = 20

    def __init__(self, db_path=None):
        self.db_path = db_path or Settings.DB_PATH
        self.logger = logging.getLogger(__name__)
        self.conn = None
        self._drive_unc_map = None

    def _is_shareable_path(self):
        # ":memory:" (y variantes vacías) son privadas por conexión por
        # diseño de sqlite3 — compartirlas entre instancias "distintas" en el
        # mismo hilo mezclaría datos entre ellas (p. ej. entre tests). Solo
        # se cachea por hilo una ruta de archivo real.
        return bool(self.db_path) and self.db_path != ":memory:"

    def get_connection(self):
        """Obtener conexión a la base de datos, reutilizando la del hilo actual si existe"""
        if self.conn is not None:
            return self.conn

        if not self._is_shareable_path():
            self.conn = self._open_connection()
            return self.conn

        cache = getattr(self._thread_local, "connections", None)
        if cache is None:
            cache = {}
            self._thread_local.connections = cache

        conn = cache.get(self.db_path)
        if conn is None:
            conn = self._open_connection()
            cache[self.db_path] = conn

        self.conn = conn
        return self.conn

    def _open_connection(self):
        # WAL + busy timeout reduce "database is locked" errors when
        # scheduler and UI workers hit SQLite concurrently.
        conn = sqlite3.connect(
            self.db_path,
            check_same_thread=False,
            timeout=60.0,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout = 60000")
        conn.execute("PRAGMA synchronous = NORMAL")
        return conn

    def close(self):
        """Cerrar la conexión (o soltar la referencia si es compartida por hilo).

        Para una ruta de archivo real, la conexión se comparte entre las
        instancias de DatabaseManager de un mismo hilo (ver
        get_connection), así que aquí solo se libera la referencia local para
        que otras tareas en ese hilo puedan seguir usándola. Para ":memory:"
        (nunca compartida) sí se cierra de verdad, como antes.
        """
        if self.conn is not None and not self._is_shareable_path():
            self.conn.close()
        self.conn = None
    
    def initialize_database(self):
        """Crear tablas si no existen y aplicar las migraciones.

        Con la BD en el NAS, cada ALTER/commit cuesta: si la BD ya está en
        SCHEMA_VERSION (PRAGMA user_version) no se repite nada. Al añadir una
        migración aquí, súbase SCHEMA_VERSION.
        """
        conn = self.get_connection()
        if conn.execute("PRAGMA user_version").fetchone()[0] >= self.SCHEMA_VERSION:
            return
        cursor = conn.cursor()

        # Tabla de series
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS series (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                folder_path TEXT UNIQUE NOT NULL,
                content_category TEXT DEFAULT 'series',
                tmdb_id INTEGER,
                tmdb_name TEXT,
                year INTEGER,
                confidence_score REAL,
                needs_review BOOLEAN DEFAULT 0,
                hispashare_url TEXT,
                hispashare_cover_url TEXT,
                status TEXT DEFAULT 'pending',
                search_disabled BOOLEAN DEFAULT 0,
                search_failures INTEGER DEFAULT 0,
                monitored BOOLEAN DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Tabla de episodios locales
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS local_episodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                series_id INTEGER NOT NULL,
                season INTEGER NOT NULL,
                episode INTEGER NOT NULL,
                filename TEXT NOT NULL,
                file_path TEXT UNIQUE NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (series_id) REFERENCES series(id) ON DELETE CASCADE,
                UNIQUE(series_id, season, episode)
            )
        ''')
        
        # Tabla de episodios faltantes
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS missing_episodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                series_id INTEGER NOT NULL,
                series_name TEXT,
                season INTEGER NOT NULL,
                episode INTEGER NOT NULL,
                name TEXT,
                air_date TEXT,
                status TEXT DEFAULT 'missing',
                hispashare_url TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (series_id) REFERENCES series(id) ON DELETE CASCADE,
                UNIQUE(series_id, season, episode)
            )
        ''')
        
        # Tabla de logs
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                level TEXT NOT NULL,
                message TEXT NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS source_matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                series_id INTEGER NOT NULL,
                provider TEXT NOT NULL DEFAULT 'hispashare',
                source_url TEXT,
                source_title TEXT,
                cover_url TEXT,
                match_score REAL DEFAULT 0,
                match_status TEXT DEFAULT 'pending',
                validation_reason TEXT,
                validated_at TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS source_offers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                series_id INTEGER NOT NULL,
                provider TEXT NOT NULL,
                source_url TEXT,
                resolution_height INTEGER,
                codec TEXT,
                hdr TEXT DEFAULT 'unknown',
                match_score REAL DEFAULT 0,
                available INTEGER DEFAULT 1,
                last_checked_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(series_id, provider)
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS decision_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                series_id INTEGER NOT NULL,
                decision_type TEXT NOT NULL,
                message TEXT NOT NULL,
                context_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Índices
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_series_tmdb ON series(tmdb_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_series_category ON series(content_category)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_episodes_series ON local_episodes(series_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_missing_series ON missing_episodes(series_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_source_matches_series_id ON source_matches(series_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_source_offers_series ON source_offers(series_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_decision_logs_series_id ON decision_logs(series_id)')
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_missing_status_order "
            "ON missing_episodes(status, series_name, season, episode)"
        )
        
        conn.commit()
        
        # Migración: Agregar campo search_disabled si no existe
        try:
            cursor.execute("ALTER TABLE series ADD COLUMN search_disabled BOOLEAN DEFAULT 0")
            conn.commit()
            self.logger.info("Campo search_disabled agregado a la tabla series")
        except Exception:
            # El campo ya existe, no hacer nada
            pass

        try:
            cursor.execute("ALTER TABLE series ADD COLUMN monitored BOOLEAN DEFAULT 1")
            conn.commit()
            self.logger.info("Campo monitored agregado a la tabla series")
        except Exception:
            pass

        try:
            cursor.execute("ALTER TABLE series ADD COLUMN search_failures INTEGER DEFAULT 0")
            conn.commit()
            self.logger.info("Campo search_failures agregado a la tabla series")
        except Exception:
            pass

        # Cuándo y cómo se desactivó la búsqueda automática: las desactivadas
        # solas se reintentan cada SEARCH_RETRY_DAYS; las del usuario, nunca.
        for column_sql, column_name in (
            ("ALTER TABLE series ADD COLUMN search_disabled_at TIMESTAMP", "search_disabled_at"),
            ("ALTER TABLE series ADD COLUMN search_disabled_manual INTEGER DEFAULT 0", "search_disabled_manual"),
        ):
            try:
                cursor.execute(column_sql)
                conn.commit()
                self.logger.info(f"Campo {column_name} agregado a la tabla series")
            except Exception:
                pass
        try:
            cursor.execute(
                "UPDATE series SET search_disabled_at = COALESCE(updated_at, CURRENT_TIMESTAMP) "
                "WHERE COALESCE(search_disabled, 0) = 1 AND search_disabled_at IS NULL"
            )
            conn.commit()
        except Exception:
            pass

        try:
            cursor.execute(
                '''
                UPDATE series
                SET search_disabled = 0,
                    search_failures = 2,
                    updated_at = CURRENT_TIMESTAMP
                WHERE COALESCE(search_disabled, 0) = 1
                  AND COALESCE(search_failures, 0) = 0
                  AND (hispashare_url IS NULL OR hispashare_url = '')
                '''
            )
            conn.commit()
        except Exception:
            pass

        # Migración: Agregar campo hispashare_cover_url si no existe
        try:
            cursor.execute("ALTER TABLE series ADD COLUMN hispashare_cover_url TEXT")
            conn.commit()
            self.logger.info("Campo hispashare_cover_url agregado a la tabla series")
        except Exception:
            pass

        try:
            cursor.execute("ALTER TABLE series ADD COLUMN content_category TEXT DEFAULT 'series'")
            conn.commit()
            self.logger.info("Campo content_category agregado a la tabla series")
        except Exception:
            pass

        for column_sql, column_name in (
            ("ALTER TABLE local_episodes ADD COLUMN resolution_height INTEGER", "resolution_height"),
            ("ALTER TABLE local_episodes ADD COLUMN codec TEXT", "codec"),
            ("ALTER TABLE local_episodes ADD COLUMN source TEXT", "source"),
            ("ALTER TABLE local_episodes ADD COLUMN size_mb REAL", "size_mb"),
            ("ALTER TABLE local_episodes ADD COLUMN quality_rank INTEGER DEFAULT 0", "quality_rank"),
            ("ALTER TABLE local_episodes ADD COLUMN hdr TEXT", "hdr"),
            # 'mediainfo' = calidad leída del propio archivo (la única fiable);
            # 'filename' o NULL = deducida del nombre, provisional hasta medirla.
            ("ALTER TABLE local_episodes ADD COLUMN quality_source TEXT", "quality_source"),
            ("ALTER TABLE local_episodes ADD COLUMN measured_at TIMESTAMP", "measured_at"),
        ):
            try:
                cursor.execute(column_sql)
                conn.commit()
                self.logger.info(f"Campo {column_name} agregado a la tabla local_episodes")
            except Exception:
                pass

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS ed2k_history (
                link TEXT PRIMARY KEY,
                kind TEXT,
                series_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_ed2k_history_series ON ed2k_history(series_id)')

        for column_sql, column_name in (
            ("ALTER TABLE ed2k_history ADD COLUMN last_sent_at TIMESTAMP", "last_sent_at"),
            ("ALTER TABLE ed2k_history ADD COLUMN retry_count INTEGER DEFAULT 0", "retry_count"),
        ):
            try:
                cursor.execute(column_sql)
                conn.commit()
                self.logger.info(f"Campo {column_name} agregado a la tabla ed2k_history")
            except Exception:
                pass

        try:
            cursor.execute("UPDATE ed2k_history SET last_sent_at = created_at WHERE last_sent_at IS NULL")
            conn.commit()
        except Exception:
            pass

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS wolfmax_history (
                url TEXT PRIMARY KEY,
                series_id INTEGER,
                status TEXT DEFAULT 'downloaded',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_wolfmax_history_series ON wolfmax_history(series_id)')
        conn.commit()

        # Backfill idempotente de source_offers desde datos ya existentes.
        # INSERT OR IGNORE respeta el UNIQUE(series_id, provider): si la fila
        # ya existe (de un backfill anterior o de un upsert posterior), no la
        # toca. Decodifica una sola vez el hack match_score = height/2160 con
        # el que Wolfmax venía guardando la calidad en source_matches.
        try:
            cursor.execute(
                '''
                INSERT OR IGNORE INTO source_offers
                    (series_id, provider, source_url, resolution_height, match_score, available)
                SELECT series_id, 'wolfmax', source_url,
                       CAST(ROUND(match_score * 2160) AS INTEGER),
                       match_score, 1
                FROM source_matches
                WHERE provider = 'wolfmax' AND source_url IS NOT NULL AND source_url != ''
                '''
            )
            conn.commit()
        except Exception:
            pass

        try:
            cursor.execute(
                '''
                INSERT OR IGNORE INTO source_offers
                    (series_id, provider, source_url, resolution_height, match_score, available)
                SELECT id, 'hispashare', hispashare_url, NULL, 0, 1
                FROM series
                WHERE hispashare_url IS NOT NULL AND hispashare_url != ''
                '''
            )
            conn.commit()
        except Exception:
            pass

        ensure_placeholder_column(self)
        # Los ALTER de arriba se tragan cualquier error: solo se da la BD por
        # migrada si las columnas están de verdad (si no, se reintenta).
        expected = {
            "series": {"search_disabled", "monitored", "search_failures", "hispashare_cover_url",
                       "content_category", "placeholder", "search_disabled_at", "search_disabled_manual"},
            "local_episodes": {"resolution_height", "codec", "source", "size_mb", "quality_rank", "hdr",
                               "quality_source", "measured_at"},
            "ed2k_history": {"last_sent_at", "retry_count"},
        }
        if all(columns <= {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
               for table, columns in expected.items()):
            conn.execute(f"PRAGMA user_version = {self.SCHEMA_VERSION}")
            conn.commit()
        self.logger.info("Base de datos inicializada correctamente")
    
    # ========== SERIES ==========

    def get_episodic_categories(self):
        return ('series', 'cartoons', 'docuseries')

    def _build_category_filter(self, categories):
        if not categories:
            return "", []
        if isinstance(categories, str):
            categories = [categories]
        placeholders = ", ".join(["?"] * len(categories))
        return f" AND s.content_category IN ({placeholders})", list(categories)

    def _build_series_category_filter(self, categories):
        if not categories:
            return "", []
        if isinstance(categories, str):
            categories = [categories]
        placeholders = ", ".join(["?"] * len(categories))
        return f" WHERE content_category IN ({placeholders})", list(categories)
    
    def normalize_path(self, path: str) -> str:
        """
        Normaliza rutas para evitar duplicados entre rutas de red y unidades mapeadas
        """
        if os.name != 'nt':
            # Linux/macOS (Docker, NAS): rutas absolutas normales, sin
            # resolver enlaces simbólicos (los volúmenes montados se ven tal
            # cual los configuró el usuario) y nunca con '\'.
            if not path:
                return path
            return os.path.normpath(os.path.abspath(path))
        try:
            # Convertir a Path para normalización básica
            normalized = Path(path).resolve()
            
            # Si es una ruta de red, mantenerla como tal
            if str(normalized).startswith('\\\\'):
                return str(normalized).replace('/', '\\')
            
            # Si es una unidad mapeada, intentar obtener la ruta real de red
            if os.name == 'nt' and len(str(normalized)) > 1 and str(normalized)[1] == ':':
                try:
                    drive_letter = str(normalized)[:2].upper()
                    drive_unc_map = self._get_drive_unc_map()
                    unc_root = drive_unc_map.get(drive_letter)
                    if unc_root:
                        relative_path = str(normalized)[2:]  # Quitar "C:"
                        if relative_path.startswith('\\'):
                            relative_path = relative_path[1:]
                        return os.path.join(unc_root, relative_path).replace('/', '\\')
                except Exception:
                    pass
            
            return str(normalized).replace('/', '\\')
            
        except Exception as e:
            logging.warning(f"Error normalizando ruta {path}: {e}")
            return path

    def _get_drive_unc_map(self, force_refresh=False):
        if self._drive_unc_map is not None and not force_refresh:
            return self._drive_unc_map

        drive_unc_map = {}
        if os.name != 'nt':
            self._drive_unc_map = drive_unc_map
            return drive_unc_map

        try:
            result = subprocess.run(
                ['net', 'use'],
                capture_output=True,
                text=True,
                shell=False,
            )
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    parts = line.split()
                    if len(parts) < 3:
                        continue
                    drive_letter = None
                    unc_path = None
                    for part in parts:
                        upper_part = part.upper()
                        if len(upper_part) == 2 and upper_part[1] == ':':
                            drive_letter = upper_part
                        elif part.startswith('\\\\'):
                            unc_path = part
                    if drive_letter and unc_path:
                        drive_unc_map[drive_letter] = unc_path
        except Exception as e:
            self.logger.debug(f"No se pudo obtener el mapa de unidades de red: {e}")

        self._drive_unc_map = drive_unc_map
        return drive_unc_map

    def insert_or_update_series(self, series_data):
        """Insertar o actualizar una serie"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        try:
            # Normalizar la ruta antes de buscar
            normalized_path = self.normalize_path(series_data['folder_path'])
            content_category = series_data.get('content_category', 'series')
            
            # Verificar si existe por ruta normalizada (insensible a mayúsculas/minúsculas)
            # Coincidencia exacta primero (usa el índice); el lower() solo como
            # respaldo porque impide usar índice y recorre toda la tabla.
            cursor.execute(
                'SELECT id, folder_path, content_category FROM series WHERE folder_path = ?',
                (normalized_path,)
            )
            existing = cursor.fetchone()
            if not existing:
                cursor.execute(
                    'SELECT id, folder_path, content_category FROM series WHERE lower(folder_path) = lower(?)',
                    (normalized_path,)
                )
                existing = cursor.fetchone()
            
            # Si no existe con la ruta normalizada, buscar por nombre y verificar si es la misma ubicación física
            if not existing:
                cursor.execute(
                    'SELECT id, folder_path, content_category FROM series WHERE lower(name) = lower(?) AND content_category = ?',
                    (series_data['name'], content_category)
                )
                name_matches = cursor.fetchall()
                
                for match_id, match_path, _match_category in name_matches:
                    if self.normalize_path(match_path).lower() == normalized_path.lower():
                        existing = (match_id, match_path, content_category)
                        break
            
            if existing:
                series_id = existing[0]
                current_path = existing[1]
                
                # Verificar conflicto antes de actualizar ruta
                if current_path != normalized_path:
                    # Verificar si la ruta destino ya existe en otra serie
                    cursor.execute('SELECT id FROM series WHERE folder_path = ?', (normalized_path,))
                    conflict = cursor.fetchone()
                    
                    if conflict and conflict[0] != series_id:
                        self.logger.warning(f"Conflicto de ruta detectado: '{normalized_path}' ya existe en ID {conflict[0]}. No se actualizará la ruta para ID {series_id}.")
                        # Solo actualizar nombre y fecha
                        cursor.execute('''
                            UPDATE series 
                            SET name = ?, content_category = ?, updated_at = CURRENT_TIMESTAMP
                            WHERE id = ?
                        ''', (series_data['name'], content_category, series_id))
                    else:
                        # Actualizar todo incluyendo ruta
                        cursor.execute('''
                            UPDATE series 
                            SET name = ?, folder_path = ?, content_category = ?, updated_at = CURRENT_TIMESTAMP
                            WHERE id = ?
                        ''', (series_data['name'], normalized_path, content_category, series_id))
                        self.logger.info(f"Serie actualizada: {series_data['name']} -> {normalized_path}")
                else:
                    # La ruta no cambió, solo actualizar nombre
                    cursor.execute('''
                        UPDATE series 
                        SET name = ?, content_category = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                    ''', (series_data['name'], content_category, series_id))
                    self.logger.debug(f"Serie actualizada (sin cambio de ruta): {series_data['name']}")
            else:
                # Insertar con ruta normalizada
                cursor.execute('''
                    INSERT INTO series (name, folder_path, content_category)
                    VALUES (?, ?, ?)
                ''', (series_data['name'], normalized_path, content_category))
                series_id = cursor.lastrowid
                logging.info(f"Serie insertada: {series_data['name']} -> {normalized_path}")
            
            conn.commit()
            
            # Insertar episodios
            if 'episodes' in series_data:
                self.insert_local_episodes(series_id, series_data['episodes'])
            
            return series_id
            
        except Exception as e:
            self.logger.error(f"Error en insert_or_update_series: {e}")
            conn.rollback()
            raise
    
    def update_tmdb_match(self, series_id, match_data):
        """Actualizar información de TMDB y reactivar búsqueda si aún no hay URL."""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute(
                '''
                SELECT name, hispashare_url, search_disabled, search_failures
                FROM series
                WHERE id = ?
                ''',
                (series_id,),
            )
            current_row = cursor.fetchone()
            current_data = dict(current_row) if current_row else {}
            has_hispashare_url = bool((current_data.get("hispashare_url") or "").strip())

            needs_review = match_data['needs_review']
            cursor.execute(
                'SELECT id, name FROM series WHERE tmdb_id = ? AND id != ?',
                (match_data['tmdb_id'], series_id),
            )
            duplicate = cursor.fetchone()
            if duplicate:
                needs_review = True
                self.logger.warning(
                    "tmdb_id %s ya está asignado a la serie '%s' (ID %s); "
                    "'%s' (ID %s) se marca needs_review para revisión manual.",
                    match_data['tmdb_id'], duplicate['name'], duplicate['id'],
                    current_data.get('name', series_id), series_id,
                )

            cursor.execute('''
                UPDATE series
                SET tmdb_id = ?, tmdb_name = ?, year = ?,
                    confidence_score = ?, needs_review = ?,
                    search_disabled = CASE
                        WHEN COALESCE(hispashare_url, '') = '' THEN 0
                        ELSE COALESCE(search_disabled, 0)
                    END,
                    search_failures = CASE
                        WHEN COALESCE(hispashare_url, '') = '' THEN 0
                        ELSE COALESCE(search_failures, 0)
                    END,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (
                match_data['tmdb_id'],
                match_data['name'],
                match_data['year'],
                match_data['confidence_score'],
                needs_review,
                series_id
            ))
            
            conn.commit()
            if not has_hispashare_url:
                self.logger.info(
                    "TMDB actualizado para '%s' (ID %s). Búsqueda Hispashare reactivada para usar '%s'.",
                    current_data.get("name", series_id),
                    series_id,
                    match_data.get("name", ""),
                )
            
        except Exception as e:
            self.logger.error(f"Error en update_tmdb_match: {e}")
            conn.rollback()
            raise

    def clear_tmdb_match(self, series_id):
        """Quitar vinculación TMDB de una serie (permite re-matching)"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute('''
                UPDATE series
                SET tmdb_id = NULL,
                    tmdb_name = NULL,
                    year = NULL,
                    confidence_score = NULL,
                    needs_review = 0,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (series_id,))
            conn.commit()
        except Exception as e:
            self.logger.error(f"Error en clear_tmdb_match: {e}")
            conn.rollback()
            raise

    def derive_hispashare_cover_url(self, hispashare_url: str) -> str | None:
        try:
            parsed = urlparse(hispashare_url or "")
            qs = parse_qs(parsed.query)
            title_id = (qs.get("id") or [None])[0]
            if not title_id:
                return None
            if not str(title_id).isdigit():
                return None
            return f"{Settings.HISPASHARE_BASE_URL}/images/titles/{title_id}/cover.jpg"
        except Exception:
            return None

    def update_hispashare_cover_url(self, series_id, cover_url):
        conn = self.get_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute('''
                UPDATE series
                SET hispashare_cover_url = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (cover_url, series_id))
            conn.commit()
        except Exception as e:
            self.logger.error(f"Error en update_hispashare_cover_url: {e}")
            conn.rollback()
            raise
    
    def update_hispashare_url(self, series_id, url):
        """Actualizar URL de Hispashare"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute('''
                UPDATE series
                SET hispashare_url = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (url, series_id))
            
            conn.commit()
            
        except Exception as e:
            self.logger.error(f"Error en update_hispashare_url: {e}")
            conn.rollback()
            raise
    
    def update_series_manual(self, series_id, changes, recalculate_missing=True):
        """Actualizar serie con cambios manuales"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        try:
            # Verificar si se está cambiando el TMDB ID
            tmdb_id_changed = 'tmdb_id' in changes
            hispashare_url_changed = 'hispashare_url' in changes
            reset_hispashare_url = False
            recheck_hispashare_url = False
            previous_hispashare_url = None
            
            # Construir query dinámicamente basado en los cambios
            set_clauses = []
            values = []
            
            for field, value in changes.items():
                if field in ['tmdb_id', 'tmdb_name', 'year', 'confidence_score', 
                           'needs_review', 'hispashare_url', 'status']:
                    set_clauses.append(f"{field} = ?")
                    values.append(value)

            if tmdb_id_changed:
                set_clauses.append("search_disabled = 0")
                set_clauses.append("search_failures = 0")
                set_clauses.append("search_disabled_at = NULL")
                set_clauses.append("search_disabled_manual = 0")
                if not hispashare_url_changed:
                    current_item = self.get_series_by_id(series_id)
                    previous_hispashare_url = (current_item or {}).get("hispashare_url")
                    # Si el TMDB cambia manualmente, relanzamos una verificación forzada
                    # de Hispashare con el nuevo nombre TMDB. La URL previa se mantiene
                    # temporalmente para no perderla si la nueva búsqueda no mejora.
                    recheck_hispashare_url = True

            if hispashare_url_changed:
                url_value = changes.get('hispashare_url')
                if url_value and str(url_value).strip():
                    cover_url = self.derive_hispashare_cover_url(str(url_value).strip())
                else:
                    cover_url = None
                set_clauses.append("hispashare_cover_url = ?")
                values.append(cover_url)
            
            if not set_clauses:
                self.logger.warning("No hay cambios válidos para actualizar")
                return
            
            # Añadir timestamp de actualización
            set_clauses.append("updated_at = CURRENT_TIMESTAMP")
            
            query = f"UPDATE series SET {', '.join(set_clauses)} WHERE id = ?"
            values.append(series_id)
            
            cursor.execute(query, values)
            conn.commit()
            
            self.logger.info(f"Serie {series_id} actualizada manualmente con cambios: {list(changes.keys())}")
            
            # Si se cambió el TMDB ID, recalcular episodios faltantes
            if recalculate_missing and tmdb_id_changed and changes.get('tmdb_id'):
                self.logger.info(f"Recalculando episodios faltantes para serie {series_id} debido a cambio de TMDB ID")
                try:
                    missing_count = self.recalculate_missing_episodes_for_series(series_id)
                    self.logger.info(f"Recalculados {missing_count} episodios faltantes")
                    # Retornar información sobre el recálculo para que la UI pueda actualizarse
                    return {
                        'recalculated_missing': True,
                        'missing_count': missing_count,
                        'reset_hispashare_url': reset_hispashare_url,
                        'recheck_hispashare_url': recheck_hispashare_url,
                        'previous_hispashare_url': previous_hispashare_url,
                    }
                except Exception as e:
                    self.logger.error(f"Error al recalcular episodios faltantes: {e}")
                    # No lanzar excepción para no afectar la actualización principal
            
            return {
                'recalculated_missing': False,
                'reset_hispashare_url': reset_hispashare_url,
                'recheck_hispashare_url': recheck_hispashare_url,
                'previous_hispashare_url': previous_hispashare_url,
            }
            
        except Exception as e:
            self.logger.error(f"Error en update_series_manual: {e}")
            conn.rollback()
            raise
    
    def get_all_series(self, categories=None):
        """Obtener todas las series"""
        conn = self.get_connection()
        cursor = conn.cursor()

        category_filter, params = self._build_category_filter(categories)
        cursor.execute(f'''
            SELECT s.*, 
                   COUNT(DISTINCT CASE WHEN me.status = 'missing' THEN me.id END) as missing_count
            FROM series s
            LEFT JOIN missing_episodes me ON s.id = me.series_id
            WHERE 1=1 {category_filter}
            GROUP BY s.id
            ORDER BY s.content_category, s.name
        ''', params)
        
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    
    def get_series_with_local_resolution_and_any_offer(self, categories=None):
        """Series con al menos un episodio local (candidatas a upgrade) que
        además tienen una oferta conocida en cualquier proveedor (source_offers
        disponible, o la URL legacy de Hispashare). Generaliza
        get_series_with_wolfmax_url para no estar hardcodeada a un proveedor."""
        conn = self.get_connection()
        cursor = conn.cursor()

        category_filter, params = self._build_category_filter(categories)
        cursor.execute(f'''
            SELECT s.id, s.name, s.tmdb_name, s.year, s.hispashare_url, s.tmdb_id, s.content_category,
                   MAX(le.resolution_height) as max_local_resolution,
                   MIN(le.resolution_height) as min_local_resolution
            FROM series s
            JOIN local_episodes le ON le.series_id = s.id
            WHERE COALESCE(s.monitored, 1) = 1
            AND (
                (s.hispashare_url IS NOT NULL AND s.hispashare_url != '')
                OR EXISTS (
                    SELECT 1 FROM source_offers so
                    WHERE so.series_id = s.id AND so.available = 1
                )
            )
            {category_filter}
            GROUP BY s.id
        ''', params)

        rows = cursor.fetchall()
        return [dict(row) for row in rows]

    def get_series_with_tmdb(self, categories=None, include_needs_review=False):
        """Obtener series con TMDB ID.

        Por defecto excluye las series marcadas needs_review=1 (match de baja
        confianza): no deben disparar recálculo de episodios faltantes ni
        búsquedas/descargas automáticas hasta que el usuario confirme el match.
        """
        conn = self.get_connection()
        cursor = conn.cursor()

        category_filter, params = self._build_series_category_filter(categories)
        review_filter = " AND COALESCE(monitored, 1) = 1"
        if not include_needs_review:
            review_filter += " AND (needs_review IS NULL OR needs_review = 0)"
        cursor.execute(f'''
            SELECT * FROM series
            WHERE tmdb_id IS NOT NULL{review_filter}{category_filter.replace(' WHERE', ' AND')}
            ORDER BY content_category, name
        ''', params)

        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    
    def get_series_by_id(self, series_id):
        """Obtener una serie por su ID incluyendo el número real de episodios faltantes"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT s.*,
                   COUNT(DISTINCT CASE WHEN me.status = 'missing' THEN me.id END) as missing_count
            FROM series s
            LEFT JOIN missing_episodes me ON s.id = me.series_id
            WHERE s.id = ?
            GROUP BY s.id
        ''', (series_id,))
        
        row = cursor.fetchone()
        return dict(row) if row else None

    def delete_series_by_paths(self, paths):
        """Borra las fichas de archivo único (películas, documentales...) de
        estas rutas, con sus datos asociados. Devuelve cuántas. Nunca toca
        series episódicas."""
        wanted = {self.normalize_path(p).lower() for p in paths if p}
        if not wanted:
            return 0
        single_file = Settings.get_single_file_categories()
        conn = self.get_connection()
        rows = conn.execute(
            f"SELECT id, folder_path FROM series WHERE content_category IN ({','.join('?' * len(single_file))})",
            single_file,
        ).fetchall()
        ids = [r[0] for r in rows if (r[1] or "").lower() in wanted or self.normalize_path(r[1] or "").lower() in wanted]
        for series_id in ids:
            for table in ("local_episodes", "missing_episodes", "source_matches", "source_offers"):
                try:
                    conn.execute(f"DELETE FROM {table} WHERE series_id = ?", (series_id,))
                except sqlite3.OperationalError:
                    pass  # tabla aún no creada en esta BD
            conn.execute("DELETE FROM series WHERE id = ?", (series_id,))
        conn.commit()
        return len(ids)

    def rename_series_folder(self, series_id, new_name):
        """Renombra la carpeta de una serie en disco y actualiza la BD.

        Returns dict with keys: success (bool), renamed (bool), error (str),
        old_name, new_name, old_folder_path, new_folder_path.
        """
        import re

        series = self.get_series_by_id(series_id)
        if not series:
            return {"success": False, "renamed": False, "error": "Serie no encontrada"}

        old_folder_path = Path(series['folder_path'])
        old_name = series['name']

        if not old_folder_path.exists():
            return {"success": False, "renamed": False, "error": f"Carpeta no existe: {old_folder_path}"}

        # Quitar caracteres no válidos en sistemas de ficheros Windows/Linux
        safe_name = re.sub(r'[<>:"/\\|?*]', '', new_name).strip().rstrip('.')
        if not safe_name:
            return {"success": False, "renamed": False, "error": "El nombre resultante está vacío tras sanitizar"}

        # Para ficheros únicos (.mp4, .mkv, …) preservar la extensión original
        if old_folder_path.is_file():
            safe_name = safe_name + old_folder_path.suffix

        if safe_name == old_name:
            return {"success": True, "renamed": False, "reason": "El nombre ya coincide"}

        new_folder_path = old_folder_path.parent / safe_name

        # En Windows y macOS los nombres no distinguen mayúsculas: el destino
        # "existe" si solo cambian mayúsculas, pero es la misma carpeta.
        if new_folder_path.exists() and not os.path.samefile(new_folder_path, old_folder_path):
            return {"success": False, "renamed": False, "error": f"Ya existe una carpeta con ese nombre: {new_folder_path}"}

        try:
            old_folder_path.rename(new_folder_path)
        except OSError as exc:
            self.logger.error(f"Error al renombrar carpeta '{old_folder_path}' -> '{new_folder_path}': {exc}")
            return {"success": False, "renamed": False, "error": str(exc)}

        self.logger.info(f"Carpeta renombrada: '{old_folder_path}' -> '{new_folder_path}'")

        # Actualizar BD
        conn = self.get_connection()
        cursor = conn.cursor()
        old_folder_str = str(old_folder_path)
        new_folder_str = str(new_folder_path)

        try:
            cursor.execute(
                "UPDATE series SET name = ?, folder_path = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (safe_name, new_folder_str, series_id),
            )

            # Actualizar rutas de episodios locales
            cursor.execute(
                "SELECT id, file_path FROM local_episodes WHERE series_id = ?",
                (series_id,),
            )
            for ep in cursor.fetchall():
                old_ep = ep['file_path']
                if old_ep.startswith(old_folder_str):
                    new_ep = new_folder_str + old_ep[len(old_folder_str):]
                    cursor.execute(
                        "UPDATE local_episodes SET file_path = ? WHERE id = ?",
                        (new_ep, ep['id']),
                    )

            conn.commit()
        except Exception as exc:
            self.logger.error(f"Error al actualizar BD tras renombrar carpeta: {exc}")
            conn.rollback()
            return {"success": False, "renamed": False, "error": str(exc)}

        return {
            "success": True,
            "renamed": True,
            "old_name": old_name,
            "new_name": safe_name,
            "old_folder_path": old_folder_str,
            "new_folder_path": new_folder_str,
        }

    def get_series_without_hispashare_url(self, categories=None):
        """Obtener series sin URL de Hispashare y que no tengan búsqueda deshabilitada"""
        conn = self.get_connection()
        cursor = conn.cursor()

        category_filter, params = self._build_series_category_filter(categories)
        cursor.execute(f'''
            SELECT id, name, tmdb_name, year, tmdb_id, content_category
            FROM series 
            WHERE (hispashare_url IS NULL OR hispashare_url = '') 
            AND (search_disabled IS NULL OR search_disabled = 0)
            AND COALESCE(monitored, 1) = 1
            {category_filter.replace(' WHERE', ' AND')}
            ORDER BY content_category, name
        ''', params)
        
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    
    def get_series_with_missing_episodes_without_url(self, categories=None):
        """Obtener series que tienen episodios faltantes y no tienen URL de Hispashare"""
        conn = self.get_connection()
        cursor = conn.cursor()

        category_filter, params = self._build_category_filter(categories)
        cursor.execute(f'''
            SELECT DISTINCT s.id, s.name, s.tmdb_name, s.year, s.tmdb_id
            FROM series s
            INNER JOIN missing_episodes me ON s.id = me.series_id
            WHERE (s.hispashare_url IS NULL OR s.hispashare_url = '') 
            AND (s.search_disabled IS NULL OR s.search_disabled = 0)
            AND COALESCE(s.monitored, 1) = 1
            AND me.status = 'missing'
            {category_filter}
            ORDER BY s.content_category, s.name
        ''', params)
        
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    
    def get_series_with_missing_episodes_and_any_offer(self, categories=None):
        """Como get_series_with_missing_episodes_and_url, pero también incluye
        series que solo tienen oferta en otros proveedores (p. ej. Wolfmax)
        sin URL de Hispashare -- get_series_with_missing_episodes_and_url las
        omitía silenciosamente porque exigía s.hispashare_url."""
        conn = self.get_connection()
        cursor = conn.cursor()

        category_filter, params = self._build_category_filter(categories)
        cursor.execute(f'''
            SELECT DISTINCT s.id, s.name, s.folder_path, s.hispashare_url, s.tmdb_id,
                   s.tmdb_name, s.year, s.content_category
            FROM series s
            INNER JOIN missing_episodes me ON s.id = me.series_id
            WHERE me.status = 'missing'
            AND COALESCE(s.monitored, 1) = 1
            AND (
                (s.hispashare_url IS NOT NULL AND s.hispashare_url != '')
                OR EXISTS (
                    SELECT 1 FROM source_offers so
                    WHERE so.series_id = s.id AND so.available = 1
                )
            )
            {category_filter}
            ORDER BY s.content_category, s.name
        ''', params)

        rows = cursor.fetchall()
        return [dict(row) for row in rows]

    def get_unmonitored_folder_paths(self):
        """Rutas (resolve().lower()) de las carpetas de series no monitorizadas."""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT folder_path FROM series WHERE COALESCE(monitored, 1) = 0")
        paths = set()
        for row in cursor.fetchall():
            try:
                paths.add(str(Path(row['folder_path']).resolve()).lower())
            except Exception:
                paths.add(str(row['folder_path']).lower())
        return paths

    def set_series_monitored(self, series_id, monitored: bool):
        """Marcar/desmarcar una serie como monitorizada. Las no monitorizadas
        se excluyen de búsquedas, recálculo de faltantes y upgrades."""
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                "UPDATE series SET monitored = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (1 if monitored else 0, series_id),
            )
            conn.commit()
            self.logger.info(
                f"Serie ID {series_id} {'monitorizada' if monitored else 'marcada como no monitorizada'}"
            )
        except Exception as e:
            self.logger.error(f"Error cambiando monitorización: {e}")
            conn.rollback()
            raise

    def disable_series_search(self, series_id, manual=False):
        """Marcar una serie para que no se busque más automáticamente.
        manual=True: lo pidió el usuario y nunca se reintenta solo."""
        conn = self.get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute('''
                UPDATE series
                SET search_disabled = 1, search_disabled_at = CURRENT_TIMESTAMP,
                    search_disabled_manual = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (1 if manual else 0, series_id))
            conn.commit()
            self.logger.info(f"Búsqueda deshabilitada para serie ID: {series_id}")
            
        except Exception as e:
            self.logger.error(f"Error al deshabilitar búsqueda: {e}")
            conn.rollback()
            raise

    def register_search_failure(self, series_id, disable_after=3):
        """Registrar fallo de búsqueda y deshabilitar solo tras varios intentos."""
        conn = self.get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute(
                '''
                UPDATE series
                SET search_failures = COALESCE(search_failures, 0) + 1,
                    search_disabled = CASE
                        WHEN COALESCE(search_failures, 0) + 1 >= ? THEN 1
                        ELSE 0
                    END,
                    search_disabled_at = CASE
                        WHEN COALESCE(search_failures, 0) + 1 >= ? THEN CURRENT_TIMESTAMP
                        ELSE search_disabled_at
                    END,
                    search_disabled_manual = 0,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                ''',
                (disable_after, disable_after, series_id),
            )
            conn.commit()

            cursor.execute(
                '''
                SELECT COALESCE(search_failures, 0) AS search_failures,
                       COALESCE(search_disabled, 0) AS search_disabled
                FROM series
                WHERE id = ?
                ''',
                (series_id,),
            )
            row = cursor.fetchone()
            return dict(row) if row else {"search_failures": 0, "search_disabled": 0}
        except Exception as e:
            self.logger.error(f"Error registrando fallo de búsqueda: {e}")
            conn.rollback()
            raise
    
    def enable_series_search(self, series_id):
        """Reactivar la búsqueda automática para una serie"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute('''
                UPDATE series
                SET search_disabled = 0,
                    search_failures = 0,
                    search_disabled_at = NULL,
                    search_disabled_manual = 0,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (series_id,))
            conn.commit()
            self.logger.info(f"Búsqueda reactivada para serie ID: {series_id}")

        except Exception as e:
            self.logger.error(f"Error al reactivar búsqueda: {e}")
            conn.rollback()
            raise

    def retry_expired_searches(self, days=None, limit=None) -> list[dict]:
        """Reactiva la búsqueda de las fichas que se desactivaron solas hace
        más de `days` días (las más antiguas primero, como mucho `limit`).
        Quedan con un fallo de margen: si tampoco se encuentran, se vuelven
        a desactivar en ese mismo intento y esperan otro periodo."""
        days = self.SEARCH_RETRY_DAYS if days is None else days
        limit = self.SEARCH_RETRY_PER_CYCLE if limit is None else limit
        conn = self.get_connection()
        rows = conn.execute(
            '''
            SELECT id, COALESCE(NULLIF(tmdb_name, ''), name) AS name FROM series
            WHERE COALESCE(search_disabled, 0) = 1
              AND COALESCE(search_disabled_manual, 0) = 0
              AND COALESCE(monitored, 1) = 1
              AND (hispashare_url IS NULL OR hispashare_url = '')
              AND COALESCE(search_disabled_at, updated_at) <= datetime('now', ?)
            ORDER BY COALESCE(search_disabled_at, updated_at)
            LIMIT ?
            ''',
            (f"-{int(days)} days", int(limit)),
        ).fetchall()
        if rows:
            conn.executemany(
                "UPDATE series SET search_disabled = 0, search_failures = 2, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                [(row[0],) for row in rows],
            )
            conn.commit()
        return [dict(row) for row in rows]

    def cleanup_deleted_series(self):
        """Limpiar series cuyas carpetas ya no existen en el disco o no están en rutas configuradas"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        try:
            # Obtener todas las series con sus rutas. Las fichas "en camino"
            # (novedades aprobadas, placeholder=1) aún no tienen carpeta: no
            # se tocan aquí, las resuelve DiscoveryService.reconcile_placeholders.
            ensure_placeholder_column(self)
            cursor.execute('SELECT id, name, folder_path, content_category FROM series WHERE COALESCE(placeholder, 0) = 0')
            all_series = cursor.fetchall()
            
            to_delete = []
            skipped_unreachable = 0
            root_exists_cache = {}

            def _exists(path):
                # Con el NAS inaccesible, exists() sobre una ruta de red puede
                # lanzar OSError (WinError 64...) en vez de devolver False.
                try:
                    return path.exists()
                except OSError:
                    return None

            def _root_exists(path):
                if path not in root_exists_cache:
                    root_exists_cache[path] = bool(_exists(path))
                return root_exists_cache[path]

            for series in all_series:
                series_id, series_name, folder_path, content_category = series
                series_path = Path(folder_path)
                configured_paths = Settings.get_paths_for_category(content_category or 'series')
                configured_paths = [Path(path).resolve() for path in configured_paths if path.strip()]

                should_delete = False
                delete_reason = ""

                exists = _exists(series_path)
                if exists is None:
                    # No se puede comprobar: nunca se borra por ello.
                    skipped_unreachable += 1
                    continue
                # Verificar si la carpeta existe
                if not exists:
                    # Solo es una baja real si su raíz configurada es accesible:
                    # con el NAS caído, o con rutas de otro sistema (BD creada
                    # en Windows usada desde Docker), "no existe" no significa
                    # que se haya borrado, y eliminar aquí vaciaría la biblioteca.
                    try:
                        resolved_missing = series_path.resolve()
                    except Exception:
                        resolved_missing = series_path
                    under_live_root = False
                    for configured_path in configured_paths:
                        try:
                            resolved_missing.relative_to(configured_path)
                        except ValueError:
                            continue
                        if _root_exists(configured_path):
                            under_live_root = True
                            break
                    if under_live_root:
                        should_delete = True
                        delete_reason = "carpeta no existe"
                    else:
                        skipped_unreachable += 1
                else:
                    # Verificar si la serie está dentro de alguna ruta configurada
                    is_in_configured_path = False
                    try:
                        resolved_series_path = series_path.resolve()
                        for configured_path in configured_paths:
                            try:
                                # Verificar si la serie está dentro de esta ruta configurada
                                resolved_series_path.relative_to(configured_path)
                                is_in_configured_path = True
                                break
                            except ValueError:
                                # La serie no está en esta ruta configurada
                                continue
                    except Exception:
                        # Error al resolver rutas, mantener la serie por seguridad
                        is_in_configured_path = True
                    
                    if not is_in_configured_path:
                        should_delete = True
                        delete_reason = "no está en rutas configuradas"
                
                if should_delete:
                    to_delete.append((series_id, series_name, folder_path, delete_reason))

            if skipped_unreachable:
                self.logger.warning(
                    f"Limpieza: {skipped_unreachable} series con carpeta no accesible y fuera de "
                    f"una ruta configurada accesible; se conservan"
                )

            max_deletions = max(self.CLEANUP_MAX_DELETIONS, int(len(all_series) * self.CLEANUP_MAX_DELETION_RATIO))
            if len(to_delete) > max_deletions:
                self.logger.error(
                    f"Limpieza cancelada: se iban a eliminar {len(to_delete)} de {len(all_series)} series "
                    f"(límite {max_deletions}). Revisa que las rutas de biblioteca sean accesibles."
                )
                return 0

            deleted_count = 0
            for series_id, series_name, folder_path, delete_reason in to_delete:
                self.logger.info(f"Eliminando serie '{series_name}' - {delete_reason}: {folder_path}")

                # Eliminar episodios locales
                cursor.execute('DELETE FROM local_episodes WHERE series_id = ?', (series_id,))

                # Eliminar episodios faltantes
                cursor.execute('DELETE FROM missing_episodes WHERE series_id = ?', (series_id,))

                # Eliminar la serie
                cursor.execute('DELETE FROM series WHERE id = ?', (series_id,))

                deleted_count += 1

            conn.commit()
            self.logger.info(f"Limpieza completada: {deleted_count} series eliminadas")
            return deleted_count
            
        except Exception as e:
            self.logger.error(f"Error en cleanup_deleted_series: {e}")
            conn.rollback()
            raise
    
    def insert_local_episodes(self, series_id, episodes):
        """Insertar episodios locales"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        try:
            # La calidad medida con MediaInfo (scan_missing_resolutions) no se
            # puede deducir del nombre del archivo: se recuerda antes de borrar
            # para no perderla en cada escaneo. Antes se perdía siempre, y solo
            # se conocía la calidad de los archivos que la llevan en el nombre.
            measured = {
                row['file_path'].lower(): dict(row)
                for row in cursor.execute(
                    'SELECT file_path, size_mb, resolution_height, codec, hdr, quality_rank, '
                    'quality_source, measured_at '
                    'FROM local_episodes WHERE series_id = ?',
                    (series_id,),
                ).fetchall()
            }

            # Primero limpiar episodios existentes para esta serie
            cursor.execute('DELETE FROM local_episodes WHERE series_id = ?', (series_id,))
            
            # Crear un set para evitar duplicados en el mismo lote
            unique_episodes = {}
            
            for episode in episodes:
                # Crear clave única basada en serie, temporada y episodio
                episode_key = (series_id, episode['season'], episode['episode'])
                
                # Si ya existe este episodio, mantener el que tenga ruta más específica
                if episode_key in unique_episodes:
                    existing_path = unique_episodes[episode_key]['file_path']
                    new_path = episode['file_path']
                    
                    # Preferir rutas más específicas (más profundas en la estructura)
                    if len(Path(new_path).parts) > len(Path(existing_path).parts):
                        unique_episodes[episode_key] = episode
                else:
                    unique_episodes[episode_key] = episode
            
            # Insertar episodios únicos
            for episode in unique_episodes.values():
                episode = _keep_measured_quality(episode, measured.get(str(episode['file_path']).lower()))
                cursor.execute('''
                    INSERT INTO local_episodes
                    (series_id, season, episode, filename, file_path, resolution_height, codec, source, size_mb,
                     quality_rank, hdr, quality_source, measured_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    series_id,
                    episode['season'],
                    episode['episode'],
                    episode['filename'],
                    episode['file_path'],
                    episode.get('resolution_height'),
                    episode.get('codec'),
                    episode.get('source'),
                    episode.get('size_mb'),
                    episode.get('quality_rank', 0),
                    episode.get('hdr'),
                    episode.get('quality_source') or ('filename' if episode.get('resolution_height') else None),
                    episode.get('measured_at'),
                ))
            
            conn.commit()
            
        except Exception as e:
            self.logger.error(f"Error en insert_local_episodes: {e}")
            conn.rollback()
            raise
    
    def cleanup_duplicate_series(self):
        """Limpiar series duplicadas (mismo nombre/path insensible a mayúsculas)"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        try:
            # Encontrar duplicados por folder_path (insensible a mayúsculas)
            cursor.execute('''
                SELECT lower(folder_path), COUNT(*) 
                FROM series 
                GROUP BY lower(folder_path) 
                HAVING COUNT(*) > 1
            ''')
            
            duplicates = cursor.fetchall()
            total_cleaned = 0
            
            for path_lower, _ in duplicates:
                cursor.execute('SELECT * FROM series WHERE lower(folder_path) = ? ORDER BY id', (path_lower,))
                rows = cursor.fetchall()
                series_list = [dict(row) for row in rows]
                
                if not series_list:
                    continue
                
                # Ordenar para encontrar la mejor para mantener
                # Criterios: 1. Tiene TMDB ID, 2. Tiene URL Hispashare, 3. ID más bajo (más antiguo)
                series_list.sort(key=lambda s: (
                    s['tmdb_id'] is not None, 
                    s['hispashare_url'] is not None and s['hispashare_url'] != '',
                    -s['id'] # Preferir IDs más antiguos (negativo porque sort es reverse=True)
                ), reverse=True)
                
                keep = series_list[0]
                to_delete = series_list[1:]
                
                self.logger.info(f"Consolidando duplicados para '{keep['name']}': Manteniendo ID {keep['id']}, eliminando {len(to_delete)} otros")
                
                for s in to_delete:
                    # Mover episodios locales a la serie mantenida
                    cursor.execute('UPDATE OR IGNORE local_episodes SET series_id = ? WHERE series_id = ?', (keep['id'], s['id']))
                    # Eliminar los que no se pudieron mover (duplicados)
                    cursor.execute('DELETE FROM local_episodes WHERE series_id = ?', (s['id'],))
                    
                    # Mover episodios faltantes
                    cursor.execute('UPDATE OR IGNORE missing_episodes SET series_id = ? WHERE series_id = ?', (keep['id'], s['id']))
                    cursor.execute('DELETE FROM missing_episodes WHERE series_id = ?', (s['id'],))
                    
                    # Eliminar la serie duplicada
                    cursor.execute('DELETE FROM series WHERE id = ?', (s['id'],))
                    total_cleaned += 1
            
            conn.commit()
            if total_cleaned > 0:
                self.logger.info(f"Limpieza de series duplicadas completada: {total_cleaned} series eliminadas")
            return total_cleaned
            
        except Exception as e:
            self.logger.error(f"Error en cleanup_duplicate_series: {e}")
            conn.rollback()
            return 0

    def cleanup_duplicate_episodes(self):
        """Limpiar episodios duplicados existentes en la base de datos"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        try:
            # Encontrar duplicados (mismo series_id, season, episode)
            cursor.execute('''
                SELECT series_id, season, episode, COUNT(*) as count
                FROM local_episodes
                GROUP BY series_id, season, episode
                HAVING COUNT(*) > 1
            ''')
            
            duplicates = cursor.fetchall()
            cleaned_count = 0
            
            for duplicate in duplicates:
                series_id, season, episode, count = duplicate
                
                # Obtener todos los registros duplicados
                cursor.execute('''
                    SELECT id, file_path FROM local_episodes
                    WHERE series_id = ? AND season = ? AND episode = ?
                    ORDER BY LENGTH(file_path) DESC
                ''', (series_id, season, episode))
                
                duplicate_records = cursor.fetchall()
                
                # Mantener solo el primero (ruta más larga/específica)
                if len(duplicate_records) > 1:
                    ids_to_delete = [record[0] for record in duplicate_records[1:]]
                    
                    for record_id in ids_to_delete:
                        cursor.execute('DELETE FROM local_episodes WHERE id = ?', (record_id,))
                        cleaned_count += 1
            
            conn.commit()
            self.logger.info(f"Limpieza de duplicados completada: {cleaned_count} episodios duplicados eliminados")
            return cleaned_count
            
        except Exception as e:
            self.logger.error(f"Error en cleanup_duplicate_episodes: {e}")
            conn.rollback()
            raise
    
    def get_local_episodes(self, series_id):
        """Obtener episodios locales de una serie"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT * FROM local_episodes
            WHERE series_id = ?
            ORDER BY season, episode
        ''', (series_id,))
        
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

    def get_local_episodes_for_tmdb_id(self, tmdb_id):
        """Obtener episodios locales de todas las carpetas que apunten al mismo TMDB ID"""
        if not tmdb_id:
            return []
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            '''
            SELECT le.*
            FROM local_episodes le
            JOIN series s ON s.id = le.series_id
            WHERE s.tmdb_id = ?
            ORDER BY le.season, le.episode
            ''',
            (tmdb_id,),
        )
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

    def filter_new_ed2k_links(self, links):
        if not links:
            return []
        conn = self.get_connection()
        cursor = conn.cursor()
        unique = []
        seen = set()
        for link in links:
            if not link or link in seen:
                continue
            seen.add(link)
            unique.append(link)

        placeholders = ",".join(["?"] * len(unique))
        cursor.execute(f"SELECT link FROM ed2k_history WHERE link IN ({placeholders})", unique)
        existing = {row["link"] for row in cursor.fetchall()}
        return [link for link in unique if link not in existing]

    def get_stale_ed2k_links(self, links, retry_after_days):
        """Enlaces de `links` ya presentes en ed2k_history cuyo último envío
        supera `retry_after_days`. El llamador solo debe pasar enlaces que
        correspondan a episodios que siguen faltando localmente, así que un
        enlace "viejo" aquí implica que el envío anterior nunca se tradujo
        en una descarga completada (fuentes muertas, cola atascada, etc.)."""
        if not links or not retry_after_days or retry_after_days <= 0:
            return []
        conn = self.get_connection()
        cursor = conn.cursor()
        unique = list(dict.fromkeys(link for link in links if link))
        if not unique:
            return []

        placeholders = ",".join(["?"] * len(unique))
        cursor.execute(
            f"""
            SELECT link FROM ed2k_history
            WHERE link IN ({placeholders})
              AND julianday('now') - julianday(COALESCE(last_sent_at, created_at)) >= ?
            """,
            unique + [retry_after_days],
        )
        return [row["link"] for row in cursor.fetchall()]

    def mark_ed2k_links_sent(self, links, kind=None, series_id=None):
        if not links:
            return 0
        conn = self.get_connection()
        cursor = conn.cursor()
        processed = 0
        try:
            for link in links:
                if not link:
                    continue
                cursor.execute(
                    """
                    INSERT INTO ed2k_history(link, kind, series_id, last_sent_at, retry_count)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP, 0)
                    ON CONFLICT(link) DO UPDATE SET
                        last_sent_at = CURRENT_TIMESTAMP,
                        retry_count = retry_count + 1
                    """,
                    (link, kind, series_id),
                )
                processed += 1
            conn.commit()
            return processed
        except Exception as e:
            self.logger.error(f"Error en mark_ed2k_links_sent: {e}")
            conn.rollback()
            raise

    def get_upgrade_candidates_for_series(self, series_id, max_quality_rank):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            '''
            SELECT season, episode, quality_rank, filename, file_path
            FROM local_episodes
            WHERE series_id = ?
              AND COALESCE(quality_rank, 0) > 0
              AND COALESCE(quality_rank, 0) < ?
            ORDER BY season, episode
            ''',
            (series_id, max_quality_rank),
        )
        rows = cursor.fetchall()
        return [dict(r) for r in rows]

    UNREADABLE_RETRY_DAYS = 7

    def get_episodes_missing_resolution(self, limit=300):
        """
        Episodios cuya calidad aún no se ha leído del propio archivo con
        MediaInfo (lo que diga el nombre no cuenta). Los que no se pudieron
        leer (quality_rank = -1: archivo inaccesible, NAS apagado...) se
        reintentan pasados UNREADABLE_RETRY_DAYS días.
        """
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute(
            '''
            SELECT id, file_path, filename
            FROM local_episodes
            WHERE COALESCE(quality_source, '') != 'mediainfo'
              AND (COALESCE(quality_rank, 0) >= 0
                   OR measured_at IS NULL
                   OR measured_at < datetime('now', ?))
            ORDER BY COALESCE(quality_rank, 0) = -1, id
            LIMIT ?
            ''',
            (f"-{self.UNREADABLE_RETRY_DAYS} days", limit),
        )
        return [dict(r) for r in cur.fetchall()]

    def count_episode_quality(self) -> dict:
        row = self.get_connection().execute(
            '''
            SELECT COUNT(*),
                   SUM(quality_source = 'mediainfo'),
                   SUM(COALESCE(quality_source, '') != 'mediainfo' AND quality_rank = -1)
            FROM local_episodes
            '''
        ).fetchone()
        return {"total": row[0] or 0, "measured": row[1] or 0, "unreadable": row[2] or 0}

    def mark_episode_resolution_unreadable(self, episode_ids: list[int]):
        """Marca episodios cuyo archivo no pudo leerse para no reintentar cada ciclo."""
        if not episode_ids:
            return
        conn = self.get_connection()
        try:
            placeholders = ','.join(['?'] * len(episode_ids))
            conn.execute(
                f'UPDATE local_episodes SET quality_rank = -1, measured_at = CURRENT_TIMESTAMP '
                f'WHERE id IN ({placeholders})',
                episode_ids,
            )
            conn.commit()
        except Exception as e:
            self.logger.error(f"Error en mark_episode_resolution_unreadable: {e}")
            conn.rollback()

    def batch_update_episode_resolutions(self, updates: list[dict]) -> int:
        """
        Actualiza resolución de varios episodios a la vez.
        Cada elemento de updates: {id, resolution_height, codec, quality_rank, hdr}
        """
        if not updates:
            return 0
        conn = self.get_connection()
        updated = 0
        try:
            for item in updates:
                conn.execute(
                    '''
                    UPDATE local_episodes
                    SET resolution_height = ?,
                        codec = ?,
                        quality_rank = ?,
                        hdr = ?,
                        quality_source = 'mediainfo',
                        measured_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    ''',
                    (
                        # Lo leído del archivo manda sobre lo deducido del nombre.
                        item['resolution_height'],
                        item.get('codec'),
                        item.get('quality_rank', 0),
                        item.get('hdr') or 'sdr',
                        item['id'],
                    ),
                )
                updated += 1
            conn.commit()
            return updated
        except Exception as e:
            self.logger.error(f"Error en batch_update_episode_resolutions: {e}")
            conn.rollback()
            raise

    def get_wolfmax_source_for_series(self, series_id: int) -> dict | None:
        """Devuelve la entrada de source_matches con provider='wolfmax' para una serie."""
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute(
            '''
            SELECT source_url, source_title, match_score
            FROM source_matches
            WHERE series_id = ? AND provider = 'wolfmax'
            ORDER BY created_at DESC
            LIMIT 1
            ''',
            (series_id,),
        )
        row = cur.fetchone()
        return dict(row) if row else None

    def save_wolfmax_source(self, series_id: int, wolfmax_url: str,
                            quality_height: int | None, title: str = '') -> None:
        """Guarda o actualiza la URL wolfmax de una serie en source_matches."""
        quality_label = f'{quality_height}p' if quality_height else 'unknown'
        score = (quality_height or 0) / 2160
        conn = self.get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                'SELECT id FROM source_matches WHERE series_id = ? AND provider = ?',
                (series_id, 'wolfmax'),
            )
            row = cur.fetchone()
            if row:
                conn.execute(
                    '''
                    UPDATE source_matches
                    SET source_url = ?, source_title = ?, match_score = ?,
                        match_status = 'confirmed', updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    ''',
                    (wolfmax_url, title or quality_label, score, row['id']),
                )
            else:
                conn.execute(
                    '''
                    INSERT INTO source_matches
                        (series_id, provider, source_url, source_title, match_score,
                         match_status, validation_reason)
                    VALUES (?, 'wolfmax', ?, ?, ?, 'confirmed', 'wolfmax category scan')
                    ''',
                    (series_id, wolfmax_url, title or quality_label, score),
                )
            conn.commit()
        except Exception as e:
            self.logger.error(f'Error en save_wolfmax_source: {e}')
            conn.rollback()
            raise

        # Dual-write hacia source_offers: misma información pero en columnas
        # tipadas (resolution_height real, no el hack match_score=height/2160
        # de arriba), para que SourceOfferRepository/SourceArbitrationService
        # puedan comparar la calidad de Wolfmax frente a otros proveedores.
        try:
            self.upsert_source_offer(
                series_id, 'wolfmax', wolfmax_url,
                resolution_height=quality_height,
                match_score=score,
            )
        except Exception as e:
            self.logger.warning(f'No se pudo actualizar source_offers para wolfmax: {e}')

    def upsert_source_offer(
        self,
        series_id: int,
        provider: str,
        source_url: str,
        *,
        resolution_height: int | None = None,
        codec: str | None = None,
        hdr: str = 'unknown',
        match_score: float = 0,
    ) -> None:
        """Guarda/actualiza la oferta de un proveedor para una serie en
        source_offers (upsert real vía ON CONFLICT, a diferencia del
        check-then-update manual de source_matches)."""
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute(
            '''
            INSERT INTO source_offers(
                series_id, provider, source_url, resolution_height, codec, hdr,
                match_score, available, last_checked_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT(series_id, provider) DO UPDATE SET
                source_url = excluded.source_url,
                resolution_height = excluded.resolution_height,
                codec = excluded.codec,
                hdr = excluded.hdr,
                match_score = excluded.match_score,
                available = 1,
                last_checked_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            ''',
            (series_id, provider, source_url, resolution_height, codec, hdr, match_score),
        )
        conn.commit()

    def get_series_with_wolfmax_url(self, categories=None) -> list[dict]:
        """Series que tienen URL wolfmax guardada, con su resolución local máxima."""
        conn = self.get_connection()
        cur = conn.cursor()
        cat_filter, cat_params = self._build_series_category_filter(categories)
        cur.execute(
            f'''
            SELECT s.id, s.name, s.hispashare_url, s.content_category,
                   sm.source_url as wolfmax_url,
                   sm.match_score as wolfmax_score,
                   MAX(le.resolution_height) as max_local_resolution
            FROM series s
            JOIN source_matches sm ON sm.series_id = s.id AND sm.provider = 'wolfmax'
            LEFT JOIN local_episodes le ON le.series_id = s.id
            WHERE COALESCE(s.monitored, 1) = 1
            {cat_filter.replace(' WHERE', ' AND')}
            GROUP BY s.id
            ''',
            cat_params,
        )
        return [dict(r) for r in cur.fetchall()]

    def filter_new_wolfmax_urls(self, urls):
        """Devuelve solo las URLs de wolfmax que no han sido descargadas ya."""
        if not urls:
            return []
        conn = self.get_connection()
        cursor = conn.cursor()
        unique = list(dict.fromkeys(u for u in urls if u))
        placeholders = ",".join(["?"] * len(unique))
        cursor.execute(
            f"SELECT url FROM wolfmax_history WHERE url IN ({placeholders})", unique
        )
        existing = {row["url"] for row in cursor.fetchall()}
        return [u for u in unique if u not in existing]

    def mark_wolfmax_urls_done(self, urls, series_id=None, status='downloaded'):
        """Registra URLs wolfmax como procesadas para evitar duplicados."""
        if not urls:
            return 0
        conn = self.get_connection()
        cursor = conn.cursor()
        inserted = 0
        try:
            for url in urls:
                if not url:
                    continue
                cursor.execute(
                    "INSERT OR IGNORE INTO wolfmax_history(url, series_id, status) VALUES (?, ?, ?)",
                    (url, series_id, status),
                )
                if cursor.rowcount:
                    inserted += 1
            conn.commit()
            return inserted
        except Exception as e:
            self.logger.error(f"Error en mark_wolfmax_urls_done: {e}")
            conn.rollback()
            raise

    # ========== EPISODIOS FALTANTES ==========
    
    def insert_missing_episode(self, episode_data):
        """Insertar episodio faltante"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute('''
                INSERT OR REPLACE INTO missing_episodes
                (series_id, series_name, season, episode, name, air_date, status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                episode_data['series_id'],
                episode_data['series_name'],
                episode_data['season'],
                episode_data['episode'],
                episode_data['name'],
                episode_data['air_date'],
                'missing'
            ))
            
            conn.commit()
            
        except Exception as e:
            self.logger.error(f"Error en insert_missing_episode: {e}")
            conn.rollback()
            raise

    def insert_missing_episodes_bulk(self, episodes, conn=None, cursor=None):
        """Insertar varios episodios faltantes en una sola operación."""
        if not episodes:
            return 0

        owns_connection = conn is None or cursor is None
        if owns_connection:
            conn = self.get_connection()
            cursor = conn.cursor()

        try:
            cursor.executemany(
                '''
                INSERT OR REPLACE INTO missing_episodes
                (series_id, series_name, season, episode, name, air_date, status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ''',
                [
                    (
                        episode['series_id'],
                        episode['series_name'],
                        episode['season'],
                        episode['episode'],
                        episode['name'],
                        episode['air_date'],
                        'missing',
                    )
                    for episode in episodes
                ],
            )
            if owns_connection:
                conn.commit()
            return len(episodes)
        except Exception as e:
            self.logger.error(f"Error en insert_missing_episodes_bulk: {e}")
            if owns_connection:
                conn.rollback()
            raise
    
    def get_missing_episodes(self):
        """Obtener todos los episodios faltantes"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT * FROM missing_episodes
            WHERE status = 'missing'
            ORDER BY series_name, season, episode
        ''')
        
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

    def get_missing_episodes_for_series(self, series_id):
        """Obtener episodios faltantes de una serie"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT * FROM missing_episodes
            WHERE series_id = ? AND status = 'missing'
            ORDER BY season, episode
        ''', (series_id,))
        
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    
    def clear_missing_episodes(self, series_id):
        """Limpiar episodios faltantes de una serie"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute('''
                DELETE FROM missing_episodes
                WHERE series_id = ?
            ''', (series_id,))
            
            conn.commit()
            
        except Exception as e:
            self.logger.error(f"Error en clear_missing_episodes: {e}")
            conn.rollback()
            raise
    
    def recalculate_missing_episodes_for_series(self, series_id):
        """Recalcular episodios faltantes para una serie específica"""
        from core.tmdb.api_client import TMDBClient
        from config.settings import Settings
        
        conn = self.get_connection()
        cursor = conn.cursor()
        
        try:
            # Obtener información de la serie
            cursor.execute('''
                SELECT * FROM series
                WHERE id = ? AND tmdb_id IS NOT NULL
            ''', (series_id,))
            
            series_row = cursor.fetchone()
            if not series_row:
                self.logger.warning(f"Serie {series_id} no encontrada o sin TMDB ID")
                return 0
            
            series = dict(series_row)
            
            # Limpiar episodios faltantes existentes en la misma transaccion
            cursor.execute(
                '''
                DELETE FROM missing_episodes
                WHERE series_id = ?
                ''',
                (series_id,),
            )
            
            # Obtener episodios de TMDB (solo los ya emitidos)
            tmdb_client = TMDBClient(Settings.TMDB_API_KEY)
            tmdb_episodes = tmdb_client.get_all_episodes(series['tmdb_id'], only_aired=True)
            
            # Obtener episodios locales
            local_episodes = self.get_local_episodes_for_tmdb_id(series['tmdb_id'])
            
            # Crear set de episodios locales
            local_set = set()
            for ep in local_episodes:
                local_set.add((ep['season'], ep['episode']))
            
            # Encontrar faltantes
            missing_episodes = []
            for ep in tmdb_episodes:
                key = (ep['season_number'], ep['episode_number'])
                if key not in local_set:
                    missing_episodes.append({
                        'series_id': series_id,
                        'series_name': series['name'],
                        'season': ep['season_number'],
                        'episode': ep['episode_number'],
                        'name': ep['name'],
                        'air_date': ep.get('air_date', '')
                    })

            missing_count = self.insert_missing_episodes_bulk(
                missing_episodes,
                conn=conn,
                cursor=cursor,
            )
            conn.commit()

            self.logger.info(f"Recalculados {missing_count} episodios faltantes para serie {series['name']}")
            return missing_count
            
        except Exception as e:
            self.logger.error(f"Error en recalculate_missing_episodes_for_series: {e}")
            conn.rollback()
            raise

    def cleanup_orphaned_missing_episodes(self):
        """Limpiar episodios faltantes huérfanos (sin serie asociada)"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute('''
                DELETE FROM missing_episodes
                WHERE series_id NOT IN (SELECT id FROM series)
            ''')
            
            deleted = cursor.rowcount
            conn.commit()
            
            if deleted > 0:
                self.logger.info(f"Se eliminaron {deleted} episodios faltantes huérfanos")
            
        except Exception as e:
            self.logger.error(f"Error en cleanup_orphaned_missing_episodes: {e}")
            conn.rollback()
            raise
    
    def search_series_by_name(self, name, categories=None):
        """
        Buscar series por nombre (búsqueda flexible)
        
        Args:
            name: Nombre de la serie a buscar
            
        Returns:
            list: Lista de series que coinciden
        """
        conn = self.get_connection()
        cursor = conn.cursor()
        
        try:
            category_filter, params = self._build_series_category_filter(categories)
            # Búsqueda exacta primero
            cursor.execute(f'''
                SELECT * FROM series 
                WHERE (name = ? OR tmdb_name = ?){category_filter.replace(' WHERE', ' AND')}
            ''', (name, name, *params))
            
            exact_matches = cursor.fetchall()
            if exact_matches:
                return [dict(row) for row in exact_matches]
            
            # Búsqueda parcial (LIKE)
            search_pattern = f"%{name}%"
            cursor.execute(f'''
                SELECT * FROM series 
                WHERE (name LIKE ? OR tmdb_name LIKE ?){category_filter.replace(' WHERE', ' AND')}
                ORDER BY content_category, name
            ''', (search_pattern, search_pattern, *params))
            
            partial_matches = cursor.fetchall()
            return [dict(row) for row in partial_matches]

        except Exception as e:
            self.logger.error(f"Error en search_series_by_name: {e}")
            return []

    def get_series_names_by_category(self, categories=None):
        """
        Devuelve {name.lower(): name} para las categorías dadas en una sola query.

        Pensado para escaneos masivos (LibraryScanner) que antes lanzaban una
        query de búsqueda por cada carpeta solo para detectar diferencias de
        mayúsculas/minúsculas entre el nombre en disco y el de la BD.
        """
        conn = self.get_connection()
        cursor = conn.cursor()

        try:
            category_filter, params = self._build_series_category_filter(categories)
            cursor.execute(f'SELECT name FROM series{category_filter}', params)
            return {row['name'].lower(): row['name'] for row in cursor.fetchall()}
        except Exception as e:
            self.logger.error(f"Error en get_series_names_by_category: {e}")
            return {}

    # ========== LOGS ==========
    
    def insert_log(self, level, message):
        """Insertar log en base de datos"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute('''
                INSERT INTO logs (level, message)
                VALUES (?, ?)
            ''', (level, message))
            
            conn.commit()
            
        except Exception as e:
            # No loguear errores de logging para evitar recursión
            pass
    
    def get_recent_logs(self, limit=100):
        """Obtener logs recientes"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT * FROM logs
            ORDER BY timestamp DESC
            LIMIT ?
        ''', (limit,))
        
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    
    def clear_old_logs(self, days=30):
        """Limpiar logs antiguos"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute('''
                DELETE FROM logs
                WHERE timestamp < datetime('now', '-' || ? || ' days')
            ''', (days,))
            
            deleted = cursor.rowcount
            conn.commit()
            
            self.logger.info(f"Se eliminaron {deleted} logs antiguos")
            
        except Exception as e:
            self.logger.error(f"Error en clear_old_logs: {e}")
            conn.rollback()
