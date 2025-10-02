from difflib import SequenceMatcher
from datetime import datetime
import re
import logging


class TMDBAutoMatcher:
    def __init__(self, api_client):
        self.api = api_client
        self.logger = logging.getLogger(__name__)
    
    def match_series(self, local_series):
        """
        Matching automático de una serie local con TMDB
        
        Args:
            local_series: dict con {
                'name': str,
                'folder_path': str,
                'seasons': dict[int, int],
                'episodes': list
            }
        
        Returns:
            dict con {
                'tmdb_id': int,
                'name': str,
                'year': int,
                'confidence_score': float,
                'needs_review': bool
            } o None
        """
        # Normalizar nombre
        normalized_name = self.normalize_name(local_series['name'])
        
        self.logger.debug(f"Buscando: {normalized_name}")
        
        # Buscar en TMDB
        results = self.api.search_series(normalized_name)
        
        if not results:
            self.logger.warning(f"No se encontraron resultados para: {local_series['name']}")
            return None
        
        # Calcular scores para cada resultado
        scored_results = []
        for result in results[:10]:  # Top 10
            score = self.calculate_match_score(local_series, result)
            scored_results.append((result, score))
            
            self.logger.debug(
                f"  - {result['name']} ({result.get('first_air_date', '')[:4]}): {score:.2%}"
            )
        
        # Ordenar por score
        scored_results.sort(key=lambda x: x[1], reverse=True)
        best_match, best_score = scored_results[0]
        
        # Solo retornar si el score es suficientemente alto
        if best_score < 0.50:
            self.logger.warning(f"Score muy bajo ({best_score:.2%}) para: {local_series['name']}")
            return None
        
        year = best_match.get('first_air_date', '')
        year = int(year[:4]) if year and len(year) >= 4 else None
        
        return {
            'tmdb_id': best_match['id'],
            'name': best_match['name'],
            'year': year,
            'confidence_score': best_score,
            'needs_review': best_score < 0.85
        }
    
    def calculate_match_score(self, local_series, tmdb_result):
        """Calcular score de coincidencia (0-1)"""
        score = 0.0
        
        # 1. Similitud de nombre (40%)
        name_similarity = self.calculate_name_similarity(
            local_series['name'],
            tmdb_result['name']
        )
        score += name_similarity * 0.4
        
        # 2. Coincidencia de temporadas (30%)
        season_score = self.calculate_season_match(
            local_series,
            tmdb_result['id']
        )
        score += season_score * 0.3
        
        # 3. Coincidencia de episodios (20%)
        episode_score = self.calculate_episode_match(
            local_series,
            tmdb_result['id']
        )
        score += episode_score * 0.2
        
        # 4. Popularidad (10%)
        popularity = tmdb_result.get('popularity', 0)
        popularity_score = min(1.0, popularity / 100)
        score += popularity_score * 0.1
        
        return score
    
    def calculate_name_similarity(self, name1, name2):
        """Calcular similitud entre dos nombres"""
        # Normalizar ambos nombres
        norm1 = self.normalize_name(name1)
        norm2 = self.normalize_name(name2)
        
        # Usar SequenceMatcher para similitud
        similarity = SequenceMatcher(None, norm1, norm2).ratio()
        
        # Bonus si uno contiene al otro
        if norm1 in norm2 or norm2 in norm1:
            similarity = max(similarity, 0.9)
        
        return similarity
    
    def calculate_season_match(self, local_series, tmdb_id):
        """Calcular coincidencia de temporadas"""
        # Obtener detalles de TMDB
        tmdb_details = self.api.get_series_details(tmdb_id)
        
        if not tmdb_details:
            return 0.0
        
        tmdb_seasons = tmdb_details.get('number_of_seasons', 0)
        local_seasons = len(local_series.get('seasons', {}))
        
        if tmdb_seasons == 0:
            return 0.0
        
        # Si local tiene menos o igual temporadas que TMDB, es buena señal
        if local_seasons <= tmdb_seasons:
            return local_seasons / tmdb_seasons
        else:
            # Si local tiene más, penalizar
            return tmdb_seasons / local_seasons * 0.5
    
    def calculate_episode_match(self, local_series, tmdb_id):
        """Calcular coincidencia de episodios"""
        # Obtener detalles de TMDB
        tmdb_details = self.api.get_series_details(tmdb_id)
        
        if not tmdb_details:
            return 0.0
        
        # Contar episodios locales
        local_episode_count = len(local_series.get('episodes', []))
        
        # Contar episodios TMDB
        tmdb_episode_count = tmdb_details.get('number_of_episodes', 0)
        
        if tmdb_episode_count == 0:
            return 0.0
        
        # Calcular ratio
        if local_episode_count <= tmdb_episode_count:
            return local_episode_count / tmdb_episode_count
        else:
            return tmdb_episode_count / local_episode_count * 0.5
    
    def normalize_name(self, name):
        """Normalizar nombre para comparación"""
        # Convertir a minúsculas
        name = name.lower()
        
        # Quitar contenido entre paréntesis
        name = re.sub(r'\([^)]*\)', '', name)
        
        # Quitar años (4 dígitos)
        name = re.sub(r'\b\d{4}\b', '', name)
        
        # Quitar caracteres especiales excepto espacios
        name = re.sub(r'[^\w\s]', ' ', name)
        
        # Quitar espacios múltiples
        name = re.sub(r'\s+', ' ', name)
        
        # Quitar espacios al inicio y final
        name = name.strip()
        
        return name