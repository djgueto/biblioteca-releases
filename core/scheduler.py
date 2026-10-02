import time
import logging
from datetime import datetime
from pathlib import Path
import re

from core.auth.hispashare_login import HispashareAuth
from core.library.scanner import LibraryScanner
from core.library.category_scanner import CategoryLibraryScanner
from core.tmdb.api_client import TMDBClient
from core.tmdb.auto_matcher import TMDBAutoMatcher
from core.hispashare.searcher import HispashareSearcher
from core.hispashare.content_scanner import ContentScanner
from core.wolfmax.category_scanner import WolfmaxCategoryScanner, WOLFMAX_CATEGORY_URLS, _normalize, _title_score
from core.wolfmax.downloader import is_current_url as is_current_wolfmax_url
from core.library.parser import detect_resolution_from_file
from core.database.db_manager import DatabaseManager
from core.events import SOURCE_URL_SAVED, TMDB_MATCHED
from core.jobs import BackgroundJob
from core.repositories import (
    DecisionLogRepository,
    SeriesRepository,
    SourceOfferRepository,
    SourceRepository,
)
from core.services import (
    MissingEpisodesService,
    ReconciliationService,
    SourceArbitrationService,
    SourceMatchService,
)
from core.services.providers import ProviderUnavailable, build_provider_registry
from core.services.quality import quality_from_local_episode
from core.services.quality_profile import TIERS, tier_of
from core.repositories.item_quality_repository import ItemQualityRepository
from core.repositories.grab_history_repository import GrabHistoryRepository
from core.library.item_quality import detect_item_quality
from config.settings import Settings

