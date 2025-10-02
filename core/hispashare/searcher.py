from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from difflib import SequenceMatcher
import re
import logging
import time


class HispashareSearcher:
    def __init__(self, driver):
        self.driver = driver
        self.base_url = "https://www.hispashare.org"
        self.logger = logging.getLogger(__name__)
    
    def search_and_select(self, series_name, tmdb_year=None):
        """
        Busca serie y selecciona mejor resultado automáticamente
        
        Args:
            series_name: Nombre de la serie
            tmdb_year: Año de TMDB (opcional)
        
        Returns:
            str: URL completa del resultado seleccionado o None
        """
        try:
            # 1. Realizar búsqueda
            self.perform_search(series_name)
            
            # 2. Esperar resultados
            time.sleep(2)
            
            # 3. Extraer resultados
            results = self.extract_results()
            
            if not results:
                self.logger.warning(f"No se encontraron resultados para: {series_name}")
                return None
            
            # 4. Seleccionar mejor match
            best_result = self.select_best_match(results, series_name, tmdb_year)
            
            if best_result:
                self.logger.info(
                    f"Mejor match: {best_result['title']} "
                    f"(Score: {best_result.get('score', 0):.2%})"
                )
                return best_result['url']
            
            return None
            
        except Exception as e:
            self.logger.error(f"Error en search_and_select: {e}", exc_info=True)
            return None
    
    def perform_search(self, query):
        """Realizar búsqueda en Hispashare"""
        try:
            # Navegar a página de búsqueda si no estamos ahí
            if 'search' not in self.driver.current_url:
                self.driver.get(f"{self.base_url}/search")
                time.sleep(1)
            
            # Buscar campo de búsqueda
            search_input = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.NAME, "q"))
            )
            
            # Limpiar y escribir
            search_input.clear()
            search_input.send_keys(query)
            
            # Buscar botón de búsqueda
            search_btn = self.driver.find_element(
                By.CSS_SELECTOR,
                "a[onclick*='dosearch']"
            )
            search_btn.click()
            
            # Alternativa: enviar con Enter
            # search_input.send_keys(Keys.RETURN)
            
        except Exception as e:
            self.logger.error(f"Error en perform_search: {e}")
            raise
    
    def extract_results(self):
        """Extraer todos los resultados de búsqueda"""
        results = []
        
        try:
            # Esperar a que aparezcan resultados
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "tr.E, tr.O"))
            )
            
            # Obtener todas las filas de resultados
            rows = self.driver.find_elements(By.CSS_SELECTOR, "tr.E, tr.O")
            
            for row in rows:
                try:
                    result = self.parse_result_row(row)
                    if result:
                        results.append(result)
                except Exception as e:
                    self.logger.debug(f"Error parseando fila: {e}")
                    continue
            
            self.logger.info(f"Se encontraron {len(results)} resultados")
            
        except Exception as e:
            self.logger.error(f"Error extrayendo resultados: {e}")
        
        return results
    
    def parse_result_row(self, row):
        """Parsear una fila de resultados"""
        try:
            # URL
            link = row.find_element(By.CSS_SELECTOR, "a[href*='view=title']")
            url = link.get_attribute('href')
            
            # Título completo
            h1 = row.find_element(By.TAG_NAME, "h1")
            full_title = h1.text
            
            # Extraer año del título
            year_match = re.search(r'\(.*?(\d{4})\)', full_title)
            year = int(year_match.group(1)) if year_match else None
            
            # Buscar información de género
            try:
                genre_elem = row.find_element(
                    By.XPATH,
                    ".//p[contains(@class, 'INFO') and contains(., 'Género:')]"
                )
                genre_text = genre_elem.text
                
                # Filtrar solo series
                is_series = "Series >" in genre_text
                
                if not is_series:
                    return None
                
            except:
                # Si no encontramos género, asumir que podría ser serie
                genre_text = ""
            
            # Última publicación
            try:
                pub_elem = row.find_element(
                    By.XPATH,
                    ".//p[contains(@class, 'INFO') and contains(., 'Última publicación:')]"
                )
                last_pub = pub_elem.text.replace('Última publicación:', '').strip()
            except:
                last_pub = ""
            
            return {
                'url': url,
                'title': full_title,
                'year': year,
                'genre': genre_text,
                'last_publication': last_pub
            }
            
        except Exception as e:
            self.logger.debug(f"Error en parse_result_row: {e}")
            return None
    
    def select_best_match(self, results, series_name, tmdb_year):
        """Seleccionar mejor resultado automáticamente"""
        if not results:
            return None
        
        scored_results = []
        
        for result in results:
            score = self.calculate_result_score(result, series_name, tmdb_year)
            result['score'] = score
            scored_results.append((result, score))
            
            self.logger.debug(
                f"  {result['title']}: {score:.2%}"
            )
        
        # Ordenar por score
        scored_results.sort(key=lambda x: x[1], reverse=True)
        best_result, best_score = scored_results[0]
        
        # Solo retornar si score >= threshold
        threshold = 0.75
        if best_score >= threshold:
            return best_result
        else:
            self.logger.warning(
                f"Mejor score ({best_score:.2%}) por debajo del threshold ({threshold:.2%})"
            )
            return None
    
    def calculate_result_score(self, result, series_name, tmdb_year):
        """Calcular score de un resultado"""
        score = 0.0
        
        # 1. Similitud de título (50%)
        title_similarity = SequenceMatcher(
            None,
            series_name.lower(),
            result['title'].lower()
        ).ratio()
        score += title_similarity * 0.5
        
        # 2. Coincidencia de año (30%)
        if result['year'] and tmdb_year:
            year_diff = abs(result['year'] - tmdb_year)
            if year_diff == 0:
                year_score = 1.0
            elif year_diff <= 1:
                year_score = 0.8
            elif year_diff <= 2:
                year_score = 0.5
            else:
                year_score = max(0, 1 - (year_diff / 10))
            
            score += year_score * 0.3
        
        # 3. Recencia de publicación (20%)
        recency_score = self.calculate_recency_score(result['last_publication'])
        score += recency_score * 0.2
        
        return score
    
    def calculate_recency_score(self, last_publication):
        """Calcular score basado en recencia de publicación"""
        if not last_publication:
            return 0.0
        
        last_pub_lower = last_publication.lower()
        
        # Publicado hace días
        if 'día' in last_pub_lower or 'day' in last_pub_lower:
            return 1.0
        
        # Publicado hace semanas
        if 'semana' in last_pub_lower or 'week' in last_pub_lower:
            return 0.8
        
        # Publicado hace meses
        if 'mes' in last_pub_lower or 'month' in last_pub_lower:
            # Intentar extraer número de meses
            match = re.search(r'(\d+)', last_publication)
            if match:
                months = int(match.group(1))
                return max(0, 1 - (months / 12))
            return 0.5
        
        # Publicado hace años
        if 'año' in last_pub_lower or 'year' in last_pub_lower:
            match = re.search(r'(\d+)', last_publication)
            if match:
                years = int(match.group(1))
                return max(0, 1 - (years / 5))
            return 0.2
        
        return 0.3