import sqlite3
import logging
import os
from pathlib import Path
from datetime import datetime
from config.settings import Settings


class DatabaseManager:
    def __init__(self, db_path=None):
        self.db_path = db_path or Settings.DB_PATH
        self.logger = logging.getLogger(__name__)
        self.conn = None
    
    def get_connection(self):
        """Obtener conexión a la base de datos"""
        if self.conn is None:
            self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self.conn.row_factory = sqlite3.Row
        return self.conn
    
    def close(self):
        """Cerrar conexión"""
        if self.conn:
            self.conn.close()
            self.conn = None
    
    def initialize_database(self):
        """Crear tablas si no existen"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # Tabla de series
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS series (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                folder_path TEXT UNIQUE NOT NULL,
                tmdb_id INTEGER,
                tmdb_name TEXT,
                year INTEGER,
                confidence_score REAL,
                needs_review BOOLEAN DEFAULT 0,
                hispashare_url TEXT,
                status TEXT DEFAULT 'pending',
                search_disabled BOOLEAN DEFAULT 0,
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
        
        # Índices
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_series_tmdb ON series(tmdb_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_episodes_series ON local_episodes(series_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_missing_series ON missing_episodes(series_id)')
        
        conn.commit()
        
        # Migración: Agregar campo search_disabled si no existe
        try:
            cursor.execute("ALTER TABLE series ADD COLUMN search_disabled BOOLEAN DEFAULT 0")
            conn.commit()
            self.logger.info("Campo search_disabled agregado a la tabla series")
        except Exception:
            # El campo ya existe, no hacer nada
            pass
        
        self.logger.info("Base de datos inicializada correctamente")
    
    # ========== SERIES ==========
    
    def normalize_path(self, path: str) -> str:
        """
        Normaliza rutas para evitar duplicados entre rutas de red y unidades mapeadas
        """
        try:
            # Convertir a Path para normalización básica
            normalized = Path(path).resolve()
            
            # Si es una ruta de red, mantenerla como tal
            if str(normalized).startswith('\\\\'):
                return str(normalized).replace('/', '\\')
            
            # Si es una unidad mapeada, intentar obtener la ruta real de red
            if os.name == 'nt' and len(str(normalized)) > 1 and str(normalized)[1] == ':':
                try:
                    # Intentar obtener la ruta UNC real usando net use
                    import subprocess
                    result = subprocess.run(['net', 'use'], capture_output=True, text=True, shell=True)
                    if result.returncode == 0:
                        lines = result.stdout.split('\n')
                        drive_letter = str(normalized)[:2].upper()
                        
                        for line in lines:
                            if drive_letter in line and '\\\\' in line:
                                # Extraer la ruta UNC
                                parts = line.split()
                                for part in parts:
                                    if part.startswith('\\\\'):
                                        # Reemplazar la unidad mapeada con la ruta UNC
                                        relative_path = str(normalized)[2:]  # Quitar "C:"
                                        if relative_path.startswith('\\'):
                                            relative_path = relative_path[1:]
                                        return os.path.join(part, relative_path).replace('/', '\\')
                except Exception:
                    pass
            
            return str(normalized).replace('/', '\\')
            
        except Exception as e:
            logging.warning(f"Error normalizando ruta {path}: {e}")
            return path

    def insert_or_update_series(self, series_data):
        """Insertar o actualizar una serie"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        try:
            # Normalizar la ruta antes de buscar
            normalized_path = self.normalize_path(series_data['folder_path'])
            
            # Verificar si existe por ruta normalizada
            cursor.execute(
                'SELECT id, folder_path FROM series WHERE folder_path = ?',
                (normalized_path,)
            )
            existing = cursor.fetchone()
            
            # Si no existe con la ruta normalizada, buscar por nombre y verificar si es la misma ubicación física
            if not existing:
                cursor.execute(
                    'SELECT id, folder_path FROM series WHERE name = ?',
                    (series_data['name'],)
                )
                name_matches = cursor.fetchall()
                
                for match_id, match_path in name_matches:
                    if self.normalize_path(match_path) == normalized_path:
                        existing = (match_id, match_path)
                        break
            
            if existing:
                series_id = existing[0]
                # Actualizar con la ruta normalizada
                cursor.execute('''
                    UPDATE series 
                    SET name = ?, folder_path = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                ''', (series_data['name'], normalized_path, series_id))
                logging.info(f"Serie actualizada: {series_data['name']} -> {normalized_path}")
            else:
                # Insertar con ruta normalizada
                cursor.execute('''
                    INSERT INTO series (name, folder_path)
                    VALUES (?, ?)
                ''', (series_data['name'], normalized_path))
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
        """Actualizar información de TMDB"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute('''
                UPDATE series
                SET tmdb_id = ?, tmdb_name = ?, year = ?,
                    confidence_score = ?, needs_review = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (
                match_data['tmdb_id'],
                match_data['name'],
                match_data['year'],
                match_data['confidence_score'],
                match_data['needs_review'],
                series_id
            ))
            
            conn.commit()
            
        except Exception as e:
            self.logger.error(f"Error en update_tmdb_match: {e}")
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
    
    def update_series_manual(self, series_id, changes):
        """Actualizar serie con cambios manuales"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        try:
            # Verificar si se está cambiando el TMDB ID
            tmdb_id_changed = 'tmdb_id' in changes
            
            # Construir query dinámicamente basado en los cambios
            set_clauses = []
            values = []
            
            for field, value in changes.items():
                if field in ['tmdb_id', 'tmdb_name', 'year', 'confidence_score', 
                           'needs_review', 'hispashare_url', 'status']:
                    set_clauses.append(f"{field} = ?")
                    values.append(value)
            
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
            if tmdb_id_changed and changes.get('tmdb_id'):
                self.logger.info(f"Recalculando episodios faltantes para serie {series_id} debido a cambio de TMDB ID")
                try:
                    missing_count = self.recalculate_missing_episodes_for_series(series_id)
                    self.logger.info(f"Recalculados {missing_count} episodios faltantes")
                    # Retornar información sobre el recálculo para que la UI pueda actualizarse
                    return {'recalculated_missing': True, 'missing_count': missing_count}
                except Exception as e:
                    self.logger.error(f"Error al recalcular episodios faltantes: {e}")
                    # No lanzar excepción para no afectar la actualización principal
            
            return {'recalculated_missing': False}
            
        except Exception as e:
            self.logger.error(f"Error en update_series_manual: {e}")
            conn.rollback()
            raise
    
    def get_all_series(self):
        """Obtener todas las series"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT s.*, 
                   COUNT(DISTINCT me.id) as missing_count
            FROM series s
            LEFT JOIN missing_episodes me ON s.id = me.series_id
            GROUP BY s.id
            ORDER BY s.name
        ''')
        
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    
    def get_series_with_hispashare_urls(self):
        """Obtener series que tienen URL de Hispashare"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT id, name, folder_path, hispashare_url, tmdb_id
            FROM series 
            WHERE hispashare_url IS NOT NULL AND hispashare_url != ''
            ORDER BY name
        ''')
        
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    
    def get_series_with_tmdb(self):
        """Obtener series con TMDB ID"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT * FROM series
            WHERE tmdb_id IS NOT NULL
            ORDER BY name
        ''')
        
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    
    def get_series_by_id(self, series_id):
        """Obtener una serie por su ID"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT * FROM series WHERE id = ?
        ''', (series_id,))
        
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_series_without_hispashare_url(self):
        """Obtener series sin URL de Hispashare y que no tengan búsqueda deshabilitada"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT id, name, tmdb_name, year, tmdb_id
            FROM series 
            WHERE (hispashare_url IS NULL OR hispashare_url = '') 
            AND (search_disabled IS NULL OR search_disabled = 0)
            ORDER BY name
        ''')
        
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    
    def get_series_with_missing_episodes_without_url(self):
        """Obtener series que tienen episodios faltantes y no tienen URL de Hispashare"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT DISTINCT s.id, s.name, s.tmdb_name, s.year, s.tmdb_id
            FROM series s
            INNER JOIN missing_episodes me ON s.id = me.series_id
            WHERE (s.hispashare_url IS NULL OR s.hispashare_url = '') 
            AND (s.search_disabled IS NULL OR s.search_disabled = 0)
            AND me.status = 'missing'
            ORDER BY s.name
        ''')
        
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    
    def get_series_with_missing_episodes_and_url(self):
        """Obtener series que tienen episodios faltantes y tienen URL de Hispashare"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT DISTINCT s.id, s.name, s.folder_path, s.hispashare_url, s.tmdb_id
            FROM series s
            INNER JOIN missing_episodes me ON s.id = me.series_id
            WHERE s.hispashare_url IS NOT NULL AND s.hispashare_url != ''
            AND me.status = 'missing'
            ORDER BY s.name
        ''')
        
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    
    def disable_series_search(self, series_id):
        """Marcar una serie para que no se busque más automáticamente"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute('''
                UPDATE series 
                SET search_disabled = 1, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (series_id,))
            conn.commit()
            self.logger.info(f"Búsqueda deshabilitada para serie ID: {series_id}")
            
        except Exception as e:
            self.logger.error(f"Error al deshabilitar búsqueda: {e}")
            conn.rollback()
            raise
    
    def enable_series_search(self, series_id):
        """Reactivar la búsqueda automática para una serie"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute('''
                UPDATE series 
                SET search_disabled = 0, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (series_id,))
            conn.commit()
            self.logger.info(f"Búsqueda reactivada para serie ID: {series_id}")
            
        except Exception as e:
            self.logger.error(f"Error al reactivar búsqueda: {e}")
            conn.rollback()
            raise

    def cleanup_deleted_series(self):
        """Limpiar series cuyas carpetas ya no existen en el disco o no están en rutas configuradas"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        try:
            # Obtener rutas configuradas actualmente
            configured_paths = Settings.get_series_paths()
            configured_paths = [Path(path).resolve() for path in configured_paths if path.strip()]
            
            # Obtener todas las series con sus rutas
            cursor.execute('SELECT id, name, folder_path FROM series')
            all_series = cursor.fetchall()
            
            deleted_count = 0
            
            for series in all_series:
                series_id, series_name, folder_path = series
                series_path = Path(folder_path)
                
                should_delete = False
                delete_reason = ""
                
                # Verificar si la carpeta existe
                if not series_path.exists():
                    should_delete = True
                    delete_reason = "carpeta no existe"
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
                cursor.execute('''
                    INSERT INTO local_episodes
                    (series_id, season, episode, filename, file_path)
                    VALUES (?, ?, ?, ?, ?)
                ''', (
                    series_id,
                    episode['season'],
                    episode['episode'],
                    episode['filename'],
                    episode['file_path']
                ))
            
            conn.commit()
            
        except Exception as e:
            self.logger.error(f"Error en insert_local_episodes: {e}")
            conn.rollback()
            raise
    
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
            
            # Limpiar episodios faltantes existentes
            self.clear_missing_episodes(series_id)
            
            # Obtener episodios de TMDB
            tmdb_client = TMDBClient(Settings.TMDB_API_KEY)
            tmdb_episodes = tmdb_client.get_all_episodes(series['tmdb_id'])
            
            # Obtener episodios locales
            local_episodes = self.get_local_episodes(series_id)
            
            # Crear set de episodios locales
            local_set = set()
            for ep in local_episodes:
                local_set.add((ep['season'], ep['episode']))
            
            # Encontrar faltantes
            missing_count = 0
            for ep in tmdb_episodes:
                key = (ep['season_number'], ep['episode_number'])
                if key not in local_set:
                    episode_data = {
                        'series_id': series_id,
                        'series_name': series['name'],
                        'season': ep['season_number'],
                        'episode': ep['episode_number'],
                        'name': ep['name'],
                        'air_date': ep.get('air_date', '')
                    }
                    self.insert_missing_episode(episode_data)
                    missing_count += 1
            
            self.logger.info(f"Recalculados {missing_count} episodios faltantes para serie {series['name']}")
            return missing_count
            
        except Exception as e:
            self.logger.error(f"Error en recalculate_missing_episodes_for_series: {e}")
            conn.rollback()
            raise

    def cleanup_orphaned_missing_episodes(self):
        """Limpiar episodios faltantes huérfanos (sin serie padre)"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        try:
            # Contar episodios huérfanos antes de limpiar
            cursor.execute('''
                SELECT COUNT(*)
                FROM missing_episodes me
                LEFT JOIN series s ON me.series_id = s.id
                WHERE s.id IS NULL
            ''')
            
            orphan_count_before = cursor.fetchone()[0]
            
            if orphan_count_before == 0:
                self.logger.info("No hay episodios faltantes huérfanos para limpiar")
                return 0
            
            # Eliminar episodios huérfanos
            cursor.execute('''
                DELETE FROM missing_episodes
                WHERE series_id NOT IN (SELECT id FROM series)
            ''')
            
            deleted_count = cursor.rowcount
            conn.commit()
            
            self.logger.info(f"Limpiados {deleted_count} episodios faltantes huérfanos")
            return deleted_count
            
        except Exception as e:
            self.logger.error(f"Error en cleanup_orphaned_missing_episodes: {e}")
            conn.rollback()
            raise
    
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