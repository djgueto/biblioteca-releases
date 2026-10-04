from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException
from difflib import SequenceMatcher
import re
import logging
import time
import unicodedata

# Common Spanish→English article/preposition equivalences for cross-language matching
_LANG_EQUIV = {
    'los': 'the', 'las': 'the', 'el': 'the', 'la': 'the',
    'un': 'a', 'una': 'a',
    'y': 'and', 'e': 'and',
    'o': 'or',
    'de': 'of', 'del': 'of',
    'en': 'in',
}

# Stop words to ignore during fuzzy token matching (Spanish + English)
_STOP_WORDS = frozenset({
    'el', 'la', 'los', 'las', 'un', 'una', 'unos', 'unas',
    'y', 'e', 'o', 'u', 'de', 'del', 'en', 'a', 'con', 'por', 'para', 'sin',
    'the', 'an', 'and', 'or', 'of', 'in', 'on', 'at', 'to', 'for', 'by', 'with', 'from',
})

# Palabras que Hispashare añade a un título sin cambiar la obra ("Angry Birds 2:
# La película"). No cuentan como diferencia al medir contención de títulos.
_GENERIC_TITLE_WORDS = frozenset({'pelicula', 'movie', 'film', 'documental', 'miniserie'})

# Similitud de título mínima (0-1) para aceptar un resultado, independientemente
# del score total: año y recencia pueden sumar hasta 0.40 por sí solos, así que
# sin este mínimo un título a medias ("La caja" -> "La caja de cristal")
# superaba el threshold si no había un resultado mejor en la lista.
_MIN_TITLE_SIMILARITY = 0.75