class Scheduler(BackgroundJob):
    """Ciclo periódico de escaneo, matching, búsqueda y descargas.

    Además de los eventos de BackgroundJob publica SOURCE_URL_SAVED y
    TMDB_MATCHED. Al compartir bus con los servicios, también llegan los
    SERIES_UPDATED de ReconciliationService (sin "source").
    """

    name = "scheduler"

    def __init__(self, event_bus=None):
        super().__init__(event_bus)
        self.logger = logging.getLogger(__name__)
        self.db = DatabaseManager()
        self.series_repository = SeriesRepository(self.db)
        self.source_repository = SourceRepository(self.db)
        self.source_offer_repository = SourceOfferRepository(self.db)
        self.decision_log_repository = DecisionLogRepository(self.db)
        self.item_quality_repository = ItemQualityRepository(self.db)
        self.grab_history_repository = GrabHistoryRepository(self.db)
        self.missing_episodes_service = MissingEpisodesService(
            self.db,
            self.series_repository,
            self.decision_log_repository,
        )
        self.source_match_service = SourceMatchService(
            self.db,
            self.source_repository,
            self.decision_log_repository,
        )
        self.reconciliation_service = ReconciliationService(
            self.series_repository,
            self.source_repository,
            self.missing_episodes_service,
            self.source_match_service,
            self.decision_log_repository,
            event_bus=self.event_bus,
            source_offer_repository=self.source_offer_repository,
        )
        self.current_driver = None  # Mantener referencia al driver actual
        # Se (re)construyen en cada execute_cycle(): HispashareSourceProvider
        # necesita el driver Selenium+login de ese ciclo concreto, así que no
        # pueden vivir en __init__ (el driver se cierra al final de cada ciclo).
        self.providers = {}
        self.source_arbitration_service = SourceArbitrationService(
            self.db,
            self.source_offer_repository,
            self.providers,
            event_bus=self.event_bus,
        )
    
    def run(self):
        """Ejecutar ciclo principal"""
        self.logger.info("Scheduler iniciado")
        while self.running:
            try:
                self.execute_cycle()
                
                # Esperar intervalo configurado
                hours = Settings.CHECK_INTERVAL_HOURS
                self._log(f"⏰ Próximo ciclo en {hours} horas")
                
                # Dormir en intervalos pequeños para poder detener el thread rápido
                sleep_time = hours * 3600
                elapsed = 0
                while elapsed < sleep_time and self.running:
                    time.sleep(1)
                    elapsed += 1
                
            except Exception as e:
                self.logger.error(f"Error en ciclo: {e}", exc_info=True)
                self._error(str(e))
                self._log(f"❌ ERROR: {str(e)}")
                # Esperar antes de reintentar, pero verificar si seguimos ejecutándose
                for _ in range(1800):  # 30 minutos en segundos
                    if not self.running:
                        break
                    time.sleep(1)
        
        self.logger.info("Scheduler detenido correctamente")
    
    def execute_cycle(self):
        """Ejecutar un ciclo completo"""
        self._log("=" * 60)
        self._log(f"🚀 INICIO CICLO - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        self._log("=" * 60)
        
        driver = None
        try:
            # 1. Login Hispashare
            self._progress(5, "🔐 Login Hispashare...")
            self._log("🔐 Iniciando sesión en Hispashare...")
            driver = self.login_hispashare()
            self.current_driver = driver  # Mantener referencia
            # Reconstruir el registro de proveedores con el driver de este
            # ciclo: HispashareSourceProvider no puede sobrevivir a un ciclo
            # porque el driver se cierra en el finally de execute_cycle.
            self.providers.clear()
            self.providers.update(build_provider_registry(
                self.db,
                hispashare_driver=driver,
                include_wolfmax=getattr(Settings, 'WOLFMAX_UPGRADE_ENABLED', False),
            ))
            if not self.running:
                return
            
            # 2. Limpiar series eliminadas
            self._progress(15, "🧹 Limpiando series eliminadas...")
            self._log("🧹 Limpiando series eliminadas del disco...")
            cleaned_count = self.db.cleanup_deleted_series()
            self._log(f"   ✅ {cleaned_count} series eliminadas")
            if not self.running:
                return
            
            # 2.1. Limpiar episodios faltantes huérfanos
            self._log("🧹 Limpiando episodios faltantes huérfanos...")
            orphan_count = self.db.cleanup_orphaned_missing_episodes()
            self._log(f"   ✅ {orphan_count} episodios huérfanos eliminados")

            # 3. Escanear biblioteca
            self._progress(25, "📁 Escaneando biblioteca...")
            self._log("📁 Escaneando biblioteca local...")
            series_list = self.scan_library()
            self._log(f"   ✅ {len(series_list)} series encontradas")
            if not self.running:
                return

            self._log("🎞️ Escaneando bibliotecas de archivo unico...")
            single_file_items = self.scan_single_file_library()
            self._log(f"   ✅ {len(single_file_items)} elementos de archivo unico encontrados")
            if not self.running:
                return
            
            # 4. Limpiar duplicados de series tras la sincronización de escaneos
            self._progress(35, "🔧 Consolidando duplicados...")
            self._log("🔧 Limpiando series duplicadas...")
            cleaned_series = self.db.cleanup_duplicate_series()
            self._log(f"   ✅ {cleaned_series} series duplicadas eliminadas")
            
            # 5. Vincular con TMDB
            self._progress(45, "💾 Sincronización completada...")
            self._log("💾 Base de datos sincronizada durante el escaneo")
            self._progress(55, "🎬 Vinculando con TMDB...")
            self._log("🎬 Vinculando bibliotecas con TMDB...")
            db_series_list = self.db.get_all_series()
            self.match_tmdb(db_series_list)
            if not self.running:
                return

            # 5.1. Novedades de TMDB/IMDb que cumplen las notas mínimas
            self._progress(60, "🆕 Revisando novedades...")
            self._log("🆕 Revisando novedades de TMDB/IMDb...")
            self.run_discovery()
            
            # 6. Detectar episodios faltantes
            self._progress(65, "🔍 Detectando episodios faltantes...")
            self._log("🔍 Detectando episodios faltantes...")
            missing_count = self.detect_missing()
            self._log(f"   ✅ {missing_count} episodios faltantes detectados")

            # 6.1. Buscar URLs para series nuevas sin episodios faltantes
            self._progress(70, "🔎 Buscando URLs para series nuevas...")
            self._log("🔎 Buscando URLs en Hispashare para series nuevas sin episodios faltantes...")
            self.search_hispashare_for_new_series(driver)

            self._progress(73, "🔎 Buscando URLs de bibliotecas...")
            self._log("🔎 Buscando URLs en Hispashare para bibliotecas de archivo unico...")
            self.search_hispashare_for_single_file_items(driver)

            # 8. Buscar en Hispashare
            self._progress(75, "🔎 Buscando en Hispashare...")
            self._log("🔎 Buscando URLs en Hispashare...")
            self.search_hispashare(driver)
            
            # 8.1. Proveedores sin catálogo propio (DonTorrent): buscar ficha
            # para las series con faltantes que aún no tienen oferta suya.
            self._progress(80, "🔎 Buscando en otras fuentes...")
            self.discover_search_offers()
            if not self.running:
                return

            # 9. Descargar episodios faltantes
            self._progress(85, "⬇️ Procesando descargas...")
            self._log("⬇️ Procesando descargas de episodios faltantes...")
            self.process_downloads(driver)
            if not self.running:
                return
            self.process_wanted_movies()
            if not self.running:
                return

            # 9.1. Detectar resolución real de archivos sin metadatos en el nombre
            self._progress(88, "🔬 Detectando resoluciones reales...")
            self.scan_missing_resolutions()
            self.scan_item_qualities()

            # 9.2. Mejorar calidad (upgrades) de episodios existentes
            if getattr(Settings, 'UPGRADE_ENABLED', False):
                self._progress(90, "🔼 Mejorando calidad...")
                self._log("🔼 Buscando upgrades de calidad (ej. 2160p)...")
                self.process_upgrades(driver)
            
            # 10. Barrido de nuevo contenido en Hispashare
            self._progress(93, "🔍 Buscando nuevo contenido...")
            self._log("🔍 Iniciando barrido de nuevo contenido en Hispashare...")
            self.scan_new_content(driver)

            # 11. Descubrimiento de contenido en Wolfmax4k
            if getattr(Settings, 'WOLFMAX_UPGRADE_ENABLED', False):
                self._progress(97, "🐺 Sincronizando catálogo Wolfmax4k...")
                self._log("🐺 Escaneando categorías Wolfmax4k...")
                self.scan_wolfmax_categories()
            
            self._progress(100, "✅ Ciclo completado")
            self._log("✅ CICLO COMPLETADO CON ÉXITO")
            self._log("=" * 60)
            
            self._finished()
            
        except Exception as e:
            self.logger.error(f"Error en execute_cycle: {e}", exc_info=True)
            raise
        finally:
            # Asegurar que el navegador se cierre siempre
            if driver:
                try:
                    driver.quit()
                    self.logger.info("Driver cerrado correctamente")
                except Exception as e:
                    self.logger.error(f"Error cerrando driver: {e}")
            self.current_driver = None
    
    def login_hispashare(self):
        """Iniciar sesión en Hispashare"""
        try:
            auth = HispashareAuth(
                Settings.HISPASHARE_USER,
                Settings.HISPASHARE_PASS,
                headless=Settings.SELENIUM_HEADLESS
            )
            
            if auth.login():
                self._log("   ✅ Login exitoso")
                return auth.driver
            else:
                raise Exception("Login fallido en Hispashare")
                
        except Exception as e:
            self.logger.error(f"Error en login: {e}")
            raise
    
    def scan_library(self):
        """Escanear biblioteca de series"""
        try:
            series_list = LibraryScanner.scan_all_episodic_categories(db_manager=self.db)
            
            # Limpiar duplicados existentes antes de procesar nuevas series
            self._log("🧹 Limpiando episodios duplicados...")
            cleaned_count = self.db.cleanup_duplicate_episodes()
            if cleaned_count > 0:
                self._log(f"   ✅ {cleaned_count} episodios duplicados eliminados")
            
            # Guardar en base de datos
            for series in series_list:
                self.db.insert_or_update_series(series)
            
            return series_list
            
        except Exception as e:
            self.logger.error(f"Error en scan: {e}")
            raise

    def scan_single_file_library(self):
        """Escanear bibliotecas de contenido de archivo unico."""
        try:
            discovered_items = []

            for category in Settings.get_single_file_categories():
                scanner = CategoryLibraryScanner(
                    category,
                    Settings.get_paths_for_category(category),
                    skip_paths=self.db.get_unmonitored_folder_paths(),
                )
                items = scanner.scan()
                for item in items:
                    item_data = {
                        'name': item.get('name', ''),
                        'folder_path': item.get('path', ''),
                        'content_category': category,
                    }
                    self.db.insert_or_update_series(item_data)
                    discovered_items.append(item_data)

            return discovered_items

        except Exception as e:
            self.logger.error(f"Error escaneando bibliotecas de archivo unico: {e}")
            raise
    
    def match_tmdb(self, series_list):
        """Vincular series con TMDB"""
        try:
            tmdb_client = TMDBClient(Settings.TMDB_API_KEY)
            matcher = TMDBAutoMatcher(tmdb_client)
            
            matched = 0
            for series in series_list:
                # Si ya tiene TMDB ID, saltar
                if series.get('tmdb_id'):
                    continue
                
                content_type = Settings.get_category_label(series.get('content_category', 'series'))
                self._log(f"   🔗 Matching [{content_type}]: {series['name']}...")

                try:
                    match_result = matcher.match_content(series)

                    if match_result:
                        series['tmdb_id'] = match_result['tmdb_id']
                        series['year'] = match_result['year']
                        series['confidence_score'] = match_result['confidence_score']
                        series['needs_review'] = match_result['needs_review']

                        update_result = self.series_repository.update_tmdb_identity(series['id'], match_result)
                        self.reconciliation_service.on_tmdb_changed(
                            series['id'],
                            update_result.get('previous_item') or {},
                            update_result.get('updated_item') or {},
                            is_library=(
                                Settings.get_category_content_mode(series.get('content_category', 'series'))
                                == 'single_file'
                            ),
                            rebuild_missing=False,
                        )
                        self.decision_log_repository.append(
                            series['id'],
                            'tmdb_scheduler_matched',
                            'TMDB asignado desde el scheduler',
                            {
                                'tmdb_id': match_result.get('tmdb_id'),
                                'name': match_result.get('name'),
                                'year': match_result.get('year'),
                                'confidence_score': match_result.get('confidence_score'),
                            },
                        )
                        matched += 1

                        # Emitir señal para actualizar la interfaz en tiempo real
                        self._emit(TMDB_MATCHED, series_id=series['id'], match=match_result)

                        confidence_emoji = "✅" if match_result['confidence_score'] >= 0.85 else "⚠️"
                        self._log(
                            f"      {confidence_emoji} {match_result['name']} "
                            f"({match_result['year']}) - "
                            f"Confianza: {match_result['confidence_score']:.1%}"
                        )
                    else:
                        self._log(f"      ❌ No se encontró match")
                except Exception as series_error:
                    self.logger.error(f"Error haciendo match TMDB de '{series.get('name')}': {series_error}")
                    self._log(f"      ❌ Error en match: {series_error}")
                    continue
            
            self._log(f"   ✅ {matched} elementos vinculados con TMDB")
            
        except Exception as e:
            self.logger.error(f"Error en match_tmdb: {e}")
            raise
    
    def detect_missing(self):
        """Detectar episodios faltantes"""
        try:
            result = self.missing_episodes_service.rebuild_for_categories(
                self.db.get_episodic_categories()
            )
            for series_result in result.get("series_results", []):
                missing_count = int(series_result.get("missing_count", 0))
                if missing_count:
                    self._log(
                        f"      📺 {series_result.get('series_name', 'Serie')}: "
                        f"{missing_count} episodios faltantes"
                    )
                series_id = series_result.get("series_id")
                if series_id is not None:
                    self._series_updated(series_id)
            self._log(
                f"   ✅ Recalculados faltantes para {result.get('recalculated_series', 0)} series"
            )
            skipped = result.get("skipped_needs_review", 0)
            if skipped:
                self._log(
                    f"   ⏸️ {skipped} series con match de baja confianza omitidas "
                    f"(pendientes de revisión manual)"
                )
            return int(result.get("total_missing", 0))
            
        except Exception as e:
            self.logger.error(f"Error en detect_missing: {e}")
            raise
    
    def compare_episodes(self, tmdb_episodes, local_episodes):
        """Comparar episodios TMDB vs locales"""
        # Crear set de episodios locales
        local_set = set()
        for ep in local_episodes:
            local_set.add((ep['season'], ep['episode']))
        
        # Encontrar faltantes
        missing = []
        for ep in tmdb_episodes:
            key = (ep['season_number'], ep['episode_number'])
            if key not in local_set:
                missing.append({
                    'season': ep['season_number'],
                    'episode': ep['episode_number'],
                    'name': ep['name'],
                    'air_date': ep.get('air_date', '')
                })
        
        return missing

    def _normalize_hispashare_query(self, text: str) -> str:
        value = (text or "").strip()
        if not value:
            return ""

        value = re.sub(r'\s+', ' ', value)
        value = re.sub(r'\bTemporadas?\s*\d+(?:\s*-\s*\d+)?\b', '', value, flags=re.IGNORECASE)
        value = re.sub(r'\bTemporada\s*\d+\b', '', value, flags=re.IGNORECASE)
        value = re.sub(r'\bSeason\s*\d+(?:\s*-\s*\d+)?\b', '', value, flags=re.IGNORECASE)
        value = re.sub(r'\bComplete\b', '', value, flags=re.IGNORECASE)
        value = re.sub(r'\bPack\b', '', value, flags=re.IGNORECASE)
        value = re.sub(r'\b\(.*?Temporad.*?\)\b', '', value, flags=re.IGNORECASE)
        value = re.sub(r'\s+', ' ', value).strip()
        return value

    def _get_hispashare_search_query(self, series: dict) -> tuple[str, int | None]:
        tmdb_name = series.get('tmdb_name') or ''
        local_name = series.get('name') or ''
        year = series.get('year')

        preferred = tmdb_name.strip() if tmdb_name and tmdb_name.strip() else local_name.strip()
        query = self._normalize_hispashare_query(preferred)
        if not query:
            query = self._normalize_hispashare_query(local_name)

        return query, year
    
    def _search_hispashare_pass(
        self,
        driver,
        series_list,
        *,
        method_name,
        empty_message,
        count_label,
        found_label,
        save_reason,
        content_mode=None,
        label_prefix=None,
    ):
        """Recorre una lista de series/items pendientes de URL y busca en Hispashare.

        Comparte la lógica que antes estaba triplicada entre search_hispashare(),
        search_hispashare_for_new_series() y search_hispashare_for_single_file_items():
        mismo bucle, mismo manejo de errores, mismo guardado de resultado. Las tres
        variantes solo difieren en qué lista consultan y en los textos de log.
        `label_prefix(i, total, item)` permite personalizar el prefijo por elemento
        (usado para mostrar la categoría en bibliotecas de archivo único).
        """
        try:
            total = len(series_list)
            if total == 0:
                self._log(f"   ℹ️ {empty_message}")
                return 0

            self._log(f"   📊 {count_label}: {total}")
            searcher = HispashareSearcher(driver, self.db)
            found = 0

            for i, series in enumerate(series_list, 1):
                if not self.running:
                    break

                query, year = self._get_hispashare_search_query(series)
                label = series.get('tmdb_name') or series.get('name')
                prefix = label_prefix(i, total, series) if label_prefix else f"[{i}/{total}]"
                self._log(f"   🔎 {prefix} Buscando: {label} -> '{query}'...")

                try:
                    kwargs = {'content_mode': content_mode} if content_mode else {}
                    url = searcher.search_and_select(query, year, series['id'], **kwargs)

                    if url:
                        try:
                            saved = self._save_hispashare_source(
                                series['id'],
                                url,
                                source_title=label,
                                reason=save_reason,
                            )
                            if saved:
                                found += 1
                                self.logger.info(f"URL guardada para '{series['name']}': {url}")
                                self._log(f"      ✅ URL encontrada y guardada: {url[:50]}...")
                                self._emit(SOURCE_URL_SAVED, series_id=series['id'], url=url)
                            else:
                                # Repetir la búsqueda devolvería la misma ficha en cada
                                # pasada (hubo items con 37 intentos y una fila 'conflict'
                                # por intento): se desactiva hasta revisión manual.
                                # register_search_failure y no disable_series_search: deja
                                # search_failures >= 1, así la migración de arranque de
                                # db_manager (reactiva desactivadas con 0 fallos) no la
                                # vuelve a encender.
                                self.db.register_search_failure(series['id'], disable_after=1)
                                self.logger.warning(f"URL en conflicto (ya asignada a otra serie) para '{series['name']}': {url}")
                                self._log("      ⚠️ URL ya pertenece a otra serie; se omite y se desactiva la búsqueda automática (revisar a mano)")
                        except Exception as save_error:
                            self.logger.error(f"Error guardando URL para '{series['name']}': {save_error}")
                            self._log(f"      ⚠️ URL encontrada pero error al guardar: {str(save_error)}")
                    else:
                        self._log("      ❌ No se encontró (marcada para no buscar más)")

                except Exception as e:
                    self.logger.error(f"Error buscando '{series['name']}': {e}")
                    self._log(f"      ❌ Error en búsqueda: {str(e)}")
                    continue

                time.sleep(2)  # Esperar entre búsquedas

            self._log(f"   ✅ {found}/{total} {found_label}")
            return found

        except Exception as e:
            self.logger.error(f"Error en {method_name}: {e}")
            raise

    def search_hispashare(self, driver):
        """Buscar series en Hispashare - solo las que tienen episodios faltantes"""
        series_list = self.db.get_series_with_missing_episodes_without_url(self.db.get_episodic_categories())
        self._search_hispashare_pass(
            driver,
            series_list,
            method_name="search_hispashare",
            empty_message="No hay series con episodios faltantes que necesiten búsqueda en Hispashare",
            count_label="Series con episodios faltantes a buscar",
            found_label="URLs encontradas para series con episodios faltantes",
            save_reason='URL encontrada por scheduler para serie con episodios faltantes',
        )

    def search_hispashare_for_new_series(self, driver):
        """Buscar y guardar URL de Hispashare para series nuevas que no tienen episodios faltantes.
        Evita duplicar con las que ya se buscan en search_hispashare()."""
        all_without_url = self.db.get_series_without_hispashare_url(self.db.get_episodic_categories())
        missing_without_url = self.db.get_series_with_missing_episodes_without_url(self.db.get_episodic_categories())
        missing_ids = {s['id'] for s in missing_without_url}
        series_list = [s for s in all_without_url if s['id'] not in missing_ids]

        self._search_hispashare_pass(
            driver,
            series_list,
            method_name="search_hispashare_for_new_series",
            empty_message="No hay series nuevas sin URL que necesiten búsqueda en Hispashare",
            count_label="Series nuevas sin URL a buscar",
            found_label="URLs encontradas para series nuevas sin episodios faltantes",
            save_reason='URL encontrada por scheduler para serie nueva',
            label_prefix=lambda i, total, series: f"[NUEVAS {i}/{total}]",
        )

    def search_hispashare_for_single_file_items(self, driver):
        """Buscar y guardar URL para bibliotecas de archivo unico."""
        categories = Settings.get_single_file_categories()
        items = self.db.get_series_without_hispashare_url(categories)

        def _prefix(i, total, item):
            category_label = Settings.get_category_label(item.get('content_category', ''))
            return f"[{i}/{total}] [{category_label}]"

        self._search_hispashare_pass(
            driver,
            items,
            method_name="search_hispashare_for_single_file_items",
            empty_message="No hay elementos de archivo unico sin URL que buscar",
            count_label="Elementos de archivo único sin URL a buscar",
            found_label="URLs encontradas para archivo unico",
            save_reason='URL encontrada por scheduler para biblioteca de archivo unico',
            content_mode='single_file',
            label_prefix=_prefix,
        )

    def run_discovery(self):
        try:
            from core.discovery.service import DiscoveryService

            DiscoveryService(self.db, log=self._log).run()
        except Exception as e:
            # Las novedades nunca deben tumbar el ciclo de la biblioteca.
            self.logger.error(f"Error en novedades: {e}", exc_info=True)
            self._log(f"   ⚠️ Error revisando novedades: {e}")

    def process_wanted_movies(self):
        """Películas/documentales "en camino" (novedades aprobadas): buscar
        fuente en los proveedores que saben de películas y descargar la mejor
        según el perfil de calidad."""
        movie_providers = [p for p in self.providers.values() if getattr(p, "supports_movies", False)]
        if not movie_providers:
            return
        categories = Settings.get_single_file_categories()
        rows = self.db.get_connection().execute(
            f"""SELECT id, name, tmdb_name, year, tmdb_id, content_category, folder_path, hispashare_url
                FROM series WHERE COALESCE(placeholder, 0) = 1 AND COALESCE(monitored, 1) = 1
                AND content_category IN ({','.join('?' for _ in categories)})""",
            categories,
        ).fetchall()
        if not rows:
            return
        self._log(f"   🎬 {len(rows)} películas/documentales en camino")
        for row in rows:
            if not self.running:
                return
            item = dict(row)
            if self.grab_history_repository.recently_grabbed_item(item["id"], Settings.GRAB_RETRY_DAYS):
                continue  # ya enviada: esperando a que el cliente la descargue
            for provider in movie_providers:
                if self.source_offer_repository.get(item["id"], provider.name):
                    continue
                try:
                    offer = provider.find_candidate(item, content_mode="single_file")
                except ProviderUnavailable:
                    continue
                except Exception as e:
                    self.logger.warning(f"[{provider.name}] error buscando '{item['name']}': {e}")
                    continue
                if offer:
                    self.source_offer_repository.upsert(item["id"], offer)
            offer = self.source_arbitration_service.best_offer(item["id"])
            provider = self.providers.get(offer.provider) if offer else None
            if provider is None:
                continue
            try:
                handle = provider.resolve_download(item, offer, missing_only=True)
            except Exception as e:
                self._log(f"      ❌ {item['tmdb_name'] or item['name']}: {e}")
                continue
            if handle:
                destinations = handle.payload.get("grabbed") or handle.payload.get("ed2k_links") or []
                self.grab_history_repository.record(offer.provider, offer.source_url, item["id"], [],
                                                    release_title=offer.title or item["name"],
                                                    destination=", ".join(destinations)[:500])
                self._log(f"      ✅ {item['tmdb_name'] or item['name']} → {offer.provider} "
                          f"({TIERS.get(tier_of(offer.quality), '?')})")

    def discover_search_offers(self):
        """Pide oferta a los proveedores que buscan bajo demanda (los que no
        tienen un catálogo que rastrear) para las series con faltantes que
        aún no tienen una suya. Las que ya la tienen se revisan en
        refresh_all_offers al procesar descargas."""
        categories = self.db.get_episodic_categories()
        for provider in self.providers.values():
            if not getattr(provider, "discovers_by_search", False):
                continue
            pending = self.source_offer_repository.series_missing_without_offer(provider.name, categories)
            if not pending:
                continue
            self._log(f"   🔎 [{provider.name}] {len(pending)} series con faltantes sin ficha todavía")
            found = 0
            for series in pending:
                if not self.running:
                    return
                try:
                    offer = provider.find_candidate(series, content_mode="episodic")
                except ProviderUnavailable as e:
                    self._log(f"   ⏸️ [{provider.name}] {e}; se sigue en el próximo ciclo")
                    break
                except Exception as e:
                    self.logger.error(f"[{provider.name}] error buscando '{series['name']}': {e}")
                    continue
                if offer is None:
                    continue
                self.source_offer_repository.upsert(series["id"], offer)
                found += 1
                quality = f"{offer.quality.resolution_height}p" if offer.quality.resolution_height else "?"
                self._log(f"      ✅ {series.get('tmdb_name') or series['name']} → {offer.title} [{quality}]")
            self._log(f"   ✅ [{provider.name}] {found}/{len(pending)} series con ficha encontrada")

    def process_downloads(self, driver):
        """
        Descarga episodios faltantes usando la fuente de mayor calidad
        disponible entre todos los proveedores activos. El arbitraje
        (SourceArbitrationService) decide qué proveedor gana -- ni Hispashare
        ni Wolfmax están hardcodeados aquí, así que añadir un proveedor nuevo
        no requiere tocar este método.
        """
        try:
            series_with_offers = self.db.get_series_with_missing_episodes_and_any_offer(
                self.db.get_episodic_categories()
            )

            if not series_with_offers:
                self._log("   ℹ️ No hay series con episodios faltantes y fuentes conocidas para procesar descargas")
                return

            total_downloads = 0
            self._log(f"   📋 Procesando {len(series_with_offers)} series con episodios faltantes...")

            for series in series_with_offers:
                if not self.running:
                    break

                series_id = series['id']
                name = series['name']

                try:
                    # Revisita todas las fuentes conocidas para esta serie antes
                    # de decidir -- así Hispashare se sigue comprobando aunque
                    # Wolfmax sea el proveedor que gane, y viceversa.
                    self.source_arbitration_service.refresh_all_offers(series_id, series)
                    offer = self.source_arbitration_service.best_offer(series_id)
                    if offer is None:
                        self._log(f"   ℹ️ [{name}] Sin oferta disponible en ningún proveedor")
                        continue

                    provider = self.providers.get(offer.provider)
                    if provider is None:
                        self.logger.warning(f"Proveedor desconocido '{offer.provider}' para '{name}'")
                        continue

                    quality_label = f"{offer.quality.resolution_height}p" if offer.quality.resolution_height else "?"
                    self._log(f"   🔍 [{offer.provider} {quality_label}] {name}")

                    handle = provider.resolve_download(series, offer, missing_only=True)
                    if handle is None:
                        self._log("      ℹ️ Sin enlaces para episodios faltantes")
                        continue

                    if handle.kind == "ed2k":
                        count = len(handle.payload.get("ed2k_links", []))
                        total_downloads += count
                        self._log(f"      ✅ {count} enlaces ed2k")
                    elif handle.kind == "torrent":
                        grabbed = handle.payload.get("grabbed", [])
                        total_downloads += len(grabbed)
                        destination = str(Path(grabbed[0]).parent) if grabbed else ""
                        self._log(f"      ✅ {len(grabbed)} torrent(s) → {destination}")

                except Exception as e:
                    self.logger.error(f"Error procesando '{name}': {e}")
                    self._log(f"      ❌ Error: {e}")
                    continue

                time.sleep(1)  # Pequeña pausa entre series

            if total_downloads > 0:
                self._log(f"   ✅ Total: {total_downloads} descargas obtenidas para episodios faltantes")
            else:
                self._log("   ℹ️ No se obtuvieron descargas en este ciclo")

        except Exception as e:
            self.logger.error(f"Error en process_downloads: {e}")
            raise

    def _save_hispashare_source(self, series_id, url, *, source_title="", reason=""):
        cover_url = self.db.derive_hispashare_cover_url(url) or ""
        saved = self.source_repository.save_match(
            series_id,
            {
                "source_url": url,
                "source_title": source_title,
                "cover_url": cover_url,
                "match_score": 1.0,
                "match_status": "confirmed",
                "validation_reason": reason,
            },
        )
        if not saved:
            return False
        self.decision_log_repository.append(
            series_id,
            "source_selected_scheduler",
            "Se guardó una URL Hispashare desde el scheduler",
            {
                "source_url": url,
                "source_title": source_title,
                "reason": reason,
            },
        )
        return True

    def scan_wolfmax_categories(self, max_pages: int = 3):
        """
        Rasca las páginas de categoría de wolfmax4k.com, hace match con las
        series/películas de la DB por título y guarda las URLs wolfmax encontradas.
        También detecta contenido nuevo (no en la DB) y lo registra.
        """
        try:
            all_series = self.db.get_all_series()
            if not all_series:
                return

            # Índice normalizado de la DB para matching rápido
            db_index = [
                {
                    'id': s['id'],
                    'name': s.get('name', ''),
                    'tmdb_name': s.get('tmdb_name', ''),
                    'category': s.get('content_category', ''),
                    'year': s.get('year'),
                    'norm': _normalize(s.get('tmdb_name') or s.get('name', '')),
                }
                for s in all_series
            ]

            scanner = WolfmaxCategoryScanner(max_pages=max_pages)
            items = scanner.scan_categories()
            self._log(f"   📋 {len(items)} items encontrados en wolfmax4k.com")

            matched = 0
            new_content = 0
            threshold = getattr(Settings, 'WOLFMAX_MATCH_THRESHOLD', 0.55)

            for item in items:
                if not self.running:
                    break
                item_norm = _normalize(item['title'])
                best_series = None
                best_score = 0.0

                # Buscar solo en la misma categoría (no caer al índice global)
                cat_candidates = [
                    s for s in db_index
                    if s['category'] == item['app_category'] and s['norm']
                ]
                item_year = item.get('year')
                for s in cat_candidates:
                    # Año conocido y distinto → nunca es el mismo contenido
                    # (remakes/homónimos: "Dune" 1984 vs "Dune" 2021).
                    if item_year and s['year'] and s['year'] != item_year:
                        continue
                    score = _title_score(item_norm, s['norm'])
                    if score > best_score:
                        best_score = score
                        best_series = s

                if best_series and best_score >= threshold:
                    existing = self.db.get_wolfmax_source_for_series(best_series['id'])
                    # Preferir URL de mayor calidad: actualizar si la nueva es mejor
                    existing_q = int((existing or {}).get('match_score', 0) * 2160)
                    new_q = item.get('quality_height') or 0
                    # Las URLs del diseño antiguo de wolfmax ya no existen:
                    # sustituirlas aunque la nueva no sea de mayor calidad.
                    stale = existing and not is_current_wolfmax_url(existing.get('source_url') or '')
                    if not existing or stale or new_q > existing_q:
                        self.db.save_wolfmax_source(
                            best_series['id'],
                            item['wolfmax_url'],
                            item.get('quality_height'),
                            item['title'],
                        )
                        quality_label = f'{new_q}p' if new_q else '?'
                        self._log(
                            f"   🐺 [{best_series['category']}] {best_series['name']} "
                            f"→ wolfmax {quality_label} (score={best_score:.2f})"
                        )
                        matched += 1
                else:
                    # Contenido no encontrado en la DB → posible contenido nuevo
                    new_content += 1

            self._log(
                f"   ✅ Wolfmax sync: {matched} URLs guardadas | "
                f"{new_content} items sin match en tu biblioteca"
            )
        except Exception as e:
            self.logger.error(f"Error en scan_wolfmax_categories: {e}")
            self._log(f"   ⚠️ Error en wolfmax category scan: {e}")

    def scan_missing_resolutions(self, batch_size=300):
        """
        Detecta la resolución real (con pymediainfo) de los episodios que
        el parser de nombre no pudo clasificar. Procesa hasta `batch_size`
        archivos por ciclo para no bloquear el scheduler demasiado tiempo.
        """
        try:
            episodes = self.db.get_episodes_missing_resolution(limit=batch_size)
            if not episodes:
                self._log("   ✅ Todas las resoluciones ya detectadas")
                return

            self._log(
                f"   🔬 Detectando resolución de {len(episodes)} archivo(s) con MediaInfo..."
            )
            resolved = []
            failed_ids = []
            for ep in episodes:
                if not self.running:
                    break
                result = detect_resolution_from_file(ep['file_path'])
                if result:
                    resolved.append({'id': ep['id'], **result})
                else:
                    failed_ids.append(ep['id'])

            if resolved:
                self.db.batch_update_episode_resolutions(resolved)
            if failed_ids:
                self.db.mark_episode_resolution_unreadable(failed_ids)

            pending = self.db.get_episodes_missing_resolution(limit=1)
            self._log(
                f"   ✅ Resueltos: {len(resolved)} | No leíbles: {len(failed_ids)} | "
                f"Pendientes: {'0' if not pending else 'más de 300'}"
            )
        except Exception as e:
            self.logger.error(f"Error en scan_missing_resolutions: {e}")
            self._log(f"   ⚠️ Error detectando resoluciones: {e}")

    def scan_item_qualities(self, batch_size=600, workers=4):
        """Mide la calidad real (MediaInfo) de películas, documentales y
        conciertos que aún no la tienen. Leer cada archivo por la red tarda
        ~2 s, así que se leen varios a la vez y en tandas por ciclo."""
        from concurrent.futures import ThreadPoolExecutor

        pending = self.item_quality_repository.pending(Settings.get_single_file_categories(), limit=batch_size)
        if not pending:
            return
        self._log(f"   🔬 Midiendo calidad de {len(pending)} películas/documentales...")
        measured = unreadable = 0
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [(item, pool.submit(detect_item_quality, item["folder_path"])) for item in pending]
            for item, future in futures:
                if not self.running:
                    pool.shutdown(cancel_futures=True)
                    return
                try:
                    file_path, quality, source = future.result()
                except Exception as e:
                    self.logger.warning(f"No se pudo medir '{item['name']}': {e}")
                    continue
                # Las escrituras van en este hilo: la conexión SQLite es por hilo.
                self.item_quality_repository.save(item["id"], item["folder_path"], file_path=file_path,
                                                  quality=quality, source=source)
                if quality:
                    measured += 1
                else:
                    unreadable += 1
        self._log(f"   ✅ Calidad medida: {measured} | sin poder leer: {unreadable}")

    def process_upgrades(self, driver):
        """
        Mejora la calidad de episodios ya descargados usando la mejor oferta
        conocida entre todos los proveedores activos (arbitraje genérico,
        vía SourceArbitrationService.should_upgrade). Reemplaza las dos
        comparaciones que antes estaban escritas a mano y por separado para
        Hispashare y Wolfmax.
        """
        try:
            candidates = []
            # Series: se compara con el PEOR episodio en disco, para que una
            # serie con un episodio en 4K y el resto en 1080p también mejore.
            # Si aún no se conoce la resolución real (pendiente de
            # scan_missing_resolutions) no se evalúa: no hay con qué comparar.
            for series in self.db.get_series_with_local_resolution_and_any_offer(self.db.get_episodic_categories()):
                if series.get('min_local_resolution') is None:
                    continue
                local = quality_from_local_episode({'resolution_height': series['min_local_resolution'], 'codec': None})
                candidates.append((series, local))
            # Películas, documentales, conciertos: la calidad medida del archivo.
            for item in self.item_quality_repository.upgrade_candidates(Settings.get_single_file_categories()):
                local = quality_from_local_episode(item)
                candidates.append((item, local))

            if not candidates:
                self._log("   ℹ️ No hay elementos con calidad conocida y fuente para evaluar mejoras")
                return

            max_series = getattr(Settings, 'UPGRADE_MAX_SERIES_PER_CYCLE', 15)
            # Presupuestos por proveedor: preservan los límites previos
            # (enlaces ed2k de Hispashare vs. descargas directas de Wolfmax
            # son recursos de coste muy distinto). Un proveedor nuevo sin
            # límite explícito configurado usa un tope conservador por defecto.
            provider_budgets = {
                'hispashare': getattr(Settings, 'UPGRADE_MAX_LINKS_PER_CYCLE', 80),
                'wolfmax': getattr(Settings, 'WOLFMAX_MAX_DOWNLOADS_PER_CYCLE', 2),
            }
            provider_used = {}
            processed = 0
            total_upgrades = 0

            self._log(f"   📊 {len(candidates)} candidatos a evaluar para upgrade")

            for series, local_quality in candidates:
                if not self.running or processed >= max_series:
                    break

                series_id = series['id']
                name = series['name']

                try:
                    self.source_arbitration_service.refresh_all_offers(series_id, series)
                    upgrade_available, offer = self.source_arbitration_service.should_upgrade(
                        series_id, local_quality
                    )
                    if not upgrade_available or offer is None:
                        continue

                    budget = provider_budgets.get(offer.provider, 5)
                    if provider_used.get(offer.provider, 0) >= budget:
                        continue

                    provider = self.providers.get(offer.provider)
                    if provider is None:
                        continue

                    processed += 1
                    self._log(
                        f"   🔼 [{offer.provider}] Mejora: {name} "
                        f"({TIERS.get(tier_of(local_quality), '?')} → {TIERS.get(tier_of(offer.quality), '?')})"
                    )

                    handle = provider.resolve_download(series, offer, missing_only=False)
                    if handle is None:
                        self._log("      ℹ️ Sin upgrades disponibles")
                        continue

                    if handle.kind == "ed2k":
                        count = len(handle.payload.get("ed2k_links", []))
                    else:
                        count = len(handle.payload.get("grabbed", []))
                    provider_used[offer.provider] = provider_used.get(offer.provider, 0) + count
                    total_upgrades += count
                    self._log(f"      ✅ {count} obtenido(s)")

                except Exception as e:
                    self.logger.error(f"Error en upgrades para '{name}': {e}")
                    self._log(f"      ❌ Error: {str(e)}")
                    continue

                time.sleep(1)

            if total_upgrades:
                self._log(f"   ✅ Upgrades: {total_upgrades} obtenido(s)")
            else:
                self._log("   ℹ️ No se realizaron upgrades en este ciclo")

        except Exception as e:
            self.logger.error(f"Error en process_upgrades: {e}")
            raise
    
    def scan_new_content(self, driver):
        """Escanear nuevo contenido en Hispashare"""
        try:
            if not self.running:
                return
            # Verificar si el barrido de contenido está habilitado
            if not getattr(Settings, 'CONTENT_SCAN_ENABLED', True):
                self._log("   ℹ️ Barrido de nuevo contenido deshabilitado en configuración")
                return
            
            scanner = ContentScanner(driver, self.db, should_stop=lambda: not self.running)
            
            # 1. Escanear páginas de Hispashare
            self._log("   📄 Escaneando páginas de Hispashare...")
            new_content = scanner.scan_new_content()
            if not self.running:
                return
            
            if not new_content:
                self._log("   ℹ️ No se encontró nuevo contenido que cumpla los filtros")
                return
            
            self._log(f"   ✅ {len(new_content)} elementos de nuevo contenido encontrados")
            grouped_counts = {}
            for item in new_content:
                category = item.get('library_category', 'sin_categoria')
                grouped_counts[category] = grouped_counts.get(category, 0) + 1
            for category, count in sorted(grouped_counts.items()):
                self._log(f"      📚 {Settings.get_category_label(category)}: {count}")
            
            # 2. Procesar contenido encontrado
            self._log("   🎬 Procesando nuevo contenido...")
            ed2k_links = scanner.process_new_content(new_content)
            if not self.running:
                return
            
            if ed2k_links:
                self._log(f"   ✅ {len(ed2k_links)} enlaces ed2k obtenidos del nuevo contenido")
            else:
                self._log("   ℹ️ No se obtuvieron enlaces del nuevo contenido")
            
        except Exception as e:
            self.logger.error(f"Error en scan_new_content: {e}")
            self._log(f"   ❌ Error en barrido de contenido: {str(e)}")
            # No relanzar la excepción para no interrumpir el ciclo completo
            pass

    def stop(self):
        """Detener el scheduler"""
        self.logger.info("Deteniendo scheduler...")
        super().stop()
        if self.current_driver:
            try:
                self.current_driver.quit()
                self.logger.info("Driver cerrado al detener scheduler")
            except Exception as e:
                self.logger.warning(f"No se pudo cerrar el driver al detener scheduler: {e}")
            finally:
                self.current_driver = None
        self.logger.info("Scheduler marcado para detenerse")
