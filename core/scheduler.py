import time
import logging
from datetime import datetime, timedelta
from pathlib import Path
import re

from core.auth.hispashare_login import HispashareAuth
from core.library.sync import LibrarySync
from core.hispashare.searcher import HispashareSearcher
from core.hispashare.content_scanner import ContentScanner
from core.wolfmax.category_scanner import WolfmaxCategoryScanner, WOLFMAX_CATEGORY_URLS, _normalize, _title_score
from core.wolfmax.downloader import is_current_url as is_current_wolfmax_url
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
from core.repositories.provider_search_log import ProviderSearchLog
from core.jobs import library_lock, task_runs
from core.jobs.cycle_lock import CycleLock
from core.library.quality_scan import MediaInfoUnavailable, measure_episodes, measure_items
from config.settings import Settings

class Scheduler(BackgroundJob):
    """Ciclo periódico de escaneo, matching, búsqueda y descargas.

    Además de los eventos de BackgroundJob publica SOURCE_URL_SAVED y
    TMDB_MATCHED. Al compartir bus con los servicios, también llegan los
    SERIES_UPDATED de ReconciliationService (sin "source").
    """

    name = "scheduler"

    def __init__(self, event_bus=None, *, wait_for_interval=False):
        super().__init__(event_bus)
        # Arranque automático: si el último ciclo completo fue hace menos de
        # CHECK_INTERVAL_HOURS, se espera lo que falte en vez de repetirlo.
        self.wait_for_interval = wait_for_interval
        self.logger = logging.getLogger(__name__)
        self.db = DatabaseManager()
        self.series_repository = SeriesRepository(self.db)
        self.source_repository = SourceRepository(self.db)
        self.source_offer_repository = SourceOfferRepository(self.db)
        self.decision_log_repository = DecisionLogRepository(self.db)
        self.item_quality_repository = ItemQualityRepository(self.db)
        self.grab_history_repository = GrabHistoryRepository(self.db)
        self.provider_search_log = ProviderSearchLog(self.db)
        self.cycle_lock = CycleLock(self.db)
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
        if self.wait_for_interval:
            self._wait_until_due()
        while self.running:
            if not self.cycle_lock.acquire():
                # Otro proceso (otro servidor sobre la misma BD) ya está en un
                # ciclo sobre la misma base de datos: se espera a que acabe.
                self._log(f"⏸️ Ya hay un ciclo en marcha en otro proceso ({self.cycle_lock.holder()}); "
                          f"se vuelve a intentar en 10 minutos")
                for _ in range(600):
                    if not self.running:
                        break
                    time.sleep(1)
                continue
            try:
                try:
                    library_lock.run_exclusive(self, self.execute_cycle, "Ciclo automático")
                finally:
                    self.cycle_lock.release()
                
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

    def _wait_until_due(self):
        last = task_runs.last_runs(self.db).get(self.name)
        if last is None:
            return
        due = last + timedelta(hours=Settings.CHECK_INTERVAL_HOURS)
        if due <= datetime.now():
            return
        self._log(f"⏰ Último ciclo completo: {last:%d/%m %H:%M}. Próximo ciclo: {due:%d/%m %H:%M}")
        self._progress(0, f"Próximo ciclo: {due:%d/%m %H:%M}")
        while self.running and datetime.now() < due:
            time.sleep(1)

    def execute_cycle(self):
        """Ejecutar un ciclo completo"""
        cycle_started = datetime.now()
        self._log("=" * 60)
        self._log(f"🚀 INICIO CICLO - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        self._log("=" * 60)
        
        driver = None
        # Cada paso va aislado (_step): si uno falla (NAS sin acceso, TMDB
        # caído...) se avisa y el ciclo sigue con los demás en vez de
        # abortar entero y no descargar nada.
        self._cycle_warnings = []
        try:
            # 1. Login Hispashare. Si falla, el ciclo sigue sin Hispashare
            # (Wolfmax, DonTorrent y la biblioteca no lo necesitan).
            self._progress(5, "🔐 Login Hispashare...")
            self._log("🔐 Iniciando sesión en Hispashare...")
            driver = self._step("Login en Hispashare", self.login_hispashare)
            if driver is None:
                self._log("   ⏭️ Se sigue sin Hispashare en este ciclo")
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
            cleaned_count = self._step("Limpieza de series eliminadas", self.db.cleanup_deleted_series)
            if cleaned_count is not None:
                self._log(f"   ✅ {cleaned_count} series eliminadas")
            if not self.running:
                return

            # 2.1. Limpiar episodios faltantes huérfanos
            self._log("🧹 Limpiando episodios faltantes huérfanos...")
            orphan_count = self._step("Limpieza de faltantes huérfanos", self.db.cleanup_orphaned_missing_episodes)
            if orphan_count is not None:
                self._log(f"   ✅ {orphan_count} episodios huérfanos eliminados")

            # 2.2. Importar lo descargado (si está activado): justo antes de
            # escanear, para que lo importado se vea en este mismo ciclo.
            self.run_import()

            # 3. Escanear biblioteca
            self._progress(25, "📁 Escaneando biblioteca...")
            self._log("📁 Escaneando biblioteca local...")
            series_list = self._step("Escaneo de series", self.scan_library)
            if series_list is not None:
                self._log(f"   ✅ {len(series_list)} series encontradas")
            if not self.running:
                return

            self._log("🎞️ Escaneando bibliotecas de archivo unico...")
            single_file_items = self._step("Escaneo de bibliotecas de archivo único", self.scan_single_file_library)
            if single_file_items is not None:
                self._log(f"   ✅ {len(single_file_items)} elementos de archivo unico encontrados")
            if not self.running:
                return

            # 4. Limpiar duplicados de series tras la sincronización de escaneos
            self._progress(35, "🔧 Consolidando duplicados...")
            self._log("🔧 Limpiando series duplicadas...")
            cleaned_series = self._step("Limpieza de duplicados", self.db.cleanup_duplicate_series)
            if cleaned_series is not None:
                self._log(f"   ✅ {cleaned_series} series duplicadas eliminadas")

            # 5. Vincular con TMDB
            self._progress(45, "💾 Sincronización completada...")
            self._log("💾 Base de datos sincronizada durante el escaneo")
            self._progress(55, "🎬 Vinculando con TMDB...")
            self._log("🎬 Vinculando bibliotecas con TMDB...")
            self._step("Vinculación con TMDB", lambda: self.match_tmdb(self.db.get_all_series()))
            if not self.running:
                return

            # 5.1. Novedades de TMDB/IMDb que cumplen las notas mínimas
            self._progress(60, "🆕 Revisando novedades...")
            self._log("🆕 Revisando novedades de TMDB/IMDb...")
            self.run_discovery()

            # 5.2. Calendario de emisiones (qué sale y cuándo)
            self._log("📅 Actualizando calendario de emisiones...")
            self.update_agenda()
            
            # 6. Detectar episodios faltantes
            self._progress(65, "🔍 Detectando episodios faltantes...")
            self._log("🔍 Detectando episodios faltantes...")
            missing_count = self._step("Detección de faltantes", self.detect_missing)
            if missing_count is not None:
                self._log(f"   ✅ {missing_count} episodios faltantes detectados")

            # 6.0. Lo que no se encontró hace tiempo vuelve a buscarse (por si
            # lo han subido); lo que el usuario apagó, no.
            self.retry_disabled_searches()

            if driver is not None:
                # 6.1. Buscar URLs para series nuevas sin episodios faltantes
                self._progress(70, "🔎 Buscando URLs para series nuevas...")
                self._log("🔎 Buscando URLs en Hispashare para series nuevas sin episodios faltantes...")
                self._step("Búsqueda en Hispashare (series nuevas)", self.search_hispashare_for_new_series, driver)

                self._progress(73, "🔎 Buscando URLs de bibliotecas...")
                self._log("🔎 Buscando URLs en Hispashare para bibliotecas de archivo unico...")
                self._step("Búsqueda en Hispashare (archivo único)",
                           self.search_hispashare_for_single_file_items, driver)

                # 8. Buscar en Hispashare
                self._progress(75, "🔎 Buscando en Hispashare...")
                self._log("🔎 Buscando URLs en Hispashare...")
                self._step("Búsqueda en Hispashare", self.search_hispashare, driver)

            # 8.1. Proveedores sin catálogo propio (DonTorrent): buscar ficha
            # para las series con faltantes que aún no tienen oferta suya.
            self._progress(80, "🔎 Buscando en otras fuentes...")
            self._step("Búsqueda en otras fuentes", self.discover_search_offers)
            # DonTorrent: películas y series por debajo de 4K (mejoras), por turnos.
            self._step("Búsqueda por turnos en DonTorrent",
                       lambda: self.rotate_provider_searches("dontorrent", limit=20))
            if not self.running:
                return

            # 8.2. Wolfmax4k: antes de descargar, para que las fichas que
            # encuentre se usen ya en las descargas y mejoras de este ciclo
            # (antes iba al final y esperaban al ciclo siguiente).
            if getattr(Settings, 'WOLFMAX_UPGRADE_ENABLED', False):
                self._progress(82, "🐺 Sincronizando catálogo Wolfmax4k...")
                self._log("🐺 Escaneando categorías Wolfmax4k...")
                self.scan_wolfmax_categories()
                self._log("🐺 Buscando en Wolfmax4k lo que no sale en su catálogo reciente...")
                self._step("Búsqueda en Wolfmax4k", self.relink_wolfmax)
                if not self.running:
                    return

            # 9. Descargar episodios faltantes
            self._progress(85, "⬇️ Procesando descargas...")
            self._log("⬇️ Procesando descargas de episodios faltantes...")
            self._step("Descargas de episodios faltantes", self.process_downloads, driver)
            if not self.running:
                return
            self._step("Descargas de películas en camino", self.process_wanted_movies)
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
                self._step("Mejoras de calidad", self.process_upgrades, driver)

            # 10. Barrido de nuevo contenido en Hispashare
            if driver is not None:
                self._progress(93, "🔍 Buscando nuevo contenido...")
                self._log("🔍 Iniciando barrido de nuevo contenido en Hispashare...")
                self.scan_new_content(driver)

            warnings = self._cycle_warnings
            if warnings:
                self._progress(100, f"⚠️ Ciclo completado con {len(warnings)} aviso(s)")
                self._log(f"⚠️ CICLO COMPLETADO CON {len(warnings)} AVISO(S):")
                for warning in warnings:
                    self._log(f"   • {warning}")
            else:
                self._progress(100, "✅ Ciclo completado")
                self._log("✅ CICLO COMPLETADO CON ÉXITO")
            self._log("=" * 60)
            self._record_task(self.name, cycle_started)

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
    
    def _step(self, label, fn, *args):
        """Ejecuta un paso del ciclo; si falla, lo apunta como aviso y
        devuelve None para que el ciclo siga con el resto."""
        try:
            return fn(*args)
        except Exception as e:
            self.logger.error(f"Error en {label}: {e}", exc_info=True)
            self._log(f"   ⚠️ {label}: {e}")
            self._cycle_warnings.append(f"{label}: {e}")
            return None

    def login_hispashare(self):
        """Iniciar sesión en Hispashare"""
        if not Settings.HISPASHARE_USER:
            self._log("   ℹ️ Hispashare sin configurar")
            return None
        auth = None
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
            # El ciclo sigue sin Hispashare: que no quede un navegador abierto.
            driver = getattr(auth, "driver", None)
            if driver is not None:
                try:
                    driver.quit()
                except Exception:
                    pass
            raise
    
    def _library_sync(self):
        return LibrarySync(
            self.db,
            self.series_repository,
            self.reconciliation_service,
            self.decision_log_repository,
            log=self._log,
            should_continue=lambda: self.running,
            on_matched=lambda series_id, match: self._emit(TMDB_MATCHED, series_id=series_id, match=match),
        )

    def scan_library(self):
        """Escanear bibliotecas episódicas."""
        return self._library_sync().scan_episodic()

    def scan_single_file_library(self):
        """Escanear bibliotecas de contenido de archivo unico."""
        return self._library_sync().scan_single_file()

    def match_tmdb(self, series_list):
        """Vincular con TMDB lo que aún no tiene ficha."""
        return self._library_sync().match_unmatched(series_list)

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
            failed = result.get("failed") or []
            if failed:
                names = ", ".join(f["series_name"] for f in failed[:5]) + ("…" if len(failed) > 5 else "")
                self._log(f"   ⚠️ {len(failed)} series no se pudieron recalcular (se mantienen sus "
                          f"faltantes anteriores): {names}. Primer error: {failed[0]['error']}")
                if hasattr(self, "_cycle_warnings"):
                    self._cycle_warnings.append(f"Detección de faltantes: {len(failed)} series con error")
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

    def retry_disabled_searches(self):
        try:
            retried = self.db.retry_expired_searches()
        except Exception as e:
            self.logger.error(f"Error reactivando búsquedas antiguas: {e}", exc_info=True)
            return
        if retried:
            names = ", ".join(r["name"] for r in retried[:5]) + ("…" if len(retried) > 5 else "")
            self._log(f"🔁 Se vuelve a buscar en Hispashare lo que no se encontró hace más de "
                      f"{self.db.SEARCH_RETRY_DAYS} días ({len(retried)}): {names}")

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

    def run_import(self):
        try:
            from core.importer.service import ImportService

            service = ImportService(self.db, log=self._log)
            if not service.settings.enabled:
                return
            self._log("📥 Importando descargas terminadas a la biblioteca...")
            self._record_task("importer")
            result = service.run()
            self._log(f"   ✅ Importación: {result['imported']} nuevos, {result['upgraded']} mejoras, "
                      f"{result['skipped']} omitidos")
        except Exception as e:
            self.logger.error(f"Error importando descargas: {e}", exc_info=True)
            self._log(f"   ⚠️ Error importando descargas: {e}")

    def _record_task(self, name, when=None):
        """Las tareas programadas de la web se reprograman desde aquí: no
        repiten lo que el ciclo acaba de hacer."""
        try:
            task_runs.record_run(self.db, name, when or datetime.now())
        except Exception as e:
            self.logger.warning(f"No se pudo guardar la última ejecución de {name}: {e}")

    def _skip_if_recent(self, name, label):
        try:
            recent = task_runs.ran_recently(self.db, name)
        except Exception:
            recent = False
        if recent:
            self._log(f"   ⏭️ {label}: ya se hizo hace poco")
        return recent

    def update_agenda(self):
        if self._skip_if_recent("agenda", "Calendario"):
            return
        try:
            from core.agenda.service import AgendaService

            self._record_task("agenda")
            result = AgendaService(self.db).rebuild()
            self._log(f"   ✅ Calendario: {result['episodes']} episodios de {result['series']} series en emisión")
        except Exception as e:
            self.logger.error(f"Error actualizando el calendario: {e}", exc_info=True)
            self._log(f"   ⚠️ Error actualizando el calendario: {e}")

    def run_discovery(self):
        if self._skip_if_recent("discovery", "Novedades"):
            return
        try:
            from core.discovery.service import DiscoveryService

            self._record_task("discovery")
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
            if self.grab_history_repository.movie_blocked(item, Settings.GRAB_RETRY_DAYS):
                continue  # ya enviada o esperando en descargas
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

    def relink_wolfmax(self, limit: int = 15, retry_days: int = 14):
        self.rotate_provider_searches("wolfmax", limit=limit, retry_days=retry_days)

    def rotate_provider_searches(self, provider_name: str, *, limit: int, retry_days: int = 14):
        """Busca en un proveedor (con su buscador) por turnos y en este orden:
          1. fichas cuyo enlace guardado ya no funciona (Wolfmax: diseño
             antiguo de la web, que el catálogo reciente no renueva);
          2. series con episodios faltantes sin enlace de ese proveedor;
          3. lo que está por debajo de 4K (calidad medida) sin enlace de ese
             proveedor, para poder mejorarlo.
        Lo no encontrado no se vuelve a buscar hasta pasados `retry_days`."""
        provider = self.providers.get(provider_name)
        if provider is None:
            return
        conn = self.db.get_connection()
        recent = self.provider_search_log.searched_recently(provider_name, retry_days)
        columns = "s.id, s.name, s.tmdb_name, s.year, s.content_category"
        stale = []
        if provider_name == "wolfmax":
            stale = [
                dict(r) for r in conn.execute(
                    f"SELECT {columns}, so.source_url FROM source_offers so JOIN series s ON s.id = so.series_id "
                    "WHERE so.provider = 'wolfmax' AND COALESCE(s.monitored, 1) = 1"
                ).fetchall()
                if not is_current_wolfmax_url(r["source_url"] or "")
            ]
        no_link = "NOT EXISTS (SELECT 1 FROM source_offers so WHERE so.series_id = s.id AND so.provider = ?)"
        missing = [dict(r) for r in conn.execute(
            f"SELECT DISTINCT {columns} FROM series s JOIN missing_episodes me ON me.series_id = s.id "
            f"AND me.status = 'missing' WHERE COALESCE(s.monitored, 1) = 1 AND {no_link}",
            (provider_name,),
        ).fetchall()]
        below_4k = [dict(r) for r in conn.execute(
            f"""SELECT {columns} FROM series s
                LEFT JOIN item_quality iq ON iq.series_id = s.id AND iq.source = 'mediainfo' AND iq.unreadable = 0
                LEFT JOIN (SELECT series_id, MIN(resolution_height) AS worst FROM local_episodes
                           WHERE quality_source = 'mediainfo' GROUP BY series_id) le ON le.series_id = s.id
                WHERE COALESCE(s.monitored, 1) = 1 AND COALESCE(s.placeholder, 0) = 0 AND {no_link}
                  AND COALESCE(iq.resolution_height, le.worst) < 2000""",
            (provider_name,),
        ).fetchall()]

        queue, seen = [], set()
        for item in stale + missing + below_4k:
            if item["id"] not in seen and item["id"] not in recent and (item.get("tmdb_name") or item.get("name")):
                seen.add(item["id"])
                queue.append(item)
        if not queue:
            return
        stale_note = f" ({len(stale)} con enlace caducado)" if stale else ""
        self._log(f"   🔎 [{provider_name}] {len(queue)} fichas por buscar{stale_note}; "
                  f"este ciclo: {min(limit, len(queue))}")
        found = 0
        for item in queue[:limit]:
            if not self.running:
                return
            mode = Settings.get_category_content_mode(item["content_category"])
            try:
                offer = provider.find_candidate(item, content_mode=mode)
            except ProviderUnavailable as e:
                self._log(f"   ⏸️ [{provider_name}] {e}; se sigue en el próximo ciclo")
                break
            except Exception as e:
                self.logger.warning(f"{provider_name}: error buscando '{item['name']}': {e}")
                continue
            self.provider_search_log.record(item["id"], provider_name, offer is not None)
            if offer is None:
                continue
            if provider_name == "wolfmax":
                self.db.save_wolfmax_source(item["id"], offer.source_url, offer.quality.resolution_height, offer.title)
            else:
                self.source_offer_repository.upsert(item["id"], offer)
            found += 1
            self._log(f"      ✅ {item.get('tmdb_name') or item['name']} → {offer.source_url} "
                      f"[{offer.quality.resolution_height or '?'}p]")
        self._log(f"   ✅ [{provider_name}] {found}/{min(limit, len(queue))} encontradas por búsqueda")

    def scan_missing_resolutions(self, batch_size=1200):
        """Analiza con MediaInfo una tanda de episodios cuya calidad aún no se
        ha leído del propio archivo (lo que diga el nombre no cuenta)."""
        self._scan_quality("episodios", measure_episodes, batch_size)

    def scan_item_qualities(self, batch_size=600):
        """Igual para películas, documentales y conciertos."""
        self._scan_quality("películas/documentales", measure_items, batch_size)

    def _scan_quality(self, label, measure, batch_size):
        try:
            result = measure(self.db, limit=batch_size, should_stop=lambda: not self.running)
        except MediaInfoUnavailable as e:
            self._log(f"   ⚠️ {e}")
            return
        except Exception as e:
            self.logger.error(f"Error analizando calidad de {label}: {e}")
            self._log(f"   ⚠️ Error analizando calidad de {label}: {e}")
            return
        if result["measured"] or result["unreadable"]:
            self._log(
                f"   🔬 Calidad de {label}: {result['measured']} analizados | {result['unreadable']} sin poder leer"
                + (" | quedan más para el próximo ciclo" if result["pending"] else "")
            )

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
            profile = self.source_arbitration_service.profile
            for series in self.db.get_series_with_local_resolution_and_any_offer(self.db.get_episodic_categories()):
                # Solo cuenta la calidad leída del archivo con MediaInfo.
                known = [
                    quality_from_local_episode(ep) for ep in self.db.get_local_episodes(series['id'])
                    if ep.get('quality_source') == 'mediainfo' and ep.get('resolution_height')
                ]
                if not known:
                    continue
                # El peor episodio según el perfil (resolución, códec y HDR reales).
                candidates.append((series, min(known, key=profile.rank)))
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

    def _log(self, message: str):
        super()._log(message)
        self._beat()

    def _progress(self, percent: int, message: str):
        super()._progress(percent, message)
        self._beat()

    def _beat(self):
        """Señal de vida del bloqueo de ciclo (cada log/progreso del ciclo)."""
        lock = getattr(self, "cycle_lock", None)
        if lock is None:
            return
        try:
            lock.heartbeat()
        except Exception:
            pass  # nunca tumbar el ciclo por la señal de vida

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
