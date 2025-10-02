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
    
    def get_all_episodes(self, series_id):
        """Obtener todos los episodios de una serie"""
        episodes = []
        
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
                episodes.append({
                    'season_number': season_num,
                    'episode_number': episode['episode_number'],
                    'name': episode['name'],
                    'air_date': episode.get('air_date', ''),
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