import os
import logging
from pathlib import Path
from .parser import EpisodeParser
from config.settings import Settings


class LibraryScanner:
    def __init__(self, series_paths=None, db_manager=None):
        # Si no se proporcionan rutas, usar las configuradas
        if series_paths is None:
            series_paths = Settings.get_series_paths()
        
        # Convertir a lista si es una sola ruta
        if isinstance(series_paths, (str, Path)):
            series_paths = [series_paths]
        
        self.series_paths = [Path(path) for path in series_paths]
        self.logger = logging.getLogger(__name__)
        self.parser = EpisodeParser()
        self.db_manager = db_manager
    
    def scan(self):
        """Escanear todas las rutas de series configuradas"""
        # Limpiar series que ya no existen (solo si tenemos db_manager)
        if self.db_manager:
            self.logger.info("Limpiando series eliminadas...")
            deleted_count = self.db_manager.cleanup_deleted_series()
            if deleted_count > 0:
                self.logger.info(f"Se eliminaron {deleted_count} series que ya no existen")
        
        all_series = []
        
        for series_path in self.series_paths:
            if not series_path.exists():
                self.logger.warning(f"Ruta no encontrada: {series_path}")
                continue
            
            self.logger.info(f"Escaneando ruta: {series_path}")
            series_list = self.scan_path(series_path)
            all_series.extend(series_list)
        
        # Eliminar duplicados basándose en la ruta normalizada de la serie
        unique_series = {}
        for series in all_series:
            series_path = str(Path(series['folder_path']).resolve()).lower()
            if series_path not in unique_series:
                unique_series[series_path] = series
            else:
                # Si ya existe, combinar episodios
                existing_series = unique_series[series_path]
                self.merge_series_data(existing_series, series)
        
        return list(unique_series.values())
    
    def scan_path(self, series_path):
        """Escanear una ruta específica de series"""
        series_list = []
        
        # Iterar por cada carpeta de serie
        for series_folder in series_path.iterdir():
            if not series_folder.is_dir():
                continue
            
            # Ignorar carpetas ocultas o del sistema
            if series_folder.name.startswith('.'):
                continue
            
            self.logger.info(f"Escaneando: {series_folder.name}")
            
            series_data = self.scan_series(series_folder)
            if series_data:
                series_list.append(series_data)
        
        return series_list
    
    def merge_series_data(self, existing_series, new_series):
        """Combinar datos de series duplicadas de diferentes rutas"""
        # Combinar episodios evitando duplicados
        for new_episode in new_series['episodes']:
            # Verificar si el episodio ya existe
            episode_exists = any(
                existing_ep['season'] == new_episode['season'] and 
                existing_ep['episode'] == new_episode['episode'] and
                (existing_ep['file_path'] == new_episode['file_path'] or 
                 Path(existing_ep['file_path']).name == Path(new_episode['file_path']).name)
                for existing_ep in existing_series['episodes']
            )
            
            if not episode_exists:
                existing_series['episodes'].append(new_episode)
        
        # Recalcular temporadas
        seasons_dict = {}
        for ep in existing_series['episodes']:
            season = ep['season']
            if season not in seasons_dict:
                seasons_dict[season] = 0
            seasons_dict[season] += 1
        
        existing_series['seasons'] = seasons_dict
    
    def scan_series(self, series_folder):
        """Escanear una serie específica"""
        # Verificar si existe en BD con otro nombre (diferencia de mayúsculas/minúsculas) y renombrar si es necesario
        if self.db_manager:
            try:
                existing_series = self.db_manager.search_series_by_name(series_folder.name)
                # Filtrar coincidencia exacta insensible a mayúsculas
                matches = [s for s in existing_series if s['name'].lower() == series_folder.name.lower()]
                
                if matches:
                    db_name = matches[0]['name']
                    # Si el nombre en disco difiere del de BD solo en mayúsculas/minúsculas
                    if db_name != series_folder.name:
                        self.logger.info(f"Renombrando carpeta '{series_folder.name}' a '{db_name}' para coincidir con la base de datos")
                        new_folder = series_folder.parent / db_name
                        series_folder.rename(new_folder)
                        series_folder = new_folder
            except Exception as e:
                self.logger.warning(f"No se pudo renombrar la carpeta '{series_folder.name}': {e}")

        series_name = series_folder.name
        
        # Estructura de datos de la serie
        series_data = {
            'name': series_name,
            'folder_path': str(series_folder),
            'seasons': {},
            'episodes': []
        }
        
        # Buscar temporadas
        season_folders = self.find_season_folders(series_folder)
        
        # Siempre escanear estructura plana para capturar todos los archivos
        flat_episodes = self.scan_flat_structure(series_folder)
        
        if season_folders:
            # Estructura con carpetas de temporadas
            for season_num, season_folder in season_folders.items():
                episodes = self.scan_season_folder(season_folder, season_num)
                series_data['episodes'].extend(episodes)
            
            # También agregar episodios de estructura plana que no estén en carpetas de temporadas
            for ep in flat_episodes:
                # Verificar si el episodio ya está en la lista (evitar duplicados mejorado)
                episode_exists = any(
                    existing_ep['season'] == ep['season'] and 
                    existing_ep['episode'] == ep['episode'] and
                    (existing_ep['file_path'] == ep['file_path'] or 
                     Path(existing_ep['file_path']).name == Path(ep['file_path']).name)
                    for existing_ep in series_data['episodes']
                )
                if not episode_exists:
                    series_data['episodes'].append(ep)
        else:
            # Series sin carpetas de temporadas (todo en raíz)
            series_data['episodes'] = flat_episodes
        
        # Agrupar por temporada
        seasons_dict = {}
        for ep in series_data['episodes']:
            season = ep['season']
            if season not in seasons_dict:
                seasons_dict[season] = 0
            seasons_dict[season] += 1
        
        series_data['seasons'] = seasons_dict
        
        return series_data
    
    def find_season_folders(self, series_folder):
        """Encontrar carpetas de temporadas"""
        season_folders = {}
        
        for item in series_folder.iterdir():
            if not item.is_dir():
                continue
            
            # Buscar patrones tipo "Season 1", "Temporada 1", "S01", etc.
            season_num = self.parser.parse_season_folder(item.name)
            if season_num is not None:
                season_folders[season_num] = item
        
        return season_folders
    
    def scan_season_folder(self, season_folder, season_num):
        """Escanear carpeta de temporada"""
        episodes = []
        
        for file_path in season_folder.iterdir():
            if not file_path.is_file():
                continue
            
            # Solo archivos de video
            if not self.is_video_file(file_path):
                continue
            
            episode_info = self.parser.parse_episode(file_path.name, season_num)
            
            if episode_info:
                episode_info['file_path'] = str(file_path)
                episodes.append(episode_info)
        
        return episodes
    
    def scan_flat_structure(self, series_folder):
        """Escanear estructura plana (sin carpetas de temporadas)"""
        episodes = []
        processed_files = set()  # Evitar procesar el mismo archivo múltiples veces
        
        for file_path in series_folder.rglob('*'):
            if not file_path.is_file():
                continue
            
            if not self.is_video_file(file_path):
                continue
            
            # Evitar duplicados por nombre de archivo
            file_key = (file_path.name.lower(), file_path.stat().st_size)
            if file_key in processed_files:
                continue
            processed_files.add(file_key)
            
            episode_info = self.parser.parse_episode(file_path.name)
            
            if episode_info:
                episode_info['file_path'] = str(file_path)
                episodes.append(episode_info)
        
        return episodes
    
    def is_video_file(self, file_path):
        """Verificar si es un archivo de video"""
        video_extensions = {
            '.mkv', '.mp4', '.avi', '.mov', '.wmv', 
            '.flv', '.m4v', '.mpg', '.mpeg', '.webm'
        }
        
        return file_path.suffix.lower() in video_extensions