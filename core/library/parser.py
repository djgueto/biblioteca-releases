import re
import logging


class EpisodeParser:
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        
        # Patrones para detectar temporada y episodio
        self.patterns = [
            # S01E01, S1E1
            re.compile(r'[Ss](\d{1,2})[Ee](\d{1,3})', re.IGNORECASE),
            # 1x01, 1x1
            re.compile(r'(\d{1,2})[xX](\d{1,3})'),
            # 101 (temporada 1, episodio 1)
            re.compile(r'[\s\._-](\d)(\d{2})[\s\._-]'),
        ]
        
        # Patrones para carpetas de temporadas
        self.season_patterns = [
            re.compile(r'[Ss]eason[\s\._-]?(\d{1,2})', re.IGNORECASE),
            re.compile(r'[Tt]emporada[\s\._-]?(\d{1,2})', re.IGNORECASE),
            re.compile(r'[Ss](\d{1,2})$', re.IGNORECASE),
        ]
    
    def parse_episode(self, filename, default_season=None):
        """
        Parsear nombre de archivo para extraer temporada y episodio
        
        Args:
            filename: Nombre del archivo
            default_season: Temporada por defecto si no se encuentra en el nombre
        
        Returns:
            dict con season, episode o None
        """
        for pattern in self.patterns:
            match = pattern.search(filename)
            if match:
                season = int(match.group(1))
                episode = int(match.group(2))
                
                return {
                    'season': season,
                    'episode': episode,
                    'filename': filename
                }
        
        # Si no se encontró patrón pero hay temporada por defecto
        if default_season is not None:
            # Intentar extraer solo el número de episodio
            ep_match = re.search(r'[Ee](\d{1,3})', filename)
            if ep_match:
                return {
                    'season': default_season,
                    'episode': int(ep_match.group(1)),
                    'filename': filename
                }
        
        self.logger.warning(f"No se pudo parsear: {filename}")
        return None
    
    def parse_season_folder(self, folder_name):
        """
        Parsear nombre de carpeta para extraer número de temporada
        
        Returns:
            int con número de temporada o None
        """
        for pattern in self.season_patterns:
            match = pattern.search(folder_name)
            if match:
                return int(match.group(1))
        
        return None