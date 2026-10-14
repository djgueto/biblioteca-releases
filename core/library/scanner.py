import os
import logging
from pathlib import Path
from .parser import EpisodeParser
from .unreadable_names import is_unreadable_name
from config.settings import Settings


class LibraryScanner:
    def __init__(self, series_paths=None, db_manager=None, content_category='series'):
        # Si no se proporcionan rutas, usar las configuradas
        if series_paths is None:
            series_paths = Settings.get_paths_for_category(content_category)
        
        # Convertir a lista si es una sola ruta
        if isinstance(series_paths, (str, Path)):
            series_paths = [series_paths]
        
        self.series_paths = [Path(path) for path in series_paths]
        self.content_category = content_category
        self.logger = logging.getLogger(__name__)
        self.parser = EpisodeParser()
        self.db_manager = db_manager
        self._name_index = None
        self._skip_paths = set()

    @classmethod
    def scan_all_episodic_categories(cls, db_manager=None):
        all_series = []
        for category in ('series', 'cartoons', 'docuseries'):
            scanner = cls(db_manager=db_manager, content_category=category)
            all_series.extend(scanner.scan(skip_cleanup=True))
        return all_series
    
    def scan(self, skip_cleanup=False):
        """Escanear todas las rutas de series configuradas"""
        # Limpiar series que ya no existen (solo si tenemos db_manager)
        if self.db_manager and not skip_cleanup:
            self.logger.info("Limpiando series eliminadas...")
            deleted_count = self.db_manager.cleanup_deleted_series()
            if deleted_count > 0:
                self.logger.info(f"Se eliminaron {deleted_count} series que ya no existen")

        # Cargar una vez el índice de nombres de esta categoría en vez de una
        # query por carpeta (ver scan_series) — antes N carpetas = N queries.
        if self.db_manager:
            self._name_index = self.db_manager.get_series_names_by_category([self.content_category])
            # Series no monitorizadas: no se reescanean
            self._skip_paths = self.db_manager.get_unmonitored_folder_paths()

        all_series = []
        
        for series_path in self.series_paths:
            # os.path.exists y no Path.exists: en Python 3.10 este último lanza
            # OSError si el NAS rechaza las credenciales (WinError 1326).
            if not os.path.exists(series_path):
                self.logger.warning(f"Ruta no encontrada o sin acceso: {series_path}")
                continue

            self.logger.info(f"Escaneando ruta: {series_path}")
            try:
                series_list = self.scan_path(series_path)
            except OSError as e:
                self.logger.warning(f"No se pudo leer {series_path}: {e}")
                continue
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

            # Ignorar carpetas temporales dejadas por un rename de solo-mayúsculas
            # interrumpido o en curso por otro escaneo concurrente (ver scan_series).
            if series_folder.name.endswith('__casefix_tmp'):
                continue

            if is_unreadable_name(series_folder):
                continue

            if self._skip_paths and str(series_folder.resolve()).lower() in self._skip_paths:
                self.logger.debug(f"Omitida (no monitorizada): {series_folder.name}")
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
        # Verificar si existe en BD con otro nombre (diferencia de mayúsculas/minúsculas) y renombrar si es necesario.
        # Usa el índice cargado una vez en scan() en vez de una query por carpeta.
        if self.db_manager and self._name_index:
            try:
                db_name = self._name_index.get(series_folder.name.lower())
                # Si el nombre en disco difiere del de BD solo en mayúsculas/minúsculas
                if db_name and db_name != series_folder.name:
                    new_folder = series_folder.parent / db_name
                    if new_folder.exists():
                        # El destino ya existe como carpeta real y distinta (p.ej. el
                        # share SMB expone dos carpetas que solo difieren en mayúsculas
                        # como entradas independientes). No se fusiona/sobrescribe
                        # automáticamente: requiere resolución manual.
                        self.logger.warning(
                            f"No se renombra '{series_folder.name}' a '{db_name}': "
                            f"ya existe una carpeta distinta con ese nombre en disco. "
                            f"Revisa manualmente si son duplicados."
                        )
                    else:
                        self.logger.info(f"Renombrando carpeta '{series_folder.name}' a '{db_name}' para coincidir con la base de datos")
                        if db_name.lower() == series_folder.name.lower():
                            # Cambio de solo mayúsculas/minúsculas: en sistemas de archivos
                            # case-insensitive (Windows/SMB) un rename directo entre nombres
                            # que solo difieren en casing falla con WinError 183, ya que el
                            # SO lo ve como renombrar X a X. Se usa un nombre intermedio.
                            temp_folder = series_folder.parent / f"{series_folder.name}__casefix_tmp"
                            series_folder.rename(temp_folder)
                            if new_folder.exists():
                                # Colisión aparecida durante el propio rename (carrera
                                # con otro escaneo concurrente): deshacer y dejar como
                                # estaba en vez de quedarse a medias con el nombre temporal.
                                self.logger.warning(
                                    f"Colisión detectada renombrando '{series_folder.name}': "
                                    f"'{db_name}' apareció durante el proceso. Revirtiendo."
                                )
                                temp_folder.rename(series_folder)
                            else:
                                temp_folder.rename(new_folder)
                                series_folder = new_folder
                        else:
                            series_folder.rename(new_folder)
                            series_folder = new_folder
            except Exception as e:
                self.logger.warning(f"No se pudo renombrar la carpeta '{series_folder.name}': {e}")

        series_name = series_folder.name
        
        # Estructura de datos de la serie
        series_data = {
            'name': series_name,
            'folder_path': str(series_folder),
            'content_category': self.content_category,
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
    
    def _parse_episode_with_fallback(self, file_path, root_folder, default_season=None):
        """Parsear episodio desde el nombre del archivo; si falla, probar carpetas padre."""
        episode_info = self.parser.parse_episode(file_path.name, default_season)
        if episode_info:
            return episode_info
        # Intentar con nombres de carpetas intermedias (excluye la raíz de la serie)
        for parent in file_path.parents:
            if parent == root_folder:
                break
            episode_info = self.parser.parse_episode(parent.name, default_season)
            if episode_info:
                episode_info['filename'] = file_path.name
                return episode_info
        return None

    def _video_files(self, folder, recursive):
        """[(ruta, tamaño en bytes)] de los vídeos de una carpeta.

        Con os.scandir: en Windows el tipo y el tamaño vienen en el propio
        listado de la carpeta. Con iterdir()/rglob() + is_file() + stat()
        eran 2-3 viajes de red más por archivo sobre la biblioteca del NAS.
        """
        found = []
        pending = [str(folder)]
        while pending:
            current = pending.pop()
            try:
                with os.scandir(current) as entries:
                    for entry in entries:
                        try:
                            if entry.is_dir():
                                # Como rglob: no se entra en enlaces a carpetas (evita bucles).
                                if recursive and not entry.is_symlink():
                                    pending.append(entry.path)
                                continue
                            if not entry.is_file():
                                continue
                            path = Path(entry.path)
                            if not self.is_video_file(path):
                                continue
                            size = entry.stat().st_size
                        except OSError:
                            continue
                        found.append((path, size))
            except OSError as e:
                if current == str(folder):
                    raise
                self.logger.warning(f"No se pudo leer {current}: {e}")
        return found

    def _episode_from_file(self, file_path, size, root_folder, default_season=None):
        episode_info = self._parse_episode_with_fallback(file_path, root_folder, default_season)
        if not episode_info:
            return None
        episode_info['file_path'] = str(file_path)
        episode_info.update(self.parser.parse_quality(file_path.name))
        episode_info['size_mb'] = round(size / (1024 * 1024), 2)
        return episode_info

    def scan_season_folder(self, season_folder, season_num):
        """Escanear carpeta de temporada"""
        episodes = []
        for file_path, size in self._video_files(season_folder, recursive=False):
            episode_info = self._episode_from_file(file_path, size, season_folder, season_num)
            if episode_info:
                episodes.append(episode_info)
        return episodes

    def scan_flat_structure(self, series_folder):
        """Escanear estructura plana (sin carpetas de temporadas)"""
        episodes = []
        processed_files = set()  # Evitar procesar el mismo archivo múltiples veces

        for file_path, size in self._video_files(series_folder, recursive=True):
            # Evitar duplicados por nombre de archivo
            file_key = (file_path.name.lower(), size)
            if file_key in processed_files:
                continue
            processed_files.add(file_key)

            episode_info = self._episode_from_file(file_path, size, series_folder)
            if episode_info:
                episodes.append(episode_info)

        return episodes

    def is_video_file(self, file_path):
        """Verificar si es un archivo de video"""
        video_extensions = {
            '.mkv', '.mp4', '.avi', '.mov', '.wmv', 
            '.flv', '.m4v', '.mpg', '.mpeg', '.webm'
        }
        
        return file_path.suffix.lower() in video_extensions