class HispashareSearcher:
    def __init__(self, driver, db_manager=None):
        self.driver = driver
        self.base_url = "https://www.hispashare.org"
        self.logger = logging.getLogger(__name__)
        self.db_manager = db_manager
    
    def search_and_select(self, series_name, tmdb_year=None, series_id=None, content_mode='episodic'):
        """
        Busca serie y selecciona mejor resultado automáticamente
        
        Args:
            series_name: Nombre de la serie
            tmdb_year: Año de TMDB (opcional)
            series_id: ID de la serie en la base de datos (opcional)
        
        Returns:
            str: URL completa del resultado seleccionado o None
        """
        best_result = self.search_best_result(
            series_name,
            tmdb_year,
            series_id,
            content_mode=content_mode,
        )
        return best_result['url'] if best_result else None

    def search_best_result(self, series_name, tmdb_year=None, series_id=None, content_mode='episodic'):
        try:
            # Try the declared content_mode first; if it yields no results at all,
            # fall back to the opposite mode.  This handles items whose
            # content_category is missing/wrong in the DB (e.g. a movie stored
            # without a category, searched as 'episodic', gets zero hits).
            fallback_mode = 'single_file' if content_mode == 'episodic' else 'episodic'
            modes_to_try = [content_mode, fallback_mode]

            for attempt_mode in modes_to_try:
                result = self._search_with_mode(
                    series_name, tmdb_year, series_id, attempt_mode
                )
                if result is not None:
                    return result
                if attempt_mode != content_mode:
                    # Fallback also failed — give up
                    break

            self.logger.warning(f"No se encontraron resultados para: {series_name}")
            self._disable_future_search(series_name, series_id, "sin resultados")
            return None

        except Exception as e:
            self.logger.error(f"Error en search_best_result: {e}", exc_info=True)
            return None

    def _search_with_mode(self, series_name, tmdb_year, series_id, content_mode):
        """Run all query candidates for one content_mode.

        Returns the best matching result dict if found, or None.
        Returns the sentinel value False (falsy but distinct from None) is NOT
        used — None means either 'no results at all' or 'results below threshold'.
        The caller interprets None as 'try next mode'.
        """
        query_candidates = self.build_query_candidates(series_name)
        aggregated_results = {}
        rejected = self._rejected_keys(series_id)
        # Aquí y no arriba: core.repositories importa este módulo (import circular).
        from core.repositories.source_repository import source_key

        for query in query_candidates:
            self.logger.info(
                f"[{content_mode}] Buscando en Hispashare: '{query}'"
            )
            self.perform_search(query)
            time.sleep(2)

            results = self.extract_results(content_mode)
            for result in results:
                if rejected and source_key(result['url']) in rejected:
                    continue  # el usuario dijo que esta ficha no es
                aggregated_results[result['url']] = result

            if not aggregated_results:
                continue

            best_result = self.select_best_match(
                list(aggregated_results.values()),
                series_name,
                tmdb_year,
                content_mode,
                query_candidates,
            )

            if best_result:
                if self.db_manager and series_id:
                    try:
                        self.db_manager.enable_series_search(series_id)
                    except Exception as exc:
                        self.logger.debug(
                            f"No se pudo resetear estado de búsqueda para {series_name}: {exc}"
                        )
                self.logger.info(
                    f"Mejor match [{content_mode}]: {best_result['title']} "
                    f"(Score: {best_result.get('score', 0):.2%})"
                )
                return best_result

        if not aggregated_results:
            if content_mode == 'episodic':
                self.logger.info(
                    f"Sin resultados en modo 'episodic' para '{series_name}', "
                    f"probando modo 'single_file'..."
                )
            else:
                self.logger.info(
                    f"Sin resultados en modo 'single_file' para '{series_name}', "
                    f"probando modo 'episodic'..."
                )
        else:
            self.logger.warning(
                f"[{content_mode}] No se encontró un match adecuado para: {series_name}"
            )
        return None

    def _rejected_keys(self, series_id):
        if not self.db_manager or not series_id:
            return set()
        try:
            from core.repositories.source_repository import SourceRepository

            return SourceRepository(self.db_manager).rejected_keys(series_id)
        except Exception as exc:
            self.logger.debug(f"No se pudieron leer las fichas rechazadas de {series_id}: {exc}")
            return set()

    def _strip_accents(self, value):
        normalized = unicodedata.normalize("NFKD", value or "")
        return "".join(ch for ch in normalized if not unicodedata.combining(ch))

    def normalize_title_for_matching(self, value):
        text = self._strip_accents(value or "").lower().strip()
        text = re.sub(r'\[.*?\]|\(.*?\)', ' ', text)
        text = re.sub(r'\bserie(?:s)?\b', ' ', text)
        text = re.sub(r'\btv\b', ' ', text)
        text = re.sub(r'\btemporad[ao]s?\b', ' ', text)
        text = re.sub(r'\bseason\b', ' ', text)
        text = re.sub(r'\bcomplete\b|\bcompleta\b|\bcompleto\b|\bpack\b', ' ', text)
        text = re.sub(r'[^a-z0-9]+', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()
        return text

    def _translate_words(self, text):
        """Replace Spanish articles/prepositions with English equivalents."""
        return ' '.join(_LANG_EQUIV.get(w, w) for w in text.split())

    def _remove_stop_words(self, text):
        """Remove Spanish and English stop words from a normalized text."""
        return ' '.join(w for w in text.split() if w not in _STOP_WORDS)

    def _word_sim(self, a, b):
        """Fuzzy similarity between two normalized words.

        Handles common suffix differences (singular/plural, gendered endings)
        so that e.g. 'simpson' and 'simpsons' score 0.92 instead of ~0.86.
        """
        if a == b:
            return 1.0
        # Tokens of 2 chars or less (e.g. roman numeral 'v', 'x') are unreliable
        # for character-level fuzzy matching: SequenceMatcher gives 'v' vs 'nueva'
        # a 0.33 ratio purely from incidental letter overlap despite being
        # unrelated words. Require an exact match for these.
        if min(len(a), len(b)) <= 2:
            return 0.0
        # Prefix containment with short suffix (e.g. 'simpson'/'simpsons', 'serie'/'series')
        shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
        if longer.startswith(shorter) and len(longer) - len(shorter) <= 3:
            return 0.92
        # Same problem one size up: 'uco' vs 'cuco' gives 0.86 from three
        # shared letters. Below 5 chars fuzzy similarity is noise, not a
        # spelling variant.
        if len(shorter) < 5:
            return 0.0
        return SequenceMatcher(None, a, b).ratio()

    def token_match_score(self, candidate_norm, result_norm):
        """Cross-language token-level fuzzy match score (0–1).

        Steps:
        1. Translate Spanish articles/prepositions in the candidate to English.
        2. Remove stop words from both sides.
        3. For each remaining token compute the best fuzzy word match.
        4. Combine into the F1 of precision and recall across all tokens,
           then dampen by the worst-matching candidate word so that a title
           which only coincidentally shares one word with an otherwise
           unrelated result (e.g. 'Generación V' vs 'Star Trek: La Nueva
           Generación', which share 'generación') can't still land a high
           average score.

        This handles cases like 'fortnite x los simpson' vs
        'fortnite x the simpsons' where the article differs (los/the) and
        the noun has a plural suffix (simpson/simpsons).
        """
        candidate_transl = self._translate_words(candidate_norm)
        # Las palabras genéricas ('película') tampoco deben sumar coincidencia:
        # 'La Patrulla Canina: La película' vs '... La dino película' puntuaba
        # 0.81 gracias a compartir 'película'.
        c_words = [w for w in self._remove_stop_words(candidate_transl).split() if w not in _GENERIC_TITLE_WORDS]
        r_words = [w for w in self._remove_stop_words(result_norm).split() if w not in _GENERIC_TITLE_WORDS]

        if not c_words or not r_words:
            return 0.0

        per_candidate_best = [
            max((self._word_sim(cw, rw) for rw in r_words), default=0.0)
            for cw in c_words
        ]
        precision = sum(per_candidate_best) / len(c_words)

        per_result_best = [
            max((self._word_sim(rw, cw) for cw in c_words), default=0.0)
            for rw in r_words
        ]
        recall = sum(per_result_best) / len(r_words)

        if precision + recall == 0:
            return 0.0
        f1 = 2 * precision * recall / (precision + recall)

        # Simétrico: una palabra significativa de la ficha que no está en el
        # título buscado ('dino' en 'La Patrulla Canina: La dino película')
        # también delata otra obra.
        min_word_match = min(min(per_candidate_best), min(per_result_best))
        score = f1 * (0.5 + 0.5 * min_word_match)

        # Mismas palabras en otro orden suele ser otra obra ('Black Beauty' vs
        # 'Beauty in Black'), no una traducción: penalizar el orden invertido.
        positions = []
        for cw in c_words:
            best_idx, best_sim = None, 0.0
            for idx, rw in enumerate(r_words):
                sim = self._word_sim(cw, rw)
                if sim > best_sim:
                    best_idx, best_sim = idx, sim
            if best_sim >= 0.8:
                positions.append(best_idx)
        if any(b < a for a, b in zip(positions, positions[1:])):
            score *= 0.7

        return score

    def build_comparison_titles(self, series_name):
        """Variantes del título con las que PUNTUAR un resultado.

        Igual que build_query_candidates pero sin los trozos separados por
        ':' / ' - ': 'Mulán: Enciende el fuego' genera la consulta 'Mulán',
        útil para buscar, pero puntuar con ella daba 1.0 contra la ficha de
        'Mulán' (2020) y robaba su URL. Lo mismo con 'My Hero Academia:
        Vigilantes' o cualquier spin-off de una franquicia."""
        return self.build_query_candidates(series_name, include_parts=False)

    def build_query_candidates(self, series_name, include_parts=True):
        raw = (series_name or "").strip()
        candidates = []

        def add_candidate(value):
            candidate = re.sub(r'\s+', ' ', (value or "").strip())
            if candidate and candidate not in candidates:
                candidates.append(candidate)

        # Se prueba primero SIN paréntesis (año, calidad, etc.): un texto de
        # búsqueda como "13 Exorcismos (2022)" puede no devolver nada en el
        # buscador de Hispashare por culpa del paréntesis/año literal, aunque
        # el título sí exista en el catálogo. El año no debe condicionar qué
        # se manda al cuadro de búsqueda -- solo se usa después, en
        # calculate_result_score, para afinar qué resultado es el correcto.
        add_candidate(re.sub(r'\(.*?\)', '', raw).strip())
        add_candidate(raw)

        def is_specific_enough(value):
            """Un fragmento de subtítulo genérico ('La película', 'La serie')
            no debe aportar por sí solo un candidato de contención: tras
            quitar stopwords casi no queda nada distintivo, y esa misma
            frase genérica aparece por casualidad en títulos de series u
            obras completamente distintas (p. ej. 'Angry Birds 2: La
            película' calzando por contención con 'Phineas y Ferb, la
            película: ...'). Se exige al menos 2 palabras significativas."""
            significant = [w for w in self.normalize_title_for_matching(value).split() if w not in _STOP_WORDS]
            return len(significant) >= 2

        for separator in (':', ' - ', ' | ', ' / '):
            if include_parts and separator in raw:
                parts = [part.strip() for part in raw.split(separator) if part.strip()]
                for idx, part in enumerate(parts):
                    # La primera parte (el título principal, antes del primer
                    # separador) se admite siempre aunque sea corta -- es la
                    # que de verdad distingue la obra ("Angry Birds 2").
                    if idx == 0 or is_specific_enough(part):
                        add_candidate(part)

        normalized = self.normalize_title_for_matching(raw)
        if normalized:
            add_candidate(normalized)

        # Stop-word-free variant: removes 'los', 'the', 'de', 'of', etc.
        # Helps when the language of the stored name differs from Hispashare.
        # E.g. "Fortnite x Los Simpson" → "fortnite x simpson"
        if normalized:
            no_stops = self._remove_stop_words(normalized)
            if no_stops and no_stops != normalized:
                add_candidate(no_stops)

        # Translated variant: converts Spanish articles to English equivalents
        # and then removes stop words.
        # E.g. "Fortnite x Los Simpson" → normalized → translate "los"→"the" → strip "the" → "fortnite x simpson"
        if normalized:
            translated = self._translate_words(normalized)
            no_stops_translated = self._remove_stop_words(translated)
            if no_stops_translated and no_stops_translated not in candidates:
                add_candidate(no_stops_translated)

        return candidates

    def _disable_future_search(self, series_name, series_id, reason):
        """Registrar fallo de búsqueda y deshabilitar solo tras varios intentos."""
        if not self.db_manager or not series_id:
            return

        try:
            failure_state = self.db_manager.register_search_failure(series_id, disable_after=3)
            failures = failure_state.get("search_failures", 0)
            disabled = bool(failure_state.get("search_disabled", 0))
            if disabled:
                self.logger.info(
                    f"Serie '{series_name}' marcada para no buscar más automáticamente "
                    f"tras {failures} fallos ({reason})."
                )
            else:
                self.logger.info(
                    f"Serie '{series_name}' mantiene búsqueda activa tras fallo {failures}/3 ({reason})."
                )
        except Exception as exc:
            self.logger.error(
                f"No se pudo marcar la serie '{series_name}' para no buscar más: {exc}"
            )
    
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
    
    def extract_results(self, content_mode='episodic'):
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
                    result = self.parse_result_row(row, content_mode)
                    if result:
                        results.append(result)
                        self.logger.debug(f"Resultado válido: {result['title']}")
                    else:
                        self.logger.debug(f"Fila {i+1} no es una serie válida")
                except Exception as e:
                    self.logger.debug(f"Error parseando fila {i+1}: {e}")
                    continue
            
            self.logger.info(f"Se encontraron {len(results)} resultados válidos")

        except TimeoutException:
            # La tabla de resultados nunca apareció: esto es la señal habitual
            # de "sin resultados" en Hispashare, no un error de red/driver.
            self.logger.info("No aparecieron filas de resultados (probablemente sin resultados)")
        except WebDriverException as e:
            # Fallo real del navegador/conexión (driver caído, timeout de red, etc.):
            # NO es equivalente a "sin resultados" y no debe contar como tal para
            # el contador de fallos de búsqueda. Se propaga para que el llamador
            # lo trate como un error, no como "no encontrado".
            self.logger.error(f"Error de WebDriver extrayendo resultados: {e}")
            try:
                self.logger.error(f"URL actual: {self.driver.current_url}")
                self.logger.error(f"Título de página: {self.driver.title}")
            except Exception:
                pass
            raise

        return results
    
    def parse_result_row(self, row, content_mode='episodic'):
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
                
                is_series = "Series >" in genre_text

                if content_mode == 'episodic' and not is_series:
                    return None

                if content_mode == 'single_file' and is_series:
                    return None
                
            except:
                # Si no encontramos género, asumir que podría ser valido
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
    
    def select_best_match(self, results, series_name, tmdb_year, content_mode='episodic', query_candidates=None):
        """Seleccionar mejor resultado automáticamente"""
        if not results:
            return None
        
        scored_results = []
        # query_candidates (con trozos de título) ya no se mezclan aquí: ver
        # build_comparison_titles. El parámetro se mantiene por compatibilidad.
        comparison_titles = self.build_comparison_titles(series_name)

        if content_mode == 'single_file' and tmdb_year:
            # Una película con el mismo título y otro año es otra película
            # ('Dogman' 2018 vs 2023). En series no se aplica: el año de la
            # ficha puede ser el de la última temporada publicada.
            same_era = [r for r in results if not r.get('year') or abs(r['year'] - tmdb_year) <= 1]
            if len(same_era) < len(results):
                self.logger.debug(
                    f"Descartados {len(results) - len(same_era)} resultados con año a más de 1 de {tmdb_year}"
                )
            results = same_era
            if not results:
                return None

        for result in results:
            score = self.calculate_result_score(result, comparison_titles, tmdb_year)
            result['score'] = score
            scored_results.append((result, score))
            
            self.logger.debug(
                f"  {result['title']}: {score:.2%}"
            )
        
        # Ordenar por score
        scored_results.sort(key=lambda x: x[1], reverse=True)
        best_result, best_score = scored_results[0]

        # Solo retornar si score >= threshold
        threshold = 0.42 if content_mode == 'episodic' else 0.40
        title_similarity = best_result.get('title_similarity', 0.0)
        if title_similarity < _MIN_TITLE_SIMILARITY:
            self.logger.warning(
                f"Mejor resultado '{best_result['title']}' descartado: similitud de título "
                f"({title_similarity:.2%}) por debajo del mínimo ({_MIN_TITLE_SIMILARITY:.2%})"
            )
            return None
        if best_score >= threshold:
            return best_result
        else:
            self.logger.warning(
                f"Mejor score ({best_score:.2%}) por debajo del threshold ({threshold:.2%})"
            )
            return None

    def _result_title_variants(self, title):
        """Títulos normalizados con los que comparar un resultado.

        Hispashare suele listar 'Título español / Título original (Director,
        año)': cada alternativa se compara por separado, además del título
        completo, para que la contención se mida contra una obra y no contra
        la concatenación de dos nombres."""
        variants = []
        full = self.normalize_title_for_matching(title)
        if full:
            variants.append(full)
        for part in re.sub(r'\(.*?\)|\[.*?\]', ' ', title or '').split(' / '):
            normalized = self.normalize_title_for_matching(part)
            if normalized and normalized not in variants:
                variants.append(normalized)
        return variants

    def _containment_similarity(self, candidate, result_title):
        """Similitud cuando un título contiene al otro como secuencia de
        palabras completas; None si no hay contención.

        Antes se usaba contención de caracteres ('uco' in 'el cuco', 'air' in
        '... maestro del aire') y siempre valía 0.94, así que 'La caja'
        emparejaba con 'La caja de cristal' y 'Kung Fu Panda' con cualquier
        película de la saga. Ahora se escala por la fracción de palabras
        significativas que cubre el título más corto."""
        padded_c, padded_r = f" {candidate} ", f" {result_title} "
        if padded_c not in padded_r and padded_r not in padded_c:
            return None

        def significant(text):
            return [
                w for w in text.split()
                if w not in _STOP_WORDS and w not in _GENERIC_TITLE_WORDS
            ]

        shorter, longer = sorted((candidate, result_title), key=len)
        longer_words = significant(longer)
        if not longer_words:
            return 0.94
        coverage = min(1.0, len(significant(shorter)) / len(longer_words))
        return 0.94 * coverage
    
    def calculate_result_score(self, result, comparison_titles, tmdb_year):
        """Calcular score de un resultado"""
        score = 0.0

        result_titles = self._result_title_variants(result['title'])
        normalized_candidates = [
            self.normalize_title_for_matching(title)
            for title in (comparison_titles or [])
            if self.normalize_title_for_matching(title)
        ]
        if not normalized_candidates:
            normalized_candidates = list(result_titles)

        # 1. Similitud de título (60%)
        title_similarity = 0.0
        for candidate in normalized_candidates:
            for result_title_normalized in result_titles:
                if candidate == result_title_normalized:
                    similarity = 1.0
                else:
                    similarity = self._containment_similarity(candidate, result_title_normalized)
                if similarity is None:
                    # SequenceMatcher.ratio() over the full strings is
                    # deliberately NOT used here. For non-exact,
                    # non-containment pairs it is unreliable: a short
                    # candidate that shares one real word with an unrelated
                    # long title (e.g. "Generación V" vs "Star Trek: La Nueva
                    # Generación") scored ~0.49 from pure character overlap
                    # and used to leak through as the "best" similarity,
                    # causing wrong Hispashare matches.
                    similarity = 0.0
                    candidate_words = set(candidate.split())
                    result_words = set(result_title_normalized.split())
                    if candidate_words and result_words:
                        overlap = len(candidate_words & result_words) / max(len(candidate_words), len(result_words))
                        similarity = max(similarity, overlap * 0.9)
                    # Cross-language fuzzy token match: handles article translation
                    # (los→the) and suffix differences (simpson→simpsons)
                    token_score = self.token_match_score(candidate, result_title_normalized)
                    similarity = max(similarity, token_score * 0.95)
                title_similarity = max(title_similarity, similarity)
        result['title_similarity'] = title_similarity
        score += title_similarity * 0.6
        
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
            
            score += year_score * 0.25
        
        # 3. Recencia de publicación (15%)
        recency_score = self.calculate_recency_score(result['last_publication'])
        score += recency_score * 0.15
        
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
