import os
import re
import logging
from pathlib import Path
from datetime import date, datetime
from bs4 import BeautifulSoup
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from ..library.parser import EpisodeParser
from ..emule.client import EmuleClient
from config.settings import Settings


class HispashareDownloader:
    def __init__(self, driver, db_manager):
        self.driver = driver
        self.db_manager = db_manager
        self.logger = logging.getLogger(__name__)
        self.parser = EpisodeParser()
        self.emule_client = EmuleClient()
        self.settings = Settings()
    
    def get_missing_episodes(self, series_id):
        """
        Obtiene los episodios que faltan para una serie comparando con archivos locales
        
        Args:
            series_id: ID de la serie en la base de datos
            
        Returns:
            dict: {season: [episode_numbers]} de episodios faltantes
        """
        # Obtener información de la serie
        series = self.db_manager.get_series_by_id(series_id)
        if not series:
            self.logger.error(f"Serie con ID {series_id} no encontrada")
            return {}
        
        series_path = Path(series['folder_path'])
        if not series_path.exists():
            self.logger.error(f"Carpeta de serie no existe: {series_path}")
            return {}
        
        # Escanear episodios existentes
        existing_episodes = self._scan_existing_episodes(series_path)
        
        # Obtener información de TMDB para saber qué episodios deberían existir
        tmdb_episodes = self._get_tmdb_episodes(series['tmdb_id'])
        
        # Calcular episodios faltantes
        missing_episodes = {}
        for season, episodes in tmdb_episodes.items():
            missing_in_season = []
            for episode_num in episodes:
                if season not in existing_episodes or episode_num not in existing_episodes[season]:
                    missing_in_season.append(episode_num)
            
            if missing_in_season:
                missing_episodes[season] = missing_in_season
        
        return missing_episodes

    def _parse_air_date(self, value):
        if not value:
            return None
        if isinstance(value, date):
            return value
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            try:
                return datetime.strptime(text, "%Y-%m-%d").date()
            except Exception:
                return None
        return None

    def _build_missing_episodes_from_rows(self, rows):
        missing_episodes = {}
        air_dates = {}
        for row in rows:
            season = row.get('season')
            episode = row.get('episode')
            if season is None or episode is None:
                continue
            missing_episodes.setdefault(season, []).append(episode)
            air_dates[(season, episode)] = row.get('air_date')

        for season in missing_episodes:
            missing_episodes[season] = sorted(set(missing_episodes[season]))

        return missing_episodes, air_dates

    def _filter_missing_by_min_age(self, missing_episodes, air_dates, min_age_days):
        if not min_age_days or min_age_days <= 0:
            return missing_episodes, 0, sum(len(v) for v in missing_episodes.values())

        today = date.today()
        eligible = {}
        skipped = 0
        total = 0

        for season, episodes in missing_episodes.items():
            for episode in episodes:
                total += 1
                air_date = self._parse_air_date(air_dates.get((season, episode)))
                if air_date is None:
                    eligible.setdefault(season, []).append(episode)
                    continue

                age_days = (today - air_date).days
                if age_days >= min_age_days:
                    eligible.setdefault(season, []).append(episode)
                else:
                    skipped += 1

        for season in eligible:
            eligible[season] = sorted(set(eligible[season]))

        return eligible, skipped, total
    
    def _scan_existing_episodes(self, series_path):
        """
        Escanea la carpeta de la serie para encontrar episodios existentes
        
        Returns:
            dict: {season: [episode_numbers]}
        """
        existing_episodes = {}
        
        # Extensiones de video válidas
        video_extensions = {'.mkv', '.mp4', '.avi', '.mov', '.wmv', '.flv', '.webm'}
        
        # Buscar archivos de video recursivamente
        for file_path in series_path.rglob('*'):
            if file_path.is_file() and file_path.suffix.lower() in video_extensions:
                episode_info = self.parser.parse_episode(file_path.name)
                if episode_info:
                    season = episode_info['season']
                    episode = episode_info['episode']
                    
                    if season not in existing_episodes:
                        existing_episodes[season] = []
                    
                    if episode not in existing_episodes[season]:
                        existing_episodes[season].append(episode)
        
        return existing_episodes
    
    def _get_tmdb_episodes(self, tmdb_id):
        """
        Obtiene la lista de episodios desde TMDB (solo episodios ya emitidos)
        
        Returns:
            dict: {season: [episode_numbers]}
        """
        from core.tmdb.api_client import TMDBClient
        from config.settings import Settings
        
        if not tmdb_id:
            return {}
        
        try:
            # Usar la API real de TMDB con filtro de episodios ya emitidos
            tmdb_client = TMDBClient(Settings.TMDB_API_KEY)
            episodes = tmdb_client.get_all_episodes(tmdb_id, only_aired=True)
            
            # Convertir formato de lista a dict {season: [episode_numbers]}
            tmdb_episodes = {}
            for episode in episodes:
                season = episode['season_number']
                episode_num = episode['episode_number']
                
                if season not in tmdb_episodes:
                    tmdb_episodes[season] = []
                
                tmdb_episodes[season].append(episode_num)
            
            # Ordenar episodios por número
            for season in tmdb_episodes:
                tmdb_episodes[season].sort()
            
            return tmdb_episodes
            
        except Exception as e:
            self.logger.error(f"Error obteniendo episodios de TMDB para serie {tmdb_id}: {e}")
            return {}
    
    def _calculate_average_file_size(self, table):
        """
        Calcula el tamaño de referencia de los archivos en una tabla T2.
        Para contenido multi-archivo, prioriza el primer archivo ya que su calidad
        determina la selección del conjunto completo.
        
        Args:
            table: Elemento BeautifulSoup de la tabla T2
            
        Returns:
            float: Tamaño de referencia en MB
        """
        file_sizes = []
        
        # Encontrar todas las filas de archivos (saltar header)
        rows = table.find_all('tr')[1:]
        
        if not rows:
            return 0
        
        # Para contenido multi-archivo, el primer archivo determina la calidad
        first_row = rows[0]
        first_file_size = self._extract_file_size_from_row(first_row)
        
        if first_file_size > 0:
            # Si el primer archivo tiene tamaño válido, usarlo como referencia principal
            file_sizes.append(first_file_size)
            self.logger.info(f"Primer archivo encontrado con tamaño: {first_file_size:.2f} MB (determina la calidad del conjunto)")
            
            # Para contenido multi-archivo, también considerar otros archivos pero con menor peso
            for row in rows[1:]:
                size_mb = self._extract_file_size_from_row(row)
                if size_mb > 0:
                    file_sizes.append(size_mb * 0.3)  # Peso reducido para archivos adicionales
        else:
            # Si el primer archivo no tiene tamaño explícito, procesar todos
            for row in rows:
                size_mb = self._extract_file_size_from_row(row)
                if size_mb > 0:
                    file_sizes.append(size_mb)
        
        # Si no encontramos tamaños explícitos, usar heurística basada en el primer archivo
        if not file_sizes and rows:
            first_row = rows[0]
            file_link = first_row.find('a')
            if file_link:
                filename = file_link.get_text(strip=True)
                estimated_size = self._estimate_size_from_filename(filename)
                file_sizes.append(estimated_size)
                self.logger.info(f"Tamaño estimado para primer archivo: {estimated_size:.2f} MB basado en nombre: {filename}")
        
        # Calcular tamaño de referencia (promedio ponderado)
        reference_size = sum(file_sizes) / len(file_sizes) if file_sizes else 0
        
        if len(rows) > 1:
            self.logger.info(f"Contenido multi-archivo detectado ({len(rows)} archivos). Tamaño de referencia: {reference_size:.2f} MB")
        else:
            self.logger.info(f"Archivo único. Tamaño: {reference_size:.2f} MB")
        
        return reference_size
    
    def _extract_file_size_from_row(self, row):
        """
        Extrae el tamaño de archivo de una fila de tabla T2
        
        Args:
            row: Elemento BeautifulSoup de la fila (tr)
            
        Returns:
            float: Tamaño en MB, 0 si no se encuentra
        """
        cells = row.find_all('td')
        
        # Buscar en todas las celdas el tamaño
        for cell in cells:
            # Verificar si la celda tiene clase SIZE (común en Hispashare)
            if 'SIZE' in cell.get('class', []):
                size_text = cell.get_text(strip=True)
                size_mb = self._parse_file_size(size_text)
                if size_mb > 0:
                    return size_mb
            
            # También buscar en celdas sin clase específica
            size_text = cell.get_text(strip=True)
            size_mb = self._parse_file_size(size_text)
            if size_mb > 0:
                return size_mb
        
        return 0
    
    def _parse_file_size(self, size_text):
        """
        Convierte texto de tamaño a MB
        
        Args:
            size_text: Texto con el tamaño (ej: "2.1 GB", "950 MB")
            
        Returns:
            float: Tamaño en MB
        """
        # Buscar patrones de tamaño
        size_pattern = r'(\d+(?:\.\d+)?)\s*(GB|MB|KB|gb|mb|kb)'
        match = re.search(size_pattern, size_text)
        
        if match:
            size_value = float(match.group(1))
            unit = match.group(2).upper()
            
            if unit == 'GB':
                return size_value * 1024  # Convertir GB a MB
            elif unit == 'MB':
                return size_value
            elif unit == 'KB':
                return size_value / 1024  # Convertir KB a MB
        
        return 0
    
    def _estimate_size_from_filename(self, filename):
        """
        Estima el tamaño del archivo basado en su nombre y calidad
        
        Args:
            filename: Nombre del archivo
            
        Returns:
            float: Tamaño estimado en MB
        """
        filename_lower = filename.lower()
        
        # Estimaciones basadas en resolución y codec
        if '2160p' in filename_lower or '4k' in filename_lower:
            if 'x265' in filename_lower or 'hevc' in filename_lower:
                return 8000  # ~8GB para 4K x265
            else:
                return 15000  # ~15GB para 4K x264
        elif '1080p' in filename_lower:
            if 'x265' in filename_lower or 'hevc' in filename_lower:
                return 1500  # ~1.5GB para 1080p x265
            else:
                return 2500  # ~2.5GB para 1080p x264
        elif '720p' in filename_lower:
            return 1000  # ~1GB para 720p
        else:
            return 800  # Tamaño por defecto

    def _get_quality_sections_with_scores(self, soup):
        """
        Obtiene todas las secciones de calidad ordenadas por tamaño de archivo.
        Prioriza secciones donde el primer archivo (que determina la calidad) es más grande.
        
        Args:
            soup: BeautifulSoup object de la página
            
        Returns:
            list: Lista de tuplas (section, file_size, quality_text) ordenadas por tamaño
        """
        quality_sections = []
        
        # Buscar todos los h2 que contienen información de calidad
        h2_elements = soup.find_all('h2')
        
        for h2 in h2_elements:
            quality_text = h2.get_text(strip=True)
            
            # Buscar la tabla T2 asociada a este h2
            # La tabla está después del h2, dentro del mismo contenedor
            parent = h2.parent
            if parent:
                table = parent.find('table', class_='T2')
                if table:
                    # Calcular el tamaño de referencia basado en el primer archivo
                    reference_size = self._calculate_average_file_size(table)
                    quality_sections.append((table, reference_size, quality_text))
                    self.logger.info(f"Calidad encontrada: {quality_text} (tamaño de referencia: {reference_size:.2f} MB)")
        
        # Ordenar por tamaño descendente (archivos más grandes primero)
        quality_sections.sort(key=lambda x: x[1], reverse=True)
        
        if quality_sections:
            self.logger.info(f"Secciones ordenadas por tamaño (mayor a menor):")
            for i, (_, size, quality) in enumerate(quality_sections, 1):
                self.logger.info(f"  {i}. {quality}: {size:.2f} MB")
        
        return quality_sections
    
    def process_download_page(self, hispashare_url, missing_episodes, air_dates=None):
        """
        Procesa la página de descarga de Hispashare para obtener enlaces ed2k
        
        Args:
            hispashare_url: URL de la serie en Hispashare
            missing_episodes: dict con episodios faltantes {season: [episodes]}
            
        Returns:
            list: Lista de enlaces ed2k de episodios faltantes
        """
        try:
            self.logger.info(f"Procesando página de descarga: {hispashare_url}")
            self.driver.get(hispashare_url)
            
            # Esperar a que la página cargue
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.CLASS_NAME, "WIN1_CONTAINER"))
            )
            
            # Obtener el HTML de la página
            page_source = self.driver.page_source
            soup = BeautifulSoup(page_source, 'html.parser')
            
            # Obtener secciones de calidad ordenadas por tamaño
            quality_sections = self._get_quality_sections_with_scores(soup)
            
            if not quality_sections:
                self.logger.warning("No se encontraron secciones de calidad")
                return []
            
            # Verificar si hay calidad 4K/2160p disponible
            has_4k_section = self._check_for_4k_quality(quality_sections)
            
            if has_4k_section and Settings.WAIT_FOR_4K:
                self.logger.info("🎬 Calidad 4K/2160p detectada - Aplicando lógica de espera por mejor calidad")
                filtered_missing = missing_episodes
                if air_dates is not None:
                    filtered_missing, skipped, total = self._filter_missing_by_min_age(
                        missing_episodes,
                        air_dates,
                        getattr(Settings, 'WAIT_FOR_4K_MAX_WAIT_DAYS', 7)
                    )
                    if skipped and total:
                        self.logger.info(f"⏳ {skipped}/{total} episodios recientes omitidos por WAIT_FOR_4K_MAX_WAIT_DAYS")
                    if not filtered_missing:
                        self.logger.warning("⏳ Todos los episodios faltantes son recientes - Esperando por 4K")
                        self.logger.info("💡 Ajusta WAIT_FOR_4K_MAX_WAIT_DAYS o desactiva WAIT_FOR_4K en .env si quieres descargar ya")
                        return []

                return self._process_with_4k_priority(quality_sections, filtered_missing)
            else:
                self.logger.info("📺 Procesando con lógica estándar (sin espera por 4K)")
                return self._process_standard_quality_selection(quality_sections, missing_episodes)
            
        except Exception as e:
            self.logger.error(f"Error procesando página de descarga: {e}")
            return []
    
    def _check_for_4k_quality(self, quality_sections):
        """
        Verifica si hay alguna sección con calidad 4K/2160p disponible
        
        Args:
            quality_sections: Lista de secciones de calidad
            
        Returns:
            bool: True si hay calidad 4K disponible
        """
        for _, _, quality_text in quality_sections:
            quality_lower = quality_text.lower()
            if any(keyword in quality_lower for keyword in ['2160p', '4k', 'uhd', 'ultra']):
                self.logger.info(f"✨ Calidad 4K detectada: {quality_text}")
                return True
        return False
    
    def _process_with_4k_priority(self, quality_sections, missing_episodes):
        """
        Procesa las secciones priorizando 4K y evitando descargas de calidades inferiores
        si hay 4K disponible pero sin los episodios buscados
        
        Args:
            quality_sections: Lista de secciones de calidad ordenadas
            missing_episodes: Episodios faltantes
            
        Returns:
            list: Enlaces ed2k o lista vacía si espera por 4K
        """
        # Separar secciones 4K de las demás
        k4_sections = []
        other_sections = []
        
        for section, file_size, quality_text in quality_sections:
            quality_lower = quality_text.lower()
            if any(keyword in quality_lower for keyword in ['2160p', '4k', 'uhd', 'ultra']):
                k4_sections.append((section, file_size, quality_text))
            else:
                other_sections.append((section, file_size, quality_text))
        
        # Primero intentar con secciones 4K
        for section, file_size, quality_text in k4_sections:
            self.logger.info(f"🎯 Procesando calidad 4K: {quality_text} (tamaño: {file_size:.2f} MB)")
            section_links = self._process_quality_section(section, missing_episodes, quality_text)
            
            if section_links:
                self.logger.info(f"✅ Encontrados {len(section_links)} enlaces en 4K: {quality_text}")
                return section_links
            else:
                self.logger.info(f"⏳ No se encontraron episodios faltantes en 4K: {quality_text}")
        
        # Si hay secciones 4K pero no tienen los episodios, decidir si esperar o descargar calidad inferior
        if k4_sections:
            self.logger.warning("🚫 Hay calidad 4K disponible pero no contiene los episodios buscados")
            self.logger.warning("⬇️ Descargando la mejor calidad disponible (no 4K) para los episodios elegibles")
            return self._process_standard_quality_selection(other_sections, missing_episodes)
        
        # Si no hay 4K, procesar normalmente
        return self._process_standard_quality_selection(other_sections, missing_episodes)
    
    def _process_standard_quality_selection(self, quality_sections, missing_episodes):
        """
        Procesa las secciones con la lógica estándar (mejor calidad disponible)
        
        Args:
            quality_sections: Lista de secciones de calidad
            missing_episodes: Episodios faltantes
            
        Returns:
            list: Enlaces ed2k encontrados
        """
        # Procesar solo la mejor calidad que tenga episodios faltantes
        for section, file_size, quality_text in quality_sections:
            self.logger.info(f"Procesando calidad: {quality_text} (tamaño: {file_size:.2f} MB)")
            
            # Procesar esta sección de calidad
            section_links = self._process_quality_section(section, missing_episodes, quality_text)
            
            # Si encontramos enlaces en esta calidad, no procesar calidades inferiores
            if section_links:
                self.logger.info(f"✅ Encontrados {len(section_links)} enlaces en la mejor calidad disponible: {quality_text}")
                return section_links
            else:
                self.logger.info(f"No se encontraron episodios faltantes en: {quality_text}")
        
        self.logger.warning("No se encontraron episodios faltantes en ninguna calidad")
        return []
    
    def _check_language_compatibility(self, section_parent):
        """
        Verifica si el idioma de la sección es compatible con las preferencias del usuario.
        Rechaza contenido que tenga 'english' como idioma principal.
        
        Args:
            section_parent: Elemento padre que contiene la información de idioma
            
        Returns:
            tuple: (is_compatible, language_info) donde is_compatible es bool y language_info es str
        """
        try:
            language_element = None
            
            # Estrategia 1: Buscar párrafo con clase INFO que contenga "Idiomas:"
            language_paragraph = section_parent.find('p', class_='INFO')
            if language_paragraph and 'Idiomas:' in language_paragraph.get_text():
                language_element = language_paragraph
            
            # Estrategia 2: Buscar celda td que contenga "Idioma:" (formato más común)
            if not language_element:
                language_cells = section_parent.find_all('td')
                for cell in language_cells:
                    if 'Idioma:' in cell.get_text():
                        language_element = cell
                        break
            
            # Estrategia 3: Buscar en elementos hermanos
            if not language_element:
                current = section_parent
                for _ in range(5):  # Buscar en los siguientes 5 elementos
                    current = current.find_next_sibling()
                    if current:
                        # Buscar párrafo INFO
                        language_paragraph = current.find('p', class_='INFO')
                        if language_paragraph and 'Idiomas:' in language_paragraph.get_text():
                            language_element = language_paragraph
                            break
                        # Buscar celda td con idioma
                        language_cells = current.find_all('td')
                        for cell in language_cells:
                            if 'Idioma:' in cell.get_text():
                                language_element = cell
                                break
                        if language_element:
                            break
            
            # Estrategia 4: Búsqueda recursiva más amplia
            if not language_element:
                # Buscar en todo el árbol del elemento padre
                all_elements = section_parent.find_all(['p', 'td', 'div'])
                for element in all_elements:
                    text = element.get_text()
                    if 'Idioma:' in text or 'Idiomas:' in text:
                        language_element = element
                        break
            
            if not language_element:
                self.logger.warning("⚠️ No se encontró información de idioma - Asumiendo compatible")
                return True, "Sin información de idioma"
            
            # Extraer todas las banderas de idioma
            language_flags = language_element.find_all('img', class_='FLAGM')
            if not language_flags:
                language_flags = language_element.find_all('img', class_='FLAG')
            
            languages = []
            for flag in language_flags:
                lang = flag.get('alt') or flag.get('title', '')
                if lang:
                    languages.append(lang.lower())
            
            language_info = ', '.join(languages) if languages else "Sin idiomas detectados"
            self.logger.info(f"🌍 Idiomas detectados: {language_info}")
            
            # Verificar que tenga español o catalán como IDIOMA DE AUDIO
            if any(lang in languages for lang in ['spanish', 'catalan', 'catala']):
                self.logger.info(f"✅ Idioma compatible - Audio en español/catalán: {language_info}")
                return True, language_info
            
            # Si tiene subtítulos españoles (spanishsub o spanishfor) también es válido
            if any(lang in languages for lang in ['spanishsub', 'spanishfor']):
                self.logger.info(f"✅ Idioma compatible - Subtítulos en español: {language_info}")
                return True, language_info
            
            # Si no tiene ningún idioma español/catalán ni subtítulos españoles, rechazar
            self.logger.warning(f"🚫 Contenido rechazado - Sin español ni catalán: {language_info}")
            return False, language_info
            
        except Exception as e:
            self.logger.error(f"Error verificando idioma: {e}")
            # En caso de error, ser conservador y rechazar
            return False, f"Error verificando idioma: {e}"

    def _process_quality_section(self, section, missing_episodes, quality_text=None):
        """
        Procesa una sección de calidad específica para encontrar episodios faltantes
        
        Args:
            section: Elemento BeautifulSoup de la tabla T2
            missing_episodes: dict con episodios faltantes
            quality_text: Texto de la calidad para logs
            
        Returns:
            list: Enlaces ed2k de esta sección
        """
        ed2k_links = []
        quality_info = f" ({quality_text})" if quality_text else ""
        
        # Verificar compatibilidad de idioma antes de procesar
        section_parent = section.parent if section.parent else section
        is_compatible, language_info = self._check_language_compatibility(section_parent)
        
        if not is_compatible:
            self.logger.warning(f"🚫 Sección omitida por idioma incompatible{quality_info}: {language_info}")
            return []
        
        self.logger.info(f"🌍 Idioma verificado{quality_info}: {language_info}")
        
        # Encontrar todas las filas de episodios
        episode_rows = section.find_all('tr')[1:]  # Saltar header
        
        checkboxes_to_select = []
        
        for row in episode_rows:
            # Extraer información del episodio
            checkbox = row.find('input', type='checkbox')
            if not checkbox:
                continue
            
            episode_link = row.find('a')
            if not episode_link:
                continue
            
            episode_title = episode_link.text.strip()
            
            # Parsear temporada y episodio del título
            episode_info = self.parser.parse_episode(episode_title)
            if not episode_info:
                continue
            
            season = episode_info['season']
            episode = episode_info['episode']
            
            # Verificar si este episodio está en la lista de faltantes
            if season in missing_episodes and episode in missing_episodes[season]:
                checkbox_id = checkbox.get('id')
                if checkbox_id:
                    checkboxes_to_select.append(checkbox_id)
                    self.logger.info(f"Episodio faltante encontrado{quality_info}: {episode_title}")
        
        # Seleccionar checkboxes de episodios faltantes
        if checkboxes_to_select:
            self.logger.info(f"Seleccionando {len(checkboxes_to_select)} episodios en calidad{quality_info}")
            ed2k_links = self._select_and_get_links(checkboxes_to_select, section, quality_text)
        else:
            self.logger.info(f"No hay episodios faltantes en esta calidad{quality_info}")
        
        return ed2k_links
    
    def _select_and_get_links(self, checkbox_ids, section, quality_text=None):
        """
        Selecciona checkboxes y obtiene los enlaces ed2k
        
        Args:
            checkbox_ids: Lista de IDs de checkboxes a seleccionar
            section: Sección HTML donde están los checkboxes
            quality_text: Texto de la calidad para logs
            
        Returns:
            list: Enlaces ed2k
        """
        ed2k_links = []
        quality_info = f" ({quality_text})" if quality_text else ""
        
        try:
            # Seleccionar cada checkbox
            for checkbox_id in checkbox_ids:
                try:
                    checkbox = self.driver.find_element(By.ID, checkbox_id)
                    if not checkbox.is_selected():
                        self.driver.execute_script("arguments[0].click();", checkbox)
                        self.logger.info(f"Checkbox seleccionado{quality_info}: {checkbox_id}")
                except NoSuchElementException:
                    self.logger.warning(f"Checkbox no encontrado{quality_info}: {checkbox_id}")
                    continue
            
            # Extraer el ID del grupo desde los checkboxes seleccionados
            # Los checkboxes tienen formato: elink_GROUPID_X
            group_id = None
            if checkbox_ids:
                # Extraer group_id del primer checkbox
                match = re.match(r'elink_(\d+)_\d+', checkbox_ids[0])
                if match:
                    group_id = match.group(1)
                    self.logger.info(f"ID de grupo detectado{quality_info}: {group_id}")
            
            if not group_id:
                self.logger.error(f"No se pudo extraer el ID del grupo de los checkboxes{quality_info}")
                return ed2k_links
            
            # Buscar el botón "Mostrar seleccionados" específico de este grupo
            try:
                # Primero intentar encontrar el botón con múltiples estrategias
                show_button = None
                
                # Estrategia 1: Buscar por onclick exacto
                try:
                    button_xpath_1 = f"//a[@onclick='javascript:DownloadSelected({group_id}, 4)']"
                    show_button = self.driver.find_element(By.XPATH, button_xpath_1)
                    self.logger.info(f"Botón encontrado con estrategia 1 para grupo {group_id}{quality_info}")
                except NoSuchElementException:
                    pass
                
                # Estrategia 2: Buscar por onclick que contenga el group_id
                if not show_button:
                    try:
                        button_xpath_2 = f"//a[contains(@onclick, 'DownloadSelected({group_id},')]"
                        show_button = self.driver.find_element(By.XPATH, button_xpath_2)
                        self.logger.info(f"Botón encontrado con estrategia 2 para grupo {group_id}{quality_info}")
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
                            self.logger.info(f"Botón encontrado con estrategia 3 para grupo {group_id}{quality_info}")
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
                                self.logger.info(f"Botón encontrado con estrategia 4 para grupo {group_id}{quality_info}")
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
                    self.logger.info(f"Botón 'Mostrar seleccionados' ejecutado para grupo {group_id}{quality_info}")
                    
                    # Esperar un poco para que se procese la acción
                    time.sleep(3)
                    
                    # Buscar el popup modal que aparece con los enlaces
                    try:
                        # Esperar a que aparezca el popup con los enlaces
                        WebDriverWait(self.driver, 15).until(
                            EC.presence_of_element_located((By.ID, "ELINKSLIST"))
                        )
                        
                        # Obtener el contenido del textarea con los enlaces ed2k
                        textarea = self.driver.find_element(By.ID, "ELINKSLIST")
                        links_text = textarea.get_attribute('value')
                        
                        if links_text:
                            # Dividir por líneas y filtrar enlaces ed2k válidos
                            lines = links_text.strip().split('\n')
                            for line in lines:
                                line = line.strip()
                                if line.startswith('ed2k://'):
                                    if line not in ed2k_links:  # Evitar duplicados
                                        ed2k_links.append(line)
                                        self.logger.info(f"Enlace ed2k obtenido{quality_info}: {line[:80]}...")
                        
                        # Cerrar el popup
                        try:
                            close_button = self.driver.find_element(By.XPATH, "//div[@class='X']")
                            self.driver.execute_script("arguments[0].click();", close_button)
                            self.logger.info(f"Popup cerrado{quality_info}")
                        except:
                            # Si no se puede cerrar, no es crítico
                            self.logger.warning(f"No se pudo cerrar el popup{quality_info}")
                                
                    except TimeoutException:
                        self.logger.warning(f"Timeout esperando popup con enlaces ed2k{quality_info}")
                        
                        # Intentar buscar enlaces ed2k directamente en la página
                        ed2k_elements = self.driver.find_elements(By.XPATH, "//a[starts-with(@href, 'ed2k://')]")
                        for element in ed2k_elements:
                            ed2k_link = element.get_attribute('href')
                            if ed2k_link and ed2k_link not in ed2k_links:
                                ed2k_links.append(ed2k_link)
                                self.logger.info(f"Enlace ed2k obtenido (fallback){quality_info}: {ed2k_link[:80]}...")
                else:
                    self.logger.error(f"No se pudo encontrar el botón 'Mostrar seleccionados' para grupo {group_id} con ninguna estrategia{quality_info}")
                    
                    # Debug: Listar todos los botones disponibles
                    try:
                        all_buttons = self.driver.find_elements(By.XPATH, "//a[contains(@onclick, 'DownloadSelected')]")
                        self.logger.info(f"Botones DownloadSelected encontrados: {len(all_buttons)}")
                        for i, button in enumerate(all_buttons):
                            onclick_attr = button.get_attribute('onclick')
                            self.logger.info(f"Botón {i+1}: {onclick_attr}")
                        
                        # Debug adicional: Guardar HTML de la página para análisis
                        page_source = self.driver.page_source
                        debug_file = f"debug_page_{group_id}.html"
                        with open(debug_file, 'w', encoding='utf-8') as f:
                            f.write(page_source)
                        self.logger.info(f"HTML de la página guardado en {debug_file} para análisis")
                        
                    except Exception as debug_e:
                        self.logger.error(f"Error en debug de botones: {debug_e}")
                    
            except Exception as e:
                self.logger.error(f"Error general buscando botón 'Mostrar seleccionados'{quality_info}: {e}")
            
        except Exception as e:
            self.logger.error(f"Error general obteniendo enlaces ed2k{quality_info}: {e}")
        
        self.logger.info(f"Total enlaces ed2k obtenidos en esta sección{quality_info}: {len(ed2k_links)}")
        return ed2k_links
    
    def download_missing_episodes(self, series_id):
        """
        Proceso completo para descargar episodios faltantes de una serie
        
        Args:
            series_id: ID de la serie
            
        Returns:
            list: Enlaces ed2k de episodios faltantes
        """
        # Obtener información de la serie
        series = self.db_manager.get_series_by_id(series_id)
        if not series or not series.get('hispashare_url'):
            self.logger.error(f"Serie {series_id} no tiene URL de Hispashare")
            return []
        
        rows = self.db_manager.get_missing_episodes_for_series(series_id)
        if not rows:
            self.logger.info(f"No hay episodios faltantes para la serie {series['name']}")
            return []

        missing_episodes, air_dates = self._build_missing_episodes_from_rows(rows)
        if not missing_episodes:
            self.logger.info(f"No hay episodios faltantes procesables para la serie {series['name']}")
            return []

        self.logger.info(f"Episodios faltantes para {series['name']}: {missing_episodes}")
        
        # Procesar página de descarga
        ed2k_links = self.process_download_page(series['hispashare_url'], missing_episodes, air_dates=air_dates)
        
        if ed2k_links:
            self.logger.info(f"Se obtuvieron {len(ed2k_links)} enlaces ed2k para {series['name']}")
            
            # Guardar enlaces en la base de datos o archivo
            self._save_ed2k_links(series_id, ed2k_links)
        
        return ed2k_links
    
    def _save_ed2k_links(self, series_id, ed2k_links):
        """
        Guarda los enlaces ed2k y opcionalmente los envía a eMule
        
        Args:
            series_id: ID de la serie
            ed2k_links: Lista de enlaces ed2k
        """
        try:
            # Crear directorio de descargas si no existe
            downloads_dir = Path("downloads")
            downloads_dir.mkdir(exist_ok=True)
            
            # Obtener nombre de la serie
            series = self.db_manager.get_series_by_id(series_id)
            series_name = series['name'] if series else f"series_{series_id}"
            
            # Limpiar nombre para archivo
            safe_name = re.sub(r'[<>:"/\\|?*]', '_', series_name)
            
            # Guardar enlaces en archivo
            links_file = downloads_dir / f"{safe_name}_ed2k_links.txt"
            
            with open(links_file, 'w', encoding='utf-8') as f:
                f.write(f"Enlaces ed2k para: {series_name}\n")
                f.write(f"Fecha: {self._get_current_timestamp()}\n")
                f.write("-" * 50 + "\n\n")
                
                for i, link in enumerate(ed2k_links, 1):
                    f.write(f"{i}. {link}\n")
            
            self.logger.info(f"Enlaces ed2k guardados en: {links_file}")
            
            # Enviar a eMule si está configurado y habilitado
            if self.settings.EMULE_AUTO_SEND:
                if self.emule_client.is_configured():
                    self.logger.info(f"Enviando {len(ed2k_links)} enlaces a eMule...")
                    result = self.emule_client.add_multiple_ed2k_links(ed2k_links)
                    
                    if result['success']:
                        self.logger.info(f"✅ {result['added']}/{result['total']} enlaces enviados a eMule")
                    else:
                        self.logger.warning(f"❌ Error enviando enlaces a eMule: {result.get('message', 'Error desconocido')}")
                else:
                    # Verificar qué configuración específica falta
                    emule_config = self.settings.get_emule_config()
                    missing_config = []
                    
                    if not emule_config.get('url'):
                        missing_config.append("URL del servidor web")
                    if not emule_config.get('username'):
                        missing_config.append("nombre de usuario")
                    
                    if missing_config:
                        self.logger.warning(f"eMule no está configurado correctamente. Falta: {', '.join(missing_config)}")
                    else:
                        self.logger.warning("eMule no está configurado correctamente para envío automático")
            
        except Exception as e:
            self.logger.error(f"Error guardando enlaces ed2k: {e}")
    
    def send_links_to_emule(self, series_id):
        """
        Envía manualmente los enlaces de una serie a eMule
        
        Args:
            series_id: ID de la serie
            
        Returns:
            dict: Resultado de la operación
        """
        try:
            if not self.emule_client.is_configured():
                return {
                    'success': False,
                    'error': 'eMule no está configurado correctamente'
                }
            
            # Obtener nombre de la serie
            series = self.db_manager.get_series_by_id(series_id)
            if not series:
                return {
                    'success': False,
                    'error': 'Serie no encontrada'
                }
            
            series_name = series['name']
            safe_name = re.sub(r'[<>:"/\\|?*]', '_', series_name)
            
            # Buscar archivo de enlaces
            downloads_dir = Path("downloads")
            links_file = downloads_dir / f"{safe_name}_ed2k_links.txt"
            
            if not links_file.exists():
                return {
                    'success': False,
                    'error': 'No se encontraron enlaces para esta serie'
                }
            
            # Leer enlaces del archivo
            with open(links_file, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Extraer enlaces ed2k
            ed2k_links = self.emule_client.extract_ed2k_links_from_text(content)
            
            if not ed2k_links:
                return {
                    'success': False,
                    'error': 'No se encontraron enlaces ed2k válidos en el archivo'
                }
            
            # Enviar enlaces a eMule
            result = self.emule_client.add_multiple_ed2k_links(ed2k_links)
            
            return result
            
        except Exception as e:
            self.logger.error(f"Error enviando enlaces a eMule: {e}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def _get_current_timestamp(self):
        """Obtiene timestamp actual formateado"""
        from datetime import datetime
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
