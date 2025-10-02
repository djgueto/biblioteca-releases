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
            self.logger.info(f"Iniciando búsqueda para: '{query}'")
            
            # Navegar a página de búsqueda si no estamos ahí
            if 'search' not in self.driver.current_url:
                self.logger.info("Navegando a página de búsqueda...")
                self.driver.get(f"{self.base_url}/search")
                time.sleep(2)  # Aumentar tiempo de espera
            
            self.logger.info(f"URL actual: {self.driver.current_url}")
            
            # Buscar campo de búsqueda con múltiples selectores
            search_input = None
            selectors = [
                (By.NAME, "q"),
                (By.ID, "q"),
                (By.CSS_SELECTOR, "input[name='q']"),
                (By.CSS_SELECTOR, "input[type='text']"),
                (By.CSS_SELECTOR, ".search-input"),
                (By.XPATH, "//input[@placeholder*='buscar' or @placeholder*='search']")
            ]
            
            for selector_type, selector_value in selectors:
                try:
                    search_input = WebDriverWait(self.driver, 5).until(
                        EC.presence_of_element_located((selector_type, selector_value))
                    )
                    self.logger.info(f"Campo de búsqueda encontrado con selector: {selector_type}={selector_value}")
                    break
                except:
                    self.logger.debug(f"Selector {selector_type}={selector_value} no funcionó")
                    continue
            
            if not search_input:
                # Intentar obtener información de la página actual para debug
                current_url = self.driver.current_url
                page_title = self.driver.title
                self.logger.error(f"No se encontró campo de búsqueda. URL: {current_url}, Título: {page_title}")
                
                # Intentar navegar directamente a la página principal y luego buscar
                self.logger.info("Intentando navegar a página principal...")
                self.driver.get(self.base_url)
                time.sleep(3)
                
                # Buscar formulario de búsqueda en la página principal
                for selector_type, selector_value in selectors:
                    try:
                        search_input = WebDriverWait(self.driver, 5).until(
                            EC.presence_of_element_located((selector_type, selector_value))
                        )
                        self.logger.info(f"Campo de búsqueda encontrado en página principal: {selector_type}={selector_value}")
                        break
                    except:
                        continue
                
                if not search_input:
                    raise Exception("No se pudo localizar el campo de búsqueda en ninguna página")
            
            # Limpiar y escribir
            search_input.clear()
            search_input.send_keys(query)
            self.logger.info(f"Texto '{query}' ingresado en campo de búsqueda")
            
            # Buscar botón de búsqueda con múltiples selectores
            search_btn = None
            button_selectors = [
                (By.CSS_SELECTOR, "a[onclick*='dosearch3s']"),  # Específico para Hispashare
                (By.CSS_SELECTOR, "a[onclick*='dosearch']"),
                (By.CSS_SELECTOR, "button[type='submit']"),
                (By.CSS_SELECTOR, "input[type='submit']"),
                (By.XPATH, "//button[contains(text(), 'Buscar') or contains(text(), 'Search')]"),
                (By.XPATH, "//a[contains(text(), 'Buscar') or contains(text(), 'Search')]")
            ]
            
            button_clicked = False
            for selector_type, selector_value in button_selectors:
                try:
                    search_btn = WebDriverWait(self.driver, 3).until(
                        EC.element_to_be_clickable((selector_type, selector_value))
                    )
                    self.logger.info(f"Botón de búsqueda encontrado y clickeable: {selector_type}={selector_value}")
                    search_btn.click()
                    button_clicked = True
                    self.logger.info("Botón de búsqueda clickeado exitosamente")
                    break
                except Exception as e:
                    self.logger.debug(f"Selector {selector_type}={selector_value} falló: {e}")
                    continue
            
            if not button_clicked:
                self.logger.warning("No se pudo hacer clic en botón de búsqueda, intentando métodos alternativos...")
                # Intentar hacer clic usando JavaScript
                try:
                    # Primero intentar ejecutar directamente la función dosearch3s()
                    try:
                        self.driver.execute_script("dosearch3s();")
                        self.logger.info("Función dosearch3s() ejecutada directamente con JavaScript")
                        button_clicked = True
                    except Exception as js_error:
                        self.logger.debug(f"Ejecución directa de dosearch3s() falló: {js_error}")
                        
                        # Si falla, intentar hacer clic en el elemento
                        for selector_type, selector_value in button_selectors:
                            try:
                                if selector_type == By.CSS_SELECTOR:
                                    element = self.driver.find_element(selector_type, selector_value)
                                    self.driver.execute_script("arguments[0].click();", element)
                                    self.logger.info(f"Botón clickeado con JavaScript: {selector_value}")
                                    button_clicked = True
                                    break
                            except:
                                continue
                except Exception as e:
                    self.logger.debug(f"JavaScript click falló: {e}")
            
            if not button_clicked:
                # Última alternativa: enviar con Enter
                self.logger.info("No se pudo hacer clic en botón, enviando búsqueda con Enter")
                search_input.send_keys(Keys.RETURN)
                self.logger.info("Tecla Enter enviada exitosamente")
            
            # Esperar a que se procese la búsqueda
            self.logger.info("Esperando que se procese la búsqueda...")
            time.sleep(2)
            
        except Exception as e:
            self.logger.error(f"Error en perform_search: {e}")
            # Agregar información adicional para debug
            try:
                current_url = self.driver.current_url
                page_source_snippet = self.driver.page_source[:500] if self.driver.page_source else "No page source"
                self.logger.error(f"URL actual: {current_url}")
                self.logger.error(f"Snippet de página: {page_source_snippet}")
            except:
                pass
            raise
    
    def extract_results(self):
        """Extraer todos los resultados de búsqueda"""
        results = []
        
        try:
            self.logger.info("Esperando resultados de búsqueda...")
            # Esperar a que aparezcan resultados
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "tr.E, tr.O"))
            )
            self.logger.info("Resultados encontrados, extrayendo...")
            
            # Obtener todas las filas de resultados
            rows = self.driver.find_elements(By.CSS_SELECTOR, "tr.E, tr.O")
            self.logger.info(f"Encontradas {len(rows)} filas de resultados")
            
            for i, row in enumerate(rows):
                try:
                    self.logger.debug(f"Procesando fila {i+1}/{len(rows)}")
                    result = self.parse_result_row(row)
                    if result:
                        results.append(result)
                        self.logger.debug(f"Resultado válido: {result['title']}")
                    else:
                        self.logger.debug(f"Fila {i+1} no es una serie válida")
                except Exception as e:
                    self.logger.debug(f"Error parseando fila {i+1}: {e}")
                    continue
            
            self.logger.info(f"Se encontraron {len(results)} resultados válidos")
            
        except Exception as e:
            self.logger.error(f"Error extrayendo resultados: {e}")
            # Añadir información de debug
            try:
                current_url = self.driver.current_url
                page_title = self.driver.title
                self.logger.error(f"URL actual: {current_url}")
                self.logger.error(f"Título de página: {page_title}")
                
                # Verificar si hay algún elemento en la página
                body_text = self.driver.find_element(By.TAG_NAME, "body").text[:500]
                self.logger.error(f"Contenido de página (primeros 500 chars): {body_text}")
            except Exception as debug_error:
                self.logger.error(f"Error obteniendo información de debug: {debug_error}")
        
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
        threshold = 0.50  # Reducido de 0.75 a 0.50 para ser más permisivo
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