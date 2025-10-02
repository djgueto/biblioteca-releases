import os
import logging
from pathlib import Path
from .parser import EpisodeParser


class LibraryScanner:
    def __init__(self, series_path):
        self.series_path = Path(series_path)
        self.logger = logging.getLogger(__name__)
        self.parser = EpisodeParser()
    
    def scan(self):
        """Escanear carpeta de series"""
        if not self.series_path.exists():
            raise FileNotFoundError(f"Ruta no encontrada: {self.series_path}")
        
        series_list = []
        
        # Iterar por cada carpeta de serie
        for series_folder in self.series_path.iterdir():
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
    
    def scan_series(self, series_folder):
        """Escanear una serie específica"""
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
                # Verificar si el episodio ya está en la lista (evitar duplicados)
                episode_exists = any(
                    existing_ep['season'] == ep['season'] and 
                    existing_ep['episode'] == ep['episode'] and
                    existing_ep['file_path'] == ep['file_path']
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
        
        for file_path in series_folder.rglob('*'):
            if not file_path.is_file():
                continue
            
            if not self.is_video_file(file_path):
                continue
            
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