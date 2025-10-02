import sqlite3
import logging
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
        self.logger.info("Base de datos inicializada correctamente")
    
    # ========== SERIES ==========
    
    def insert_or_update_series(self, series_data):
        """Insertar o actualizar una serie"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        try:
            # Verificar si existe
            cursor.execute(
                'SELECT id FROM series WHERE folder_path = ?',
                (series_data['folder_path'],)
            )
            existing = cursor.fetchone()
            
            if existing:
                series_id = existing[0]
                # Actualizar
                cursor.execute('''
                    UPDATE series 
                    SET name = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                ''', (series_data['name'], series_id))
            else:
                # Insertar
                cursor.execute('''
                    INSERT INTO series (name, folder_path)
                    VALUES (?, ?)
                ''', (series_data['name'], series_data['folder_path']))
                series_id = cursor.lastrowid
            
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
    
    def get_series_without_hispashare_url(self):
        """Obtener series sin URL de Hispashare"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT * FROM series
            WHERE tmdb_id IS NOT NULL
            AND (hispashare_url IS NULL OR hispashare_url = '')
            ORDER BY name
        ''')
        
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    
    # ========== EPISODIOS LOCALES ==========
    
    def insert_local_episodes(self, series_id, episodes):
        """Insertar episodios locales"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        try:
            for episode in episodes:
                cursor.execute('''
                    INSERT OR REPLACE INTO local_episodes
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