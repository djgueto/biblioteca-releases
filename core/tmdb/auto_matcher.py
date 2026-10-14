from difflib import SequenceMatcher
from datetime import datetime
import re
import logging
import unicodedata

from config.settings import Settings
from core.tmdb.metadata import allowed_media_types


class TMDBAutoMatcher:
    def __init__(self, api_client):
        self.api = api_client
        self.logger = logging.getLogger(__name__)

    def match_content(self, local_item):
        category = (local_item or {}).get('content_category', 'series')
        if Settings.get_category_content_mode(category) == 'episodic':
            return self.match_series(local_item)
        return self.match_single_file(local_item)
    
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
        if best_score < 0.40:  # Reducido de 0.50 a 0.40
            self.logger.warning(f"Score muy bajo ({best_score:.2%}) para: {local_series['name']}")
            return None
        
        year = best_match.get('first_air_date', '')
        year = int(year[:4]) if year and len(year) >= 4 else None
        
        return {
            'tmdb_id': best_match['id'],
            'name': self.get_result_title(best_match),
            'year': year,
            'confidence_score': best_score,
            'needs_review': best_score < Settings.AUTO_MATCH_THRESHOLD,
            'media_type': 'tv',
        }

    def match_single_file(self, local_item):
        normalized_name = self.normalize_name(local_item.get('name', ''))

        self.logger.debug(f"Buscando archivo unico: {normalized_name}")

        # Documentales y similares: en TMDB muchos son miniseries.
        allow_tv = 'tv' in allowed_media_types(local_item.get('content_category'))
        allowed = ('movie', 'tv', None) if allow_tv else ('movie', None)
        results = [(r, 'movie') for r in self.api.search_movies(normalized_name)]
        if not results:
            results = [
                (result, result.get('media_type') or 'movie') for result in self.api.search_multi(normalized_name)
                if result.get('media_type') in allowed
            ]

        scored_results = self._score_single_file(local_item, results)
        if allow_tv and (not scored_results or scored_results[0][1] < 0.45):
            # Documentales y similares: en TMDB muchos son miniseries.
            seen = {(r['id'], media_type) for (r, media_type), _ in scored_results}
            series = [(r, 'tv') for r in self.api.search_series(normalized_name) if (r['id'], 'tv') not in seen]
            scored_results = sorted(scored_results + self._score_single_file(local_item, series),
                                    key=lambda x: x[1], reverse=True)

        if not scored_results:
            self.logger.warning(f"No se encontraron resultados para: {local_item.get('name', '')}")
            return None

        (best_match, media_type), best_score = scored_results[0]

        if best_score < 0.45:
            self.logger.warning(
                f"Score muy bajo ({best_score:.2%}) para archivo unico: {local_item.get('name', '')}"
            )
            return None

        return {
            'tmdb_id': best_match['id'],
            'name': self.get_result_title(best_match),
            'year': self.get_result_year(best_match),
            'confidence_score': best_score,
            'needs_review': best_score < Settings.AUTO_MATCH_THRESHOLD,
            'media_type': media_type,
        }

    def _score_single_file(self, local_item, results):
        """[((resultado, media_type), score)] de mayor a menor."""
        scored = []
        for result, media_type in results[:10]:
            score = self.calculate_single_file_match_score(local_item, result)
            scored.append(((result, media_type), score))
            self.logger.debug(
                f"  - {self.get_result_title(result)} ({self.get_result_year(result) or ''}) [{media_type}]: {score:.2%}"
            )
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored

    def calculate_match_score(self, local_series, tmdb_result):
        """Calcular score de coincidencia (0-1)"""
        local_name = local_series.get('name', '')
        local_seasons = len(local_series.get('seasons', {}) or {})
        local_episodes = len(local_series.get('episodes', []) or [])

        name_similarity = self.calculate_name_similarity(local_name, tmdb_result.get('name', ''))
        local_year = self.extract_year(local_series)
        year_score = self.calculate_year_match(local_year, tmdb_result)
        date_score = self.calculate_date_relevance(tmdb_result)

        popularity = tmdb_result.get('popularity', 0)
        popularity_score = min(1.0, popularity / 100) if popularity else 0.0

        has_local_structure = local_seasons > 0 or local_episodes > 0
        is_sparse_local = local_episodes < 3 and local_seasons == 0

        if not has_local_structure or is_sparse_local:
            w_name = 0.78
            w_year = 0.18 if local_year else 0.0
            w_date = 0.03
            w_pop = 0.01
            denom = w_name + w_year + w_date + w_pop
            w_name, w_year, w_date, w_pop = (w_name / denom, w_year / denom, w_date / denom, w_pop / denom)
            return (name_similarity * w_name) + (year_score * w_year) + (date_score * w_date) + (popularity_score * w_pop)

        season_score = self.calculate_season_match(local_series, tmdb_result['id'])
        episode_score = self.calculate_episode_match(local_series, tmdb_result['id'])

        w_name = 0.55
        w_season = 0.20
        w_episode = 0.10
        w_year = 0.10 if local_year else 0.0
        w_date = 0.03
        w_pop = 0.02
        denom = w_name + w_season + w_episode + w_year + w_date + w_pop
        w_name, w_season, w_episode, w_year, w_date, w_pop = (
            w_name / denom,
            w_season / denom,
            w_episode / denom,
            w_year / denom,
            w_date / denom,
            w_pop / denom,
        )

        return (
            (name_similarity * w_name)
            + (season_score * w_season)
            + (episode_score * w_episode)
            + (year_score * w_year)
            + (date_score * w_date)
            + (popularity_score * w_pop)
        )
    
    def calculate_name_similarity(self, name1, name2):
        """Calcular similitud entre dos nombres"""
        def variants(raw: str) -> list[str]:
            raw = raw or ""
            base = self.primary_title(raw)
            return [self.normalize_name(raw), self.normalize_name(base)]

        best = 0.0
        v1 = variants(name1)
        v2 = variants(name2)

        for a in v1:
            for b in v2:
                if not a or not b:
                    continue
                sim = SequenceMatcher(None, a, b).ratio()
                if a in b or b in a:
                    sim = max(sim, 0.92)
                words1 = set(a.split())
                words2 = set(b.split())
                if words1 and words2:
                    overlap = len(words1.intersection(words2)) / max(len(words1), len(words2))
                    sim = max(sim, overlap * 0.9)
                best = max(best, sim)

        return best

    def calculate_single_file_match_score(self, local_item, tmdb_result):
        local_name = local_item.get('name', '')

        name_similarity = self.calculate_name_similarity(local_name, self.get_result_title(tmdb_result))
        local_year = self.extract_year(local_item)
        year_score = self.calculate_year_match(local_year, tmdb_result)
        date_score = self.calculate_date_relevance(tmdb_result)

        popularity = tmdb_result.get('popularity', 0)
        popularity_score = min(1.0, popularity / 100) if popularity else 0.0

        w_name = 0.78
        w_year = 0.18 if local_year else 0.0
        w_date = 0.03
        w_pop = 0.01
        denom = w_name + w_year + w_date + w_pop
        w_name, w_year, w_date, w_pop = (
            w_name / denom,
            w_year / denom,
            w_date / denom,
            w_pop / denom,
        )

        return (
            (name_similarity * w_name)
            + (year_score * w_year)
            + (date_score * w_date)
            + (popularity_score * w_pop)
        )
    
    def calculate_season_match(self, local_series, tmdb_id):
        """Calcular coincidencia de temporadas con mayor precisión"""
        # Obtener detalles de TMDB
        tmdb_details = self.api.get_series_details(tmdb_id)
        
        if not tmdb_details:
            return 0.0
        
        tmdb_seasons = tmdb_details.get('number_of_seasons', 0)
        local_seasons = len(local_series.get('seasons', {}))
        
        if tmdb_seasons == 0:
            return 0.0
        
        # Bonus por coincidencia exacta
        if local_seasons == tmdb_seasons:
            return 1.0
        
        # Si local tiene menos temporadas (serie en curso o incompleta)
        if local_seasons < tmdb_seasons:
            ratio = local_seasons / tmdb_seasons
            # Bonus adicional si la diferencia es pequeña (1-2 temporadas)
            if tmdb_seasons - local_seasons <= 2:
                return min(1.0, ratio + 0.2)
            return ratio
        
        # Si local tiene más temporadas (posible error o serie diferente)
        else:
            ratio = tmdb_seasons / local_seasons
            # Penalización menor si la diferencia es pequeña
            if local_seasons - tmdb_seasons <= 1:
                return ratio * 0.8
            return ratio * 0.5
    
    def calculate_episode_match(self, local_series, tmdb_id):
        """Calcular coincidencia de episodios con mayor precisión"""
        # Obtener detalles de TMDB
        tmdb_details = self.api.get_series_details(tmdb_id)
        
        if not tmdb_details:
            return 0.0
        
        # Contar episodios locales
        local_episode_count = len(local_series.get('episodes', []))
        
        # Contar episodios TMDB - validar que no sea None
        tmdb_episode_count = tmdb_details.get('number_of_episodes')
        if tmdb_episode_count is None:
            tmdb_episode_count = 0
        
        if tmdb_episode_count == 0:
            return 0.0
        
        # Bonus por coincidencia exacta
        if local_episode_count == tmdb_episode_count:
            return 1.0
        
        # Si local tiene menos episodios (serie en curso o incompleta)
        if local_episode_count < tmdb_episode_count:
            ratio = local_episode_count / tmdb_episode_count
            # Bonus si la diferencia es razonable (hasta 10 episodios)
            if tmdb_episode_count - local_episode_count <= 10:
                return min(1.0, ratio + 0.15)
            # Bonus menor para diferencias moderadas (hasta 25 episodios)
            elif tmdb_episode_count - local_episode_count <= 25:
                return min(1.0, ratio + 0.1)
            return ratio
        
        # Si local tiene más episodios (posible error o serie diferente)
        else:
            ratio = tmdb_episode_count / local_episode_count
            # Penalización menor si la diferencia es pequeña (hasta 5 episodios)
            if local_episode_count - tmdb_episode_count <= 5:
                return ratio * 0.9
            # Penalización moderada para diferencias medianas
            elif local_episode_count - tmdb_episode_count <= 15:
                return ratio * 0.7
            return ratio * 0.5
    
    def normalize_name(self, name):
        """Normalizar nombre para comparación"""
        name = name or ""
        name = unicodedata.normalize('NFD', name)
        name = ''.join(ch for ch in name if unicodedata.category(ch) != 'Mn')
        name = name.lower()
        
        # Quitar contenido entre paréntesis
        name = re.sub(r'\([^)]*\)', '', name)
        
        # Quitar años (4 dígitos)
        name = re.sub(r'\b\d{4}\b', '', name)
        
        # Quitar caracteres especiales excepto espacios
        name = name.replace('&', ' and ')
        name = re.sub(r'[^\w\s]', ' ', name)
        
        # Quitar espacios múltiples
        name = re.sub(r'\s+', ' ', name)
        
        # Quitar espacios al inicio y final
        name = name.strip()
        
        return name

    def primary_title(self, name: str) -> str:
        name = name or ""
        for sep in (" - ", " – ", " — ", ": "):
            if sep in name:
                return name.split(sep, 1)[0].strip()
        return name.strip()

    def extract_year(self, local_series) -> int | None:
        candidates = []
        for key in ('name', 'folder_path'):
            value = (local_series or {}).get(key)
            if value:
                candidates.append(str(value))

        text = " ".join(candidates)
        years = re.findall(r'\b(19\d{2}|20\d{2})\b', text)
        if not years:
            return None

        try:
            year = int(years[0])
        except ValueError:
            return None

        current_year = datetime.now().year
        if 1900 <= year <= current_year + 1:
            return year
        return None

    def calculate_year_match(self, local_year: int | None, tmdb_result) -> float:
        if not local_year:
            return 0.5

        result_year = self.get_result_year(tmdb_result)
        if not result_year:
            return 0.5

        diff = abs(result_year - local_year)
        if diff == 0:
            return 1.0
        if diff == 1:
            return 0.85
        if diff == 2:
            return 0.65
        return max(0.0, 1.0 - (diff / 8.0))

    def calculate_fast_score(self, local_series, tmdb_details) -> float:
        local_name = (local_series or {}).get('name', '')
        name_similarity = self.calculate_name_similarity(local_name, self.get_result_title(tmdb_details))
        local_year = self.extract_year(local_series)
        year_score = self.calculate_year_match(local_year, tmdb_details)
        date_score = self.calculate_date_relevance(tmdb_details)
        popularity = (tmdb_details or {}).get('popularity', 0) or 0
        popularity_score = min(1.0, popularity / 100) if popularity else 0.0

        w_name = 0.82
        w_year = 0.14 if local_year else 0.0
        w_date = 0.03
        w_pop = 0.01
        denom = w_name + w_year + w_date + w_pop
        w_name, w_year, w_date, w_pop = (w_name / denom, w_year / denom, w_date / denom, w_pop / denom)
        return (name_similarity * w_name) + (year_score * w_year) + (date_score * w_date) + (popularity_score * w_pop)
    
    def calculate_date_relevance(self, tmdb_result):
        """Calcular relevancia temporal - priorizar series recientes"""
        result_year = self.get_result_year(tmdb_result)

        if not result_year:
            return 0.5  # Score neutro si no hay fecha
        
        try:
            current_year = datetime.now().year
            
            # Calcular años de diferencia
            years_diff = current_year - result_year
            
            # Bonus para series muy recientes (últimos 5 años)
            if years_diff <= 5:
                return 1.0
            # Buena puntuación para series de la última década
            elif years_diff <= 10:
                return 0.8
            # Puntuación media para series de hace 10-20 años
            elif years_diff <= 20:
                return 0.6
            # Puntuación baja para series muy antiguas
            else:
                return 0.3
                
        except (ValueError, TypeError):
            return 0.5  # Score neutro si hay error en la fecha

    def get_result_title(self, tmdb_result):
        if not tmdb_result:
            return ''
        return tmdb_result.get('name') or tmdb_result.get('title') or ''

    def get_result_year(self, tmdb_result):
        if not tmdb_result:
            return None

        date_value = tmdb_result.get('first_air_date') or tmdb_result.get('release_date') or ''
        if not date_value or len(date_value) < 4:
            return None

        try:
            return int(date_value[:4])
        except (ValueError, TypeError):
            return None
