import os
import re
import time
import logging
from pathlib import Path
from bs4 import BeautifulSoup
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from ..library.parser import EpisodeParser
from ..emule.client import EmuleClient
from config.settings import Settings


class ContentScanner:
    """
    Escáner automático de nuevo contenido en Hispashare con filtros específicos
    """
    
    def __init__(self, driver, db_manager, should_stop=None):
        self.driver = driver
        self.db_manager = db_manager
        self.logger = logging.getLogger(__name__)
        self.parser = EpisodeParser()
        self.emule_client = EmuleClient()
        self.settings = Settings()
        self.should_stop = should_stop or (lambda: False)
        
        # URLs de búsqueda por páginas (dinámico basado en configuración)
        num_pages = self.settings.CONTENT_DISCOVERY_PAGES
        self.search_urls = [
            f"https://www.hispashare.org/?page={i}&view=search&order=y&direction=D"
            for i in range(1, num_pages + 1)
        ]
        
        from datetime import datetime
        current_year = datetime.now().year
        
        # Filtros de contenido
        self.filters = {
            'genres': ['Películas', 'Series', 'Música', 'Documentales'],
            'preferred_formats': ['2160p', '1080p'],  # Preferir 2160p sobre 1080p
            'preferred_codecs': ['x265', 'x264'],     # Preferir x265 sobre x264
            'preferred_quality': ['hdr', 'dolby vision'],  # Preferir HDR
            'languages': ['spanish', 'catalan'],      # Solo español y catalán
            'year': current_year                      # Solo contenido del año actual
        }
        self.category_profiles = self._build_category_profiles()

    def _build_category_profiles(self):
        return {
            'series': {
                'label': Settings.get_category_label('series'),
                'content_mode': 'episodic',
                'genre_tokens': ['series >'],
                'keywords': [],
            },
            'cartoons': {
                'label': Settings.get_category_label('cartoons'),
                'content_mode': 'episodic',
                'genre_tokens': ['series >'],
                'keywords': ['animacion', 'animación', 'anime', 'dibujos'],
            },
            'docuseries': {
                'label': Settings.get_category_label('docuseries'),
                'content_mode': 'episodic',
                'genre_tokens': ['series >', 'documentales >'],
                'keywords': ['docuserie', 'documental'],
            },
            'movies': {
                'label': Settings.get_category_label('movies'),
                'content_mode': 'single_file',
                'genre_tokens': ['películas >'],
                'keywords': [],
            },
            'animated_movies': {
                'label': Settings.get_category_label('animated_movies'),
                'content_mode': 'single_file',
                'genre_tokens': ['películas >'],
                'keywords': ['animacion', 'animación', 'anime'],
            },
            'documentaries': {
                'label': Settings.get_category_label('documentaries'),
                'content_mode': 'single_file',
                'genre_tokens': ['documentales >'],
                'keywords': ['documental'],
            },
            'concerts': {
                'label': Settings.get_category_label('concerts'),
                'content_mode': 'single_file',
                'genre_tokens': ['música >', 'musica >'],
                'keywords': ['concierto', 'concert', 'live'],
            },
            'music': {
                'label': Settings.get_category_label('music'),
                'content_mode': 'single_file',
                'genre_tokens': ['música >', 'musica >'],
                'keywords': [],
            },
        }
    
    def scan_new_content(self):
        """
        Escanea las páginas de Hispashare buscando nuevo contenido que cumpla los filtros
        
        Returns:
            list: Lista de contenido encontrado que cumple los filtros
        """
        self.logger.info("🔍 Iniciando barrido de nuevo contenido en Hispashare...")
        
        all_content = []
        
        for page_num, url in enumerate(self.search_urls, 1):
            if self.should_stop():
                self.logger.info("🛑 Barrido cancelado por solicitud de parada")
                break
            self.logger.info(f"📄 Escaneando página {page_num}: {url}")
            
            try:
                content_items = self._scan_page(url)
                categorized_items = self._categorize_content_items(content_items)
                filtered_items = self._apply_filters(categorized_items)
                
                self.logger.info(f"   📊 Página {page_num}: {len(content_items)} elementos encontrados, {len(filtered_items)} pasaron filtros")
                
                all_content.extend(filtered_items)
                
            except Exception as e:
                if self.should_stop():
                    self.logger.info("🛑 Barrido cancelado por solicitud de parada")
                    break
                self.logger.error(f"❌ Error escaneando página {page_num}: {e}")
                continue
        
        # Verificar duplicados en bibliotecas existentes
        unique_content = self._check_existing_content(all_content)

        for category in Settings.get_library_categories():
            category_items = [item for item in unique_content if item.get('library_category') == category]
            if category_items:
                self.logger.info(
                    f"   📚 {Settings.get_category_label(category)}: {len(category_items)} candidatos nuevos"
                )
        
        self.logger.info(f"✅ Barrido completado: {len(unique_content)} elementos únicos encontrados")
        
        return unique_content

    def _categorize_content_items(self, content_items):
        categorized = []
        for item in content_items:
            category = self._classify_library_category(item)
            if not category:
                continue
            item['library_category'] = category
            item['content_mode'] = Settings.get_category_content_mode(category)
            categorized.append(item)
        return categorized
    
    def _scan_page(self, url):
        """
        Escanea una página específica de Hispashare
        
        Args:
            url: URL de la página a escanear
            
        Returns:
            list: Lista de elementos de contenido encontrados
        """
        try:
            if self.should_stop():
                return []

            for attempt in range(1, 4):
                if self.should_stop():
                    return []
                try:
                    self.driver.get(url)
                    break
                except Exception as e:
                    if self.should_stop():
                        return []
                    if attempt >= 3:
                        raise
                    time.sleep(1.5 * attempt)
            
            # Esperar a que la página cargue
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.CLASS_NAME, "WIN1_CONTAINER"))
            )
            
            # Obtener el HTML de la página
            page_source = self.driver.page_source
            soup = BeautifulSoup(page_source, 'html.parser')
            
            # Buscar todas las filas de contenido (tr con clases E u O)
            content_rows = soup.find_all('tr', class_=['E', 'O'])
            
            content_items = []
            
            for row in content_rows:
                try:
                    item = self._parse_content_row(row)
                    if item:
                        content_items.append(item)
                except Exception as e:
                    self.logger.warning(f"Error parseando fila de contenido: {e}")
                    continue
            
            return content_items
            
        except Exception as e:
            if self.should_stop():
                return []
            self.logger.error(f"Error escaneando página {url}: {e}")
            return []
    
    def _parse_content_row(self, row):
        """
        Parsea una fila de contenido de Hispashare
        
        Args:
            row: Elemento BeautifulSoup de la fila
            
        Returns:
            dict: Información del contenido o None si no es válido
        """
        try:
            # Obtener el enlace principal del título desde la celda R1
            r1_cell = row.find('td', class_='R1')
            if not r1_cell:
                return None
                
            title_h1 = r1_cell.find('h1')
            if not title_h1:
                return None
                
            title_link = title_h1.find('a')
            if not title_link:
                return None
            
            title = title_link.get_text(strip=True)
            content_url = "https://www.hispashare.org/" + title_link.get('href')
            
            # Extraer año del título (formato: "Título (Director, 2025)" o "Título (2025)")
            year_match = re.search(r'\(.*?(\d{4})\)', title)
            year = int(year_match.group(1)) if year_match else None
            
            # Extraer puntuación de las estrellas
            rating = None
            stars_container = r1_cell.find('div', class_='STARS_CONTAINER')
            if stars_container:
                # Buscar el texto que contiene la puntuación (formato: "&nbsp;X.X")
                stars_text = stars_container.get_text(strip=True)
                rating_match = re.search(r'(\d+\.\d+)', stars_text)
                if rating_match:
                    try:
                        rating = float(rating_match.group(1))
                    except ValueError:
                        rating = None
            
            # Obtener información de los párrafos INFO
            info_paragraphs = r1_cell.find_all('p', class_='INFO')
            
            genre_text = ""
            format_info = ""
            quality_info = ""
            language_info = ""
            
            for p in info_paragraphs:
                text = p.get_text(strip=True)
                if text.startswith('Género:'):
                    genre_text = text
                elif text.startswith('Formato:'):
                    format_info = text
                elif text.startswith('Calidad:'):
                    quality_info = text
                elif text.startswith('Idiomas:'):
                    # Para idiomas, extraer los atributos alt/title de las imágenes de banderas
                    language_flags = []
                    flag_images = p.find_all('img', class_='FLAGM')
                    for img in flag_images:
                        alt_text = img.get('alt', '')
                        title_text = img.get('title', '')
                        if alt_text:
                            language_flags.append(alt_text)
                        elif title_text:
                            language_flags.append(title_text)
                    language_info = "Idiomas: " + ", ".join(language_flags)
            
            return {
                'title': title,
                'url': content_url,
                'year': year,
                'rating': rating,
                'genre': genre_text,
                'format': format_info,
                'quality': quality_info,
                'languages': language_info
            }
            
        except Exception as e:
            self.logger.warning(f"Error parseando fila: {e}")
            return None
    
    def _apply_filters(self, content_items):
        """
        Aplica los filtros de contenido a la lista de elementos
        
        Args:
            content_items: Lista de elementos de contenido
            
        Returns:
            list: Elementos que pasan todos los filtros
        """
        filtered_items = []
        
        for item in content_items:
            if self._passes_filters(item):
                filtered_items.append(item)
        
        return filtered_items
    
    def _passes_filters(self, item):
        """
        Verifica si un elemento pasa todos los filtros
        
        Args:
            item: Elemento de contenido
            
        Returns:
            bool: True si pasa todos los filtros
        """
        try:
            category = item.get('library_category')
            category_label = Settings.get_category_label(category) if category else "Sin categoría"
            self.logger.info(f"🔍 Evaluando filtros para [{category_label}]: {item['title'][:50]}...")
            self.logger.info(f"   Año: {item['year']} (requerido: {self.filters['year']})")
            self.logger.info(f"   Puntuación: {item.get('rating', 'N/A')} (mínimo: {self.settings.CONTENT_SCAN_MIN_RATING})")
            self.logger.info(f"   Género: {item['genre'][:100]}...")
            self.logger.info(f"   Formato: {item['format'][:100]}...")
            self.logger.info(f"   Calidad: {item['quality'][:100]}...")
            self.logger.info(f"   Idiomas: {item['languages'][:100]}...")
            
            if not category:
                self.logger.info("   ❌ No se pudo clasificar en una biblioteca")
                return False

            profile = self.category_profiles.get(category, {})
            genre_text = item['genre'].lower()
            combined_text = f"{genre_text} {(item.get('title') or '').lower()}"
            if not self._matches_category_profile(profile, genre_text, combined_text):
                self.logger.info(f"   ❌ El contenido no encaja con la biblioteca {category_label}")
                return False

            # Filtro de puntuación mínima
            rating = item.get('rating')
            if rating is None:
                self.logger.info(f"   ❌ Sin puntuación disponible")
                return False
            elif rating < self.settings.CONTENT_SCAN_MIN_RATING:
                self.logger.info(f"   ❌ Puntuación muy baja: {rating} < {self.settings.CONTENT_SCAN_MIN_RATING}")
                return False
            else:
                self.logger.info(f"   ✅ Puntuación aceptable: {rating} >= {self.settings.CONTENT_SCAN_MIN_RATING}")
            
            # Filtro de año (solo 2025)
            if item['year'] != self.filters['year']:
                self.logger.info(f"   ❌ Año no coincide: {item['year']} != {self.filters['year']}")
                return False
            
            # Filtro de idioma (debe contener español o catalán, puede tener otros idiomas adicionales)
            language_text = item['languages'].lower()
            language_found = False
            
            # Verificar idiomas aceptables (español, catalán) - prioritario
            for lang in self.filters['languages']:
                if lang in language_text or 'spanish' in language_text or 'catala' in language_text or 'spanishfor' in language_text:
                    language_found = True
                    self.logger.info(f"   ✅ Idioma encontrado: {lang}")
                    break
            
            # Si no tiene español/catalán, verificar si solo tiene inglés
            if not language_found and 'english' in language_text:
                self.logger.info(f"   🚫 Contenido rechazado - Solo inglés sin español: {language_text}")
                return False
            
            if not language_found:
                self.logger.info(f"   ❌ Idioma no encontrado en: {language_text}")
                return False
            
            # Filtro de formato (preferir 2160p, aceptar 1080p)
            format_text = item['format'].lower()
            quality_text = item['quality'].lower()
            combined_quality_text = format_text + " " + quality_text
            
            format_found = False
            for fmt in self.filters['preferred_formats']:
                if fmt.lower() in combined_quality_text:
                    format_found = True
                    self.logger.info(f"   ✅ Formato encontrado: {fmt}")
                    break
            
            if not format_found:
                self.logger.info(f"   ❌ Formato no encontrado en: {combined_quality_text}")
                return False
            
            # Filtro de tamaño para contenido de archivo único visual
            if item.get('content_mode') == 'single_file':
                estimated_size_gb = self._estimate_movie_size_from_quality(combined_quality_text)
                min_size_gb = self.settings.MINIMUM_MOVIE_SIZE_GB
                max_size_gb = getattr(self.settings, 'MAXIMUM_MOVIE_SIZE_GB', 0)

                if estimated_size_gb < min_size_gb:
                    self.logger.info(f"   ❌ Película muy pequeña: {estimated_size_gb:.1f}GB < {min_size_gb}GB mínimo")
                    return False
                if max_size_gb and estimated_size_gb > max_size_gb:
                    self.logger.info(f"   ❌ Película demasiado grande: {estimated_size_gb:.1f}GB > {max_size_gb}GB máximo")
                    return False
                self.logger.info(f"   ✅ Tamaño de película estimado aceptable: {estimated_size_gb:.1f}GB")
            
            self.logger.info(f"✅ Contenido pasa todos los filtros: {item['title'][:50]}...")
            return True
            
        except Exception as e:
            self.logger.warning(f"Error aplicando filtros: {e}")
            return False
    
    def _estimate_movie_size_from_quality(self, quality_text):
        """
        Estima el tamaño de una película basándose en la calidad
        ACTUALIZADO: Estimaciones más conservadoras para evitar descargas < 4GB
        """
        quality_lower = quality_text.lower()
        
        # Patrones de tamaño por calidad - ESTIMACIONES MÁS CONSERVADORAS
        if '2160p' in quality_lower or '4k' in quality_lower:
            if 'remux' in quality_lower or 'bdremux' in quality_lower:
                return 50.0  # 4K Remux muy grande
            elif 'bluray' in quality_lower or 'bdrip' in quality_lower:
                return 25.0  # 4K BluRay
            else:
                return 15.0  # 4K comprimido
        elif '1080p' in quality_lower:
            if 'remux' in quality_lower or 'bdremux' in quality_lower:
                return 25.0  # 1080p Remux
            elif 'bluray' in quality_lower or 'bdrip' in quality_lower:
                return 8.0   # 1080p BluRay
            else:
                # CAMBIO CRÍTICO: Estimación más conservadora para 1080p comprimido
                return 5.0   # 1080p comprimido - ESTIMACIÓN MÁS CONSERVADORA (era 3.0)
        elif '720p' in quality_lower:
            return 2.5   # Aumentado de 2.0 a 2.5
        else:
            return 2.0   # Calidad desconocida, estimación más conservadora (era 1.5)
    
    def _is_movie(self, item):
        """
        Determina si un elemento es una película basándose en su género
        """
        genre_text = item['genre'].lower()
        return 'películas' in genre_text

    def _check_existing_content(self, content_items):
        """
        Verifica qué contenido ya existe en las bibliotecas
        
        Args:
            content_items: Lista de elementos de contenido
            
        Returns:
            list: Elementos que no existen en las bibliotecas
        """
        unique_items = []
        
        for item in content_items:
            if not self._content_exists_in_library(item):
                unique_items.append(item)
            else:
                self.logger.info(f"⚠️ Contenido ya existe en biblioteca: {item['title'][:50]}...")
        
        return unique_items
    
    def _content_exists_in_library(self, item):
        """
        Verifica si el contenido ya existe en alguna biblioteca
        
        Args:
            item: Elemento de contenido
            
        Returns:
            bool: True si ya existe
        """
        try:
            # Limpiar título para búsqueda
            clean_title = self._clean_title_for_search(item['title'])

            category = item.get('library_category') or self._classify_library_category(item)
            if category == 'series':
                return self._series_exists_in_library(clean_title)
            if category == 'movies':
                return self._movie_exists_in_library(clean_title)
            if category == 'documentaries':
                return self._documentary_exists_in_library(clean_title)
            if category == 'cartoons':
                return self._cartoons_exists_in_library(clean_title)
            if category == 'animated_movies':
                return self._animated_movies_exists_in_library(clean_title)
            if category == 'docuseries':
                return self._docuseries_exists_in_library(clean_title)
            if category == 'concerts':
                return self._concerts_exists_in_library(clean_title)
            if category == 'music':
                return self._music_exists_in_library(clean_title)

            # Por defecto, buscar en todas las bibliotecas
            return (
                self._series_exists_in_library(clean_title) or
                self._movie_exists_in_library(clean_title) or
                self._documentary_exists_in_library(clean_title) or
                self._cartoons_exists_in_library(clean_title) or
                self._animated_movies_exists_in_library(clean_title) or
                self._docuseries_exists_in_library(clean_title) or
                self._concerts_exists_in_library(clean_title) or
                self._music_exists_in_library(clean_title)
            )
            
        except Exception as e:
            self.logger.warning(f"Error verificando contenido existente: {e}")
            return False

    def _classify_library_category(self, item):
        genre_text = (item.get('genre') or '').lower()
        title_text = (item.get('title') or '').lower()
        combined_text = f"{genre_text} {title_text}"

        is_series = 'series' in genre_text or 'serie' in genre_text
        is_movie = 'películas' in genre_text or 'pelicula' in genre_text
        is_documentary = 'documentales' in genre_text or 'documental' in genre_text
        is_music = 'música' in genre_text or 'musica' in genre_text
        is_animation = 'animación' in combined_text or 'animacion' in combined_text or 'anime' in combined_text
        is_concert = 'concierto' in combined_text or 'concert' in combined_text or 'live' in combined_text

        if is_series and ('documental' in combined_text or 'docuserie' in combined_text):
            return 'docuseries'
        if is_series and is_animation:
            return 'cartoons'
        if is_movie and is_animation:
            return 'animated_movies'
        if is_music and is_concert:
            return 'concerts'
        if is_documentary:
            return 'documentaries'
        if is_movie:
            return 'movies'
        if is_series:
            return 'series'
        if is_music:
            return 'music'
        return None

    def _matches_category_profile(self, profile, genre_text, combined_text):
        if not profile:
            return False

        genre_tokens = profile.get('genre_tokens', [])
        keywords = profile.get('keywords', [])

        genre_match = any(token in genre_text for token in genre_tokens) if genre_tokens else True
        keyword_match = any(keyword in combined_text for keyword in keywords) if keywords else True

        if genre_tokens and keywords:
            return genre_match and keyword_match
        return genre_match and keyword_match
    
    def _series_exists_in_library(self, clean_title):
        """Verifica si una serie ya existe en la biblioteca"""
        try:
            # Buscar en series existentes en la base de datos
            if self._db_match_exists(clean_title):
                return True
            
            # Buscar en archivos locales por nombre similar
            library_paths = self.settings.get_series_paths()
            for library_path in library_paths:
                if self._search_in_directory(Path(library_path), clean_title):
                    return True
            
            return False
        except Exception as e:
            self.logger.warning(f"Error verificando serie existente: {e}")
            return False
    
    def _movie_exists_in_library(self, clean_title):
        """Verifica si una película ya existe en la biblioteca"""
        try:
            if self._db_match_exists(clean_title, ['movies', 'animated_movies']):
                return True
            library_paths = self.settings.get_movies_paths() + self.settings.get_animated_movies_paths()
            for library_path in library_paths:
                if self._search_in_directory(Path(library_path), clean_title):
                    return True
            return False
        except Exception as e:
            self.logger.warning(f"Error verificando película existente: {e}")
            return False
    
    def _documentary_exists_in_library(self, clean_title):
        """Verifica si un documental ya existe en la biblioteca"""
        try:
            if self._db_match_exists(clean_title, ['documentaries', 'docuseries']):
                return True
            library_paths = self.settings.get_documentaries_paths() + self.settings.get_docuseries_paths()
            for library_path in library_paths:
                if self._search_in_directory(Path(library_path), clean_title):
                    return True
            return False
        except Exception as e:
            self.logger.warning(f"Error verificando documental existente: {e}")
            return False

    def _cartoons_exists_in_library(self, clean_title):
        """Verifica si dibujos ya existen en la biblioteca."""
        try:
            if self._db_match_exists(clean_title, ['cartoons']):
                return True
            library_paths = self.settings.get_cartoons_paths()
            for library_path in library_paths:
                if self._search_in_directory(Path(library_path), clean_title):
                    return True
            return False
        except Exception as e:
            self.logger.warning(f"Error verificando dibujos existentes: {e}")
            return False

    def _animated_movies_exists_in_library(self, clean_title):
        """Verifica si una película de animación ya existe en la biblioteca."""
        try:
            if self._db_match_exists(clean_title, ['animated_movies']):
                return True
            library_paths = self.settings.get_animated_movies_paths()
            for library_path in library_paths:
                if self._search_in_directory(Path(library_path), clean_title):
                    return True
            return False
        except Exception as e:
            self.logger.warning(f"Error verificando película animada existente: {e}")
            return False

    def _docuseries_exists_in_library(self, clean_title):
        """Verifica si un documental en serie ya existe en la biblioteca."""
        try:
            if self._db_match_exists(clean_title, ['docuseries']):
                return True
            library_paths = self.settings.get_docuseries_paths()
            for library_path in library_paths:
                if self._search_in_directory(Path(library_path), clean_title):
                    return True
            return False
        except Exception as e:
            self.logger.warning(f"Error verificando docuserie existente: {e}")
            return False

    def _concerts_exists_in_library(self, clean_title):
        """Verifica si un concierto ya existe en la biblioteca."""
        try:
            if self._db_match_exists(clean_title, ['concerts']):
                return True
            library_paths = self.settings.get_concerts_paths()
            for library_path in library_paths:
                if self._search_in_directory(Path(library_path), clean_title):
                    return True
            return False
        except Exception as e:
            self.logger.warning(f"Error verificando concierto existente: {e}")
            return False
    
    def _music_exists_in_library(self, clean_title):
        """Verifica si música ya existe en la biblioteca"""
        try:
            if self._db_match_exists(clean_title, ['music']):
                return True
            library_paths = self.settings.get_music_paths()
            for library_path in library_paths:
                if self._search_in_directory(Path(library_path), clean_title):
                    return True
            return False
        except Exception as e:
            self.logger.warning(f"Error verificando música existente: {e}")
            return False
    
    def _clean_title_for_search(self, title):
        """
        Limpia el título para búsqueda eliminando información extra
        
        Args:
            title: Título original
            
        Returns:
            str: Título limpio
        """
        # Remover información entre paréntesis (director, año)
        clean_title = re.sub(r'\s*\([^)]*\)', '', title)
        
        # Remover información de temporada
        clean_title = re.sub(r'\s*\(Temporada\s+\d+\)', '', clean_title, flags=re.IGNORECASE)
        clean_title = re.sub(r'\s*Temporada\s+\d+', '', clean_title, flags=re.IGNORECASE)
        
        # Remover "Miniserie"
        clean_title = re.sub(r'\s*\(Miniserie\)', '', clean_title, flags=re.IGNORECASE)
        
        return clean_title.strip()
    
    def _search_in_directory(self, directory, title):
        """
        Busca un título en un directorio
        
        Args:
            directory: Path del directorio
            title: Título a buscar
            
        Returns:
            bool: True si se encuentra
        """
        try:
            if not directory.exists():
                return False
            
            title_lower = title.lower()
            max_depth = getattr(self.settings, 'LIBRARY_SEARCH_MAX_DEPTH', 2)
            video_exts = {'.mkv', '.mp4', '.avi', '.mov', '.m4v'}
            
            # Búsqueda directa en carpetas de primer nivel
            for item in directory.iterdir():
                if item.is_dir():
                    item_name_lower = item.name.lower()
                    # Búsqueda flexible por similitud
                    if self._titles_are_similar(title_lower, item_name_lower):
                        return True
            
            # Recorrer subdirectorios y comprobar archivos de vídeo
            def walk(dir_path, depth):
                if depth > max_depth:
                    return False
                for entry in dir_path.iterdir():
                    try:
                        if entry.is_dir():
                            entry_name_lower = entry.name.lower()
                            if self._titles_are_similar(title_lower, entry_name_lower):
                                return True
                            if walk(entry, depth + 1):
                                return True
                        else:
                            if entry.suffix.lower() in video_exts:
                                file_name_lower = entry.stem.lower()
                                if self._titles_are_similar(title_lower, file_name_lower):
                                    return True
                    except Exception:
                        continue
                return False
            
            return walk(directory, 1)
            
        except Exception as e:
            self.logger.warning(f"Error buscando en directorio {directory}: {e}")
            return False
    
    def _titles_are_similar(self, title1, title2):
        """
        Verifica si dos títulos son similares
        MEJORADO: Comparación más estricta para evitar falsos positivos
        
        Args:
            title1: Primer título
            title2: Segundo título
            
        Returns:
            bool: True si son similares
        """
        # Normalizar títulos (eliminar acentos y puntuación)
        import unicodedata
        def normalize(text):
            text_no_accents = ''.join(ch for ch in unicodedata.normalize('NFD', text) if unicodedata.category(ch) != 'Mn')
            return re.sub(r'[^\w\s]', '', text_no_accents.lower())
        
        t1 = normalize(title1)
        t2 = normalize(title2)
        
        # MEJORA 1: Verificar coincidencia exacta primero
        if t1 == t2:
            return True

        # NOTA: NO se usa "uno contenido en el otro" como criterio de similitud.
        # Eso trataba franquicias/secuelas como el mismo título (p.ej. "Toy Story"
        # quedaba "contenido en" "Toy Story 3"), marcándolas como ya poseídas y
        # dejándolas fuera para siempre de la búsqueda/descarga automática.

        # MEJORA 3: Comparación por palabras tipo Jaccard (intersección / unión),
        # simétrica en ambos sentidos: una palabra o número distintivo presente
        # en un título pero no en el otro (p.ej. "3", "II", un año) penaliza la
        # similitud sin importar cuál de los dos títulos sea más largo.
        words1 = set(word for word in t1.split() if len(word) > 2 or word.isdigit())
        words2 = set(word for word in t2.split() if len(word) > 2 or word.isdigit())

        if not words1 or not words2:
            return False

        intersection = words1 & words2
        union = words1 | words2
        similarity = len(intersection) / len(union)

        # MEJORA 4: Logging para debug
        if similarity >= 0.7:  # Log cuando hay similitud alta pero no suficiente
            self.logger.debug(f"   🔍 Similitud {similarity:.2f}: '{title1}' vs '{title2}'")

        return similarity >= 0.85  # Umbral más estricto

    def _db_match_exists(self, clean_title, categories=None):
        """
        Comprueba si algún título ya guardado en BD corresponde realmente al
        título buscado, en vez de confiar ciegamente en el filtro LIKE '%...%'
        de search_series_by_name (que trata "Toy Story" y "Toy Story 3" como
        el mismo resultado por pura coincidencia de subcadena).
        """
        candidates = self.db_manager.search_series_by_name(clean_title, categories) or []
        for candidate in candidates:
            candidate_name = candidate.get('name') or ''
            candidate_tmdb_name = candidate.get('tmdb_name') or ''
            if candidate_name and self._titles_are_similar(clean_title, candidate_name):
                return True
            if candidate_tmdb_name and self._titles_are_similar(clean_title, candidate_tmdb_name):
                return True
        return False

    def process_new_content(self, content_items):
        """
        Procesa el nuevo contenido encontrado, accediendo a cada URL y descargando
        
        Args:
            content_items: Lista de elementos de contenido a procesar
            
        Returns:
            list: Enlaces ed2k obtenidos
        """
        self.logger.info(f"🎬 Procesando {len(content_items)} elementos de nuevo contenido...")
        
        all_ed2k_links = []
        grouped_items = {}
        for item in content_items:
            grouped_items.setdefault(item.get('library_category', 'sin_categoria'), []).append(item)

        for category, items in grouped_items.items():
            self.logger.info(f"📚 Procesando categoría {Settings.get_category_label(category)}: {len(items)} elementos")
        
        for i, item in enumerate(content_items, 1):
            category_label = Settings.get_category_label(item.get('library_category', ''))
            self.logger.info(f"📥 Procesando {i}/{len(content_items)} [{category_label}]: {item['title'][:50]}...")
            
            try:
                ed2k_links = self._process_content_item(item)
                all_ed2k_links.extend(ed2k_links)
                
                self.logger.info(f"   ✅ {len(ed2k_links)} enlaces obtenidos")
                
            except Exception as e:
                self.logger.error(f"   ❌ Error procesando contenido: {e}")
                continue
        
        self.logger.info(f"🎉 Procesamiento completado: {len(all_ed2k_links)} enlaces ed2k totales")
        
        # Enviar enlaces a eMule si está configurado
        if all_ed2k_links and self.settings.EMULE_AUTO_SEND:
            self._send_links_to_emule(all_ed2k_links, "nuevo_contenido")
        
        return all_ed2k_links
    
    def _process_content_item(self, item):
        """
        Procesa un elemento de contenido específico
        
        Args:
            item: Elemento de contenido
            
        Returns:
            list: Enlaces ed2k del elemento
        """
        try:
            self.driver.get(item['url'])
            
            # Esperar a que la página cargue
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.CLASS_NAME, "WIN1_CONTAINER"))
            )
            
            # Obtener el HTML de la página
            page_source = self.driver.page_source
            soup = BeautifulSoup(page_source, 'html.parser')
            
            # Buscar secciones de calidad
            quality_sections = self._get_quality_sections_with_scores(soup)
            
            if not quality_sections:
                self.logger.warning(f"No se encontraron secciones de calidad para: {item['title']}")
                return []
            
            # Procesar la mejor calidad disponible
            for section, score, quality_text in quality_sections:
                self.logger.info(f"   🎯 Procesando calidad: {quality_text}")
                
                # Verificar tamaño para películas usando tamaño REAL del HTML (tabla T2)
                if self._is_movie(item):
                    reference_size_mb = self._calculate_average_file_size(section)
                    size_gb_real = (reference_size_mb / 1024.0) if reference_size_mb and reference_size_mb > 0 else None
                    
                    min_size_gb = self.settings.MINIMUM_MOVIE_SIZE_GB
                    max_size_gb = getattr(self.settings, 'MAXIMUM_MOVIE_SIZE_GB', 0)

                    # USAR TAMAÑO REAL DEL HTML - No más estimaciones
                    if size_gb_real is not None and size_gb_real > 0:
                        # Aplicar filtros de tamaño usando el tamaño real extraído del HTML
                        if size_gb_real < min_size_gb:
                            self.logger.warning(f"   🚫 Película rechazada por tamaño insuficiente: {size_gb_real:.2f}GB < {min_size_gb}GB (tamaño real del HTML)")
                            continue  # Probar siguiente calidad

                        if max_size_gb and max_size_gb > 0 and size_gb_real > max_size_gb:
                            self.logger.warning(f"   🚫 Película rechazada por tamaño superior al máximo: {size_gb_real:.2f}GB > {max_size_gb}GB (tamaño real del HTML)")
                            continue  # Probar siguiente calidad

                        self.logger.info(f"   ✅ Tamaño de película aceptable: {size_gb_real:.2f}GB (tamaño real del HTML)")
                    else:
                        # Si no se puede extraer el tamaño real del HTML, usar estimación como fallback
                        size_gb_estimated = self._estimate_movie_size_from_quality(quality_text)
                        if size_gb_estimated < min_size_gb:
                            self.logger.warning(f"   🚫 Película rechazada por tamaño estimado insuficiente: {size_gb_estimated:.1f}GB < {min_size_gb}GB (fallback)")
                            continue
                        self.logger.info(f"   ⚠️ Usando tamaño estimado (no se pudo extraer del HTML): {size_gb_estimated:.1f}GB")
                
                ed2k_links = self._process_content_quality_section(section, quality_text)
                
                if ed2k_links:
                    return ed2k_links
            
            return []
            
        except Exception as e:
            self.logger.error(f"Error procesando elemento de contenido: {e}")
            return []
    
    def _get_quality_sections_with_scores(self, soup):
        """
        Obtiene secciones de calidad con puntuaciones (reutiliza lógica del downloader)
        """
        quality_sections = []
        
        # Buscar todos los h2 que contienen información de calidad
        h2_elements = soup.find_all('h2')
        
        for h2 in h2_elements:
            quality_text = h2.get_text(strip=True)
            
            # Buscar la tabla T2 asociada a este h2
            parent = h2.parent
            if parent:
                table = parent.find('table', class_='T2')
                if table:
                    score = self._calculate_quality_score(quality_text)
                    quality_sections.append((table, score, quality_text))
        
        # Ordenar por puntuación descendente
        quality_sections.sort(key=lambda x: x[1], reverse=True)
        
        return quality_sections

    def _calculate_average_file_size(self, table):
        """
        Calcula un tamaño de referencia (MB) a partir de la tabla T2 asociada a una calidad.
        Para contenido multi-archivo, prioriza el primer archivo y añade el resto con menor peso.
        """
        file_sizes = []
        try:
            rows = table.find_all('tr')
            if not rows:
                return 0
            # Saltar cabecera si la primera fila no contiene enlaces
            data_rows = rows[1:] if len(rows) > 1 else rows

            if not data_rows:
                return 0

            first_row = data_rows[0]
            first_size_mb = self._extract_file_size_from_row(first_row)
            if first_size_mb > 0:
                file_sizes.append(first_size_mb)
                for row in data_rows[1:]:
                    size_mb = self._extract_file_size_from_row(row)
                    if size_mb > 0:
                        file_sizes.append(size_mb * 0.3)
            else:
                # No se pudo extraer el tamaño del primer archivo, intentar con todos
                for row in data_rows:
                    size_mb = self._extract_file_size_from_row(row)
                    if size_mb > 0:
                        file_sizes.append(size_mb)

            # Si no hay tamaños, estimar por nombre del primer archivo
            if not file_sizes and data_rows:
                file_link = data_rows[0].find('a')
                if file_link:
                    filename = file_link.get_text(strip=True)
                    estimated_size = self._estimate_size_from_filename(filename)
                    if estimated_size > 0:
                        file_sizes.append(estimated_size)

            return sum(file_sizes) / len(file_sizes) if file_sizes else 0
        except Exception:
            return 0

    def _extract_file_size_from_row(self, row):
        """
        Extrae el tamaño del archivo (MB) desde una fila de la tabla T2.
        Busca específicamente en la columna de tamaño (class="SIZE").
        """
        try:
            # Buscar primero celdas con class="SIZE"
            size_cells = row.find_all('td', class_='SIZE')
            if size_cells:
                # La primera celda SIZE debería ser el tamaño del archivo
                for cell in size_cells:
                    text = cell.get_text(strip=True)
                    size_mb = self._parse_file_size(text)
                    if size_mb > 0:
                        self.logger.debug(f"Tamaño extraído de celda SIZE: {text} -> {size_mb} MB")
                        return size_mb
            
            # Fallback: buscar en todas las celdas
            cells = row.find_all('td')
            for cell in cells:
                text = cell.get_text(strip=True)
                size_mb = self._parse_file_size(text)
                if size_mb > 0:
                    self.logger.debug(f"Tamaño extraído de celda genérica: {text} -> {size_mb} MB")
                    return size_mb
        except Exception as e:
            self.logger.debug(f"Error extrayendo tamaño de fila: {e}")
        return 0

    def _parse_file_size(self, size_text):
        """
        Convierte un texto de tamaño (GB/MB/KB) a MB.
        Maneja tanto formato español (coma decimal) como inglés (punto decimal).
        """
        try:
            # Normalizar el texto: reemplazar comas por puntos para el parsing
            normalized_text = size_text.replace(',', '.')
            
            # Buscar patrón de número seguido de unidad
            match = re.search(r'(\d+(?:\.\d+)?)\s*(?:&nbsp;)?\s*(GB|MB|KB)', normalized_text, flags=re.IGNORECASE)
            if match:
                value = float(match.group(1))
                unit = match.group(2).upper()
                if unit == 'GB':
                    return value * 1024  # Convertir GB a MB
                if unit == 'MB':
                    return value
                if unit == 'KB':
                    return value / 1024  # Convertir KB a MB
        except Exception as e:
            self.logger.debug(f"Error parseando tamaño '{size_text}': {e}")
        return 0

    def _estimate_size_from_filename(self, filename):
        """
        Estima tamaño (MB) basándose en nombre y calidad.
        """
        name = filename.lower()
        if '2160p' in name or '4k' in name:
            if 'x265' in name or 'hevc' in name:
                return 8000
            return 15000
        if '1080p' in name:
            if 'x265' in name or 'hevc' in name:
                return 1500
            return 2500
        if '720p' in name:
            return 1000
        return 800
    
    def _calculate_quality_score(self, quality_text):
        """
        Calcula puntuación de calidad con preferencia x264 para 1080p
        
        Criterios de prioridad:
        1. Resolución: 2160p > 1080p > 720p
        2. Codec: Para 1080p prefiere x264 > x265, para 2160p prefiere x265 > x264
        3. HDR
        """
        score = 0
        quality_lower = quality_text.lower()
        
        # Resolución
        if '2160p' in quality_lower or '4k' in quality_lower:
            score += 3000
            # Para 2160p, preferir x265 (mejor compresión para alta resolución)
            if 'x265' in quality_lower or 'h265' in quality_lower or 'hevc' in quality_lower:
                score += 500
            elif 'x264' in quality_lower or 'h264' in quality_lower:
                score += 300
        elif '1080p' in quality_lower:
            score += 2000
            # Para 1080p, preferir x264 (mejor compatibilidad y calidad)
            if 'x264' in quality_lower or 'h264' in quality_lower:
                score += 500
            elif 'x265' in quality_lower or 'h265' in quality_lower or 'hevc' in quality_lower:
                score += 300
        elif '720p' in quality_lower:
            score += 1000
            # Para 720p, preferir x264
            if 'x264' in quality_lower or 'h264' in quality_lower:
                score += 400
            elif 'x265' in quality_lower or 'h265' in quality_lower or 'hevc' in quality_lower:
                score += 200
        
        # HDR
        if 'hdr' in quality_lower or 'dolby vision' in quality_lower:
            score += 100
        
        return score
    
    def _process_content_quality_section(self, section, quality_text):
        """
        Procesa una sección de calidad para contenido nuevo
        
        Args:
            section: Elemento BeautifulSoup de la tabla
            quality_text: Texto de la calidad
            
        Returns:
            list: Enlaces ed2k
        """
        ed2k_links = []
        
        try:
            # Buscar checkboxes en la sección
            checkboxes = section.find_all('input', type='checkbox')
            self.logger.info(f"    📋 Checkboxes encontrados: {len(checkboxes)}")
            
            if checkboxes:
                # Si hay checkboxes, seleccionar todos y obtener enlaces
                checkbox_ids = [cb.get('id') for cb in checkboxes if cb.get('id')]
                self.logger.info(f"    📋 Checkbox IDs válidos: {len(checkbox_ids)} - {checkbox_ids[:3] if checkbox_ids else 'ninguno'}")
                
                if checkbox_ids:
                    ed2k_links = self._select_all_and_get_links(checkbox_ids, quality_text)
                else:
                    self.logger.warning(f"    ⚠️ No hay checkbox IDs válidos")
            else:
                # Si no hay checkboxes, buscar enlaces directos
                self.logger.info(f"    🔗 No hay checkboxes, buscando enlaces directos...")
                ed2k_links = self._get_direct_links(section)
            
        except Exception as e:
            self.logger.error(f"Error procesando sección de calidad: {e}")
        
        return ed2k_links
    
    def _select_all_and_get_links(self, checkbox_ids, quality_text):
        """
        Selecciona todos los checkboxes y obtiene enlaces ed2k
        """
        ed2k_links = []
        
        try:
            # Seleccionar todos los checkboxes
            for checkbox_id in checkbox_ids:
                try:
                    checkbox = self.driver.find_element(By.ID, checkbox_id)
                    if not checkbox.is_selected():
                        self.driver.execute_script("arguments[0].click();", checkbox)
                        self.logger.info(f"    📋 Checkbox seleccionado: {checkbox_id}")
                except NoSuchElementException:
                    self.logger.warning(f"    ⚠️ Checkbox no encontrado: {checkbox_id}")
                    continue
            
            # Extraer group_id y obtener enlaces (similar al downloader)
            if checkbox_ids:
                match = re.match(r'elink_(\d+)_\d+', checkbox_ids[0])
                if match:
                    group_id = match.group(1)
                    self.logger.info(f"    🔍 Group ID detectado: {group_id}")
                    ed2k_links = self._get_links_from_group(group_id, quality_text)
                else:
                    self.logger.warning(f"    ⚠️ No se pudo extraer group_id de: {checkbox_ids[0]}")
            else:
                self.logger.warning(f"    ⚠️ No hay checkbox_ids válidos")
            
        except Exception as e:
            self.logger.error(f"Error seleccionando checkboxes: {e}")
        
        return ed2k_links
    
    def _get_links_from_group(self, group_id, quality_text):
        """
        Obtiene enlaces ed2k de un grupo específico usando múltiples estrategias de búsqueda
        """
        ed2k_links = []
        
        try:
            # Buscar botón "Mostrar seleccionados" con múltiples estrategias
            show_button = None
            
            # Estrategia 1: Buscar por onclick exacto
            try:
                button_xpath_1 = f"//a[@onclick='javascript:DownloadSelected({group_id}, 4)']"
                show_button = self.driver.find_element(By.XPATH, button_xpath_1)
                self.logger.info(f"Botón encontrado con estrategia 1 para grupo {group_id}")
            except NoSuchElementException:
                pass
            
            # Estrategia 2: Buscar por onclick que contenga el group_id
            if not show_button:
                try:
                    button_xpath_2 = f"//a[contains(@onclick, 'DownloadSelected({group_id},')]"
                    show_button = self.driver.find_element(By.XPATH, button_xpath_2)
                    self.logger.info(f"Botón encontrado con estrategia 2 para grupo {group_id}")
                except NoSuchElementException:
                    pass
            
            # Estrategia 3: Buscar por texto del span interno
            if not show_button:
                try:
                    button_xpath_3 = f"//a[contains(@onclick, 'DownloadSelected')]//span[contains(text(), 'Mostrar seleccionados')]/.."
                    show_button = self.driver.find_element(By.XPATH, button_xpath_3)
                    # Verificar que sea el botón correcto comparando el onclick
                    onclick_attr = show_button.get_attribute('onclick')
                    if f"DownloadSelected({group_id}," in onclick_attr:
                        self.logger.info(f"Botón encontrado con estrategia 3 para grupo {group_id}")
                    else:
                        show_button = None
                except NoSuchElementException:
                    pass
            
            # Estrategia 4: Buscar todos los botones y filtrar por group_id
            if not show_button:
                try:
                    all_buttons = self.driver.find_elements(By.XPATH, "//a[contains(@onclick, 'DownloadSelected')]")
                    for button in all_buttons:
                        onclick_attr = button.get_attribute('onclick')
                        if f"DownloadSelected({group_id}," in onclick_attr:
                            show_button = button
                            self.logger.info(f"Botón encontrado con estrategia 4 para grupo {group_id}")
                            break
                except Exception:
                    pass
            
            if show_button:
                # Hacer scroll al botón para asegurar que esté visible
                self.driver.execute_script("arguments[0].scrollIntoView(true);", show_button)
                import time
                time.sleep(1)
                
                # Hacer click en el botón
                self.driver.execute_script("arguments[0].click();", show_button)
                self.logger.info(f"Botón 'Mostrar seleccionados' ejecutado para grupo {group_id}")
                
                # Esperar un poco para que se procese la acción
                time.sleep(3)
                
                # Esperar popup con enlaces
                WebDriverWait(self.driver, 15).until(
                    EC.presence_of_element_located((By.ID, "ELINKSLIST"))
                )
                
                # Obtener enlaces del textarea
                textarea = self.driver.find_element(By.ID, "ELINKSLIST")
                links_text = textarea.get_attribute('value')
                
                if links_text:
                    lines = links_text.strip().split('\n')
                    for line in lines:
                        line = line.strip()
                        if line.startswith('ed2k://'):
                            if line not in ed2k_links:  # Evitar duplicados
                                ed2k_links.append(line)
                                self.logger.info(f"Enlace ed2k obtenido: {line[:80]}...")
                
                # Cerrar popup
                try:
                    close_button = self.driver.find_element(By.XPATH, "//div[@class='X']")
                    self.driver.execute_script("arguments[0].click();", close_button)
                except:
                    pass
            else:
                self.logger.warning(f"No se pudo encontrar el botón 'Mostrar seleccionados' para grupo {group_id}")
                
        except Exception as e:
            self.logger.error(f"Error obteniendo enlaces del grupo {group_id}: {e}")
        
        return ed2k_links
    
    def _get_direct_links(self, section):
        """
        Obtiene enlaces directos cuando no hay checkboxes
        Para archivos únicos, hace clic en el enlace para abrir ventana emergente
        """
        ed2k_links = []
        
        try:
            # Primero buscar enlaces ed2k directos en la sección
            ed2k_elements = section.find_all('a', href=re.compile(r'^ed2k://'))
            
            for element in ed2k_elements:
                ed2k_link = element.get('href')
                if ed2k_link:
                    ed2k_links.append(ed2k_link)
            
            # Si no hay enlaces directos, buscar enlaces que abran ventana emergente
            if not ed2k_links:
                self.logger.info("    🔗 No hay enlaces ed2k directos, buscando enlaces de descarga...")
                ed2k_links = self._handle_single_file_download(section)
            
        except Exception as e:
            self.logger.error(f"Error obteniendo enlaces directos: {e}")
        
        return ed2k_links
    
    def _handle_single_file_download(self, section):
        """
        Maneja la descarga de archivos únicos haciendo clic en enlaces
        """
        ed2k_links = []
        
        try:
            # Buscar enlaces JavaScript:Download() usando Selenium directamente
            javascript_links = self.driver.find_elements(By.XPATH, "//a[starts-with(@href, 'javascript:Download(')]")
            
            for link_element in javascript_links:
                href = link_element.get_attribute('href')
                text = link_element.text.strip()
                
                if href and href.startswith('javascript:Download('):
                    self.logger.info(f"    🎯 Encontrado enlace JavaScript: '{text}' -> {href}")
                    
                    try:
                        # Scroll al elemento y hacer clic
                        self.driver.execute_script("arguments[0].scrollIntoView(true);", link_element)
                        time.sleep(1)
                        
                        # Hacer clic usando JavaScript
                        self.driver.execute_script("arguments[0].click();", link_element)
                        
                        # Esperar a que aparezca una ventana emergente o modal
                        time.sleep(3)
                        
                        # Buscar ventana emergente con enlaces ed2k
                        ed2k_links = self._extract_ed2k_from_popup()
                        
                        if ed2k_links:
                            self.logger.info(f"    ✅ Obtenidos {len(ed2k_links)} enlaces de '{text}'")
                            break
                        
                    except Exception as e:
                        self.logger.warning(f"    ⚠️ Error ejecutando JavaScript para '{text}': {e}")
                        continue
            
            # Si no se encontraron enlaces JavaScript:Download(), buscar otros enlaces de descarga
            if not ed2k_links:
                self.logger.info("    🔄 Buscando otros enlaces de descarga...")
                download_links = self.driver.find_elements(By.XPATH, "//a[@href and not(starts-with(@href, 'ed2k://')) and not(starts-with(@href, '#'))]")
                
                for link_element in download_links[:10]:  # Limitar intentos
                    href = link_element.get_attribute('href')
                    text = link_element.text.strip()
                    
                    # Filtrar enlaces que parecen ser de descarga
                    if (href and 
                        ('descarga' in href.lower() or 'download' in href.lower() or 'get' in href.lower() or
                         'descarga' in text.lower() or 'download' in text.lower())):
                        
                        self.logger.info(f"    🎯 Probando enlace de descarga: '{text}' -> {href}")
                        
                        try:
                            self.driver.execute_script("arguments[0].scrollIntoView(true);", link_element)
                            time.sleep(1)
                            self.driver.execute_script("arguments[0].click();", link_element)
                            time.sleep(3)
                            
                            ed2k_links = self._extract_ed2k_from_popup()
                            
                            if ed2k_links:
                                self.logger.info(f"    ✅ Obtenidos {len(ed2k_links)} enlaces de '{text}'")
                                break
                                
                        except Exception as e:
                            self.logger.warning(f"    ⚠️ Error con enlace '{text}': {e}")
                            continue
            
            # Si aún no hay enlaces, probar con todos los enlaces JavaScript
            if not ed2k_links:
                self.logger.info("    🔄 Probando con todos los enlaces JavaScript disponibles...")
                all_js_links = self.driver.find_elements(By.XPATH, "//a[starts-with(@href, 'javascript:')]")
                
                for link_element in all_js_links[:5]:  # Limitar a 5 intentos
                    href = link_element.get_attribute('href')
                    text = link_element.text.strip()
                    
                    if href and len(href) > 15:  # Enlaces JavaScript válidos
                        self.logger.info(f"    🎯 Probando JavaScript: '{text}' -> {href[:50]}...")
                        
                        try:
                            self.driver.execute_script("arguments[0].scrollIntoView(true);", link_element)
                            time.sleep(1)
                            self.driver.execute_script("arguments[0].click();", link_element)
                            time.sleep(3)
                            
                            ed2k_links = self._extract_ed2k_from_popup()
                            
                            if ed2k_links:
                                self.logger.info(f"    ✅ Obtenidos {len(ed2k_links)} enlaces de '{text}'")
                                break
                                
                        except Exception as e:
                            self.logger.warning(f"    ⚠️ Error con JavaScript '{text}': {e}")
                            continue
            
        except Exception as e:
            self.logger.error(f"Error manejando descarga de archivo único: {e}")
        
        return ed2k_links
    
    def _extract_ed2k_from_popup(self):
        """
        Extrae enlaces ed2k de ventanas emergentes o modales
        """
        ed2k_links = []
        
        try:
            # Buscar modal con ID conocido (como en downloader)
            try:
                modal = WebDriverWait(self.driver, 5).until(
                    EC.presence_of_element_located((By.ID, "ELINKSLIST"))
                )
                
                # Buscar textarea con enlaces
                textarea = modal.find_element(By.TAG_NAME, "textarea")
                links_text = textarea.get_attribute("value")
                
                if links_text:
                    for line in links_text.split('\n'):
                        line = line.strip()
                        if line.startswith('ed2k://'):
                            ed2k_links.append(line)
                
                # Cerrar modal
                try:
                    close_button = modal.find_element(By.XPATH, ".//input[@value='Cerrar']")
                    close_button.click()
                except:
                    pass
                    
            except:
                # Si no hay modal específico, buscar en toda la página
                page_source = self.driver.page_source
                soup = BeautifulSoup(page_source, 'html.parser')
                
                # Buscar textareas con enlaces ed2k
                textareas = soup.find_all('textarea')
                for textarea in textareas:
                    content = textarea.get_text()
                    if content and 'ed2k://' in content:
                        for line in content.split('\n'):
                            line = line.strip()
                            if line.startswith('ed2k://'):
                                ed2k_links.append(line)
                
                # Buscar enlaces ed2k directos que puedan haber aparecido
                ed2k_elements = soup.find_all('a', href=re.compile(r'^ed2k://'))
                for element in ed2k_elements:
                    ed2k_link = element.get('href')
                    if ed2k_link and ed2k_link not in ed2k_links:
                        ed2k_links.append(ed2k_link)
            
        except Exception as e:
            self.logger.warning(f"Error extrayendo enlaces de ventana emergente: {e}")
        
        return ed2k_links
    
    def _send_links_to_emule(self, ed2k_links, content_type):
        """
        Envía enlaces ed2k a eMule
        
        Args:
            ed2k_links: Lista de enlaces ed2k
            content_type: Tipo de contenido para el nombre del archivo
        """
        try:
            if not self.emule_client.is_configured():
                self.logger.warning("eMule no está configurado para envío automático")
                return

            links_to_send = list(ed2k_links or [])
            if self.db_manager:
                links_to_send = self.db_manager.filter_new_ed2k_links(links_to_send)
                if not links_to_send:
                    self.logger.info("No hay enlaces nuevos para enviar a eMule (ya enviados anteriormente)")
                    return
            
            # Guardar enlaces en archivo
            downloads_dir = Path("downloads")
            downloads_dir.mkdir(exist_ok=True)
            
            from datetime import datetime
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            links_file = downloads_dir / f"{content_type}_{timestamp}_ed2k_links.txt"
            
            with open(links_file, 'w', encoding='utf-8') as f:
                f.write(f"Enlaces ed2k - {content_type}\n")
                f.write(f"Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write("-" * 50 + "\n\n")
                
                for i, link in enumerate(links_to_send, 1):
                    f.write(f"{i}. {link}\n")
            
            self.logger.info(f"Enlaces guardados en: {links_file}")
            
            # Enviar a eMule
            result = self.emule_client.add_multiple_ed2k_links(links_to_send)
            
            if result['success']:
                self.logger.info(f"✅ {result['added']}/{result['total']} enlaces enviados a eMule")
                if result.get('failed_links'):
                    self.logger.warning(
                        f"⚠️ {len(result['failed_links'])} enlaces no se pudieron añadir a aMule y se reintentarán en el próximo ciclo"
                    )
                if self.db_manager:
                    sent_links = result.get('added_links', links_to_send)
                    self.db_manager.mark_ed2k_links_sent(sent_links, kind=content_type)
            else:
                self.logger.warning(
                    f"❌ Error enviando enlaces a eMule: {result.get('message', 'Error desconocido')}. "
                    f"Errores: {result.get('errors', [])[:3]}"
                )
                
        except Exception as e:
            self.logger.error(f"Error enviando enlaces a eMule: {e}")
