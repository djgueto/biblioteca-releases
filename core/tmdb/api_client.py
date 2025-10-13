import requests
import logging
from datetime import datetime


class TMDBClient:
    def __init__(self, api_key):
        self.api_key = api_key
        self.base_url = "https://api.themoviedb.org/3"
        self.logger = logging.getLogger(__name__)
        self.session = requests.Session()
    
    def _make_request(self, endpoint, params=None):
        """Hacer petición a la API de TMDB"""
        if params is None:
            params = {}
        
        params['api_key'] = self.api_key
        params['language'] = 'es-ES'
        
        url = f"{self.base_url}/{endpoint}"
        
        try:
            response = self.session.get(url, params=params, timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Error en petición TMDB: {e}")
            return None
    
    def search_series(self, query):
        """Buscar series por nombre"""
        data = self._make_request('search/tv', {'query': query})
        
        if data and 'results' in data:
            return data['results']
        
        return []
    
    def get_series_details(self, series_id):
        """Obtener detalles completos de una serie"""
        return self._make_request(f'tv/{series_id}')
    
    def get_season_details(self, series_id, season_number):
        """Obtener detalles de una temporada"""
        return self._make_request(f'tv/{series_id}/season/{season_number}')
    
    def get_all_episodes(self, series_id, only_aired=True):
        """
        Obtener todos los episodios de una serie
        
        Args:
            series_id: ID de la serie en TMDB
            only_aired: Si True, solo incluye episodios ya emitidos (por defecto True)
        
        Returns:
            list: Lista de episodios filtrados por fecha de emisión
        """
        episodes = []
        today = datetime.now().date()
        
        # Primero obtener detalles de la serie para saber cuántas temporadas tiene
        series_details = self.get_series_details(series_id)
        
        if not series_details:
            return episodes
        
        num_seasons = series_details.get('number_of_seasons', 0)
        
        # Iterar por cada temporada
        for season_num in range(1, num_seasons + 1):
            season_data = self.get_season_details(series_id, season_num)
            
            if not season_data or 'episodes' not in season_data:
                continue
            
            for episode in season_data['episodes']:
                air_date_str = episode.get('air_date', '')
                
                # Si only_aired es True, filtrar por fecha de emisión
                if only_aired and air_date_str:
                    try:
                        # Parsear fecha de emisión (formato YYYY-MM-DD)
                        air_date = datetime.strptime(air_date_str, '%Y-%m-%d').date()
                        
                        # Solo incluir si ya se emitió (fecha <= hoy)
                        if air_date > today:
                            self.logger.debug(f"Episodio futuro omitido: S{season_num}E{episode['episode_number']} - {air_date_str}")
                            continue
                            
                    except ValueError:
                        # Si no se puede parsear la fecha, omitir el episodio si only_aired=True
                        self.logger.warning(f"Fecha de emisión inválida para episodio S{season_num}E{episode['episode_number']}: {air_date_str}")
                        if only_aired:
                            continue
                
                episodes.append({
                    'season_number': season_num,
                    'episode_number': episode['episode_number'],
                    'name': episode['name'],
                    'air_date': air_date_str,
                    'overview': episode.get('overview', '')
                })
        
        return episodes
    
    def get_latest_episode(self, series_id):
        """Obtener el último episodio emitido"""
        series_details = self.get_series_details(series_id)
        
        if series_details and 'last_episode_to_air' in series_details:
            return series_details['last_episode_to_air']
        
        return None
    
    def get_next_episode(self, series_id):
        """Obtener el próximo episodio a emitir"""
        series_details = self.get_series_details(series_id)
        
        if series_details and 'next_episode_to_air' in series_details:
            return series_details['next_episode_to_air']
        
        return None
    
    def is_series_active(self, series_id):
        """Verificar si la serie sigue en emisión"""
        series_details = self.get_series_details(series_id)
        
        if not series_details:
            return False
        
        status = series_details.get('status', '')
        return status in ['Returning Series', 'In Production']