from pathlib import Path

from PyQt6.QtCore import QObject, QRunnable, pyqtSignal

from config.settings import Settings
from core.database.db_manager import DatabaseManager
from core.events import EventBus
from core.library.scanner import LibraryScanner
from core.library.category_scanner import CategoryLibraryScanner
from core.repositories import (
    DecisionLogRepository,
    SeriesRepository,
    SourceOfferRepository,
    SourceRepository,
)
from core.services import (
    MissingEpisodesService,
    ReconciliationService,
    SourceMatchService,
)
from core.tmdb.api_client import TMDBClient
from core.tmdb.auto_matcher import TMDBAutoMatcher


def _build_domain_services(db):
    series_repository = SeriesRepository(db)
    source_repository = SourceRepository(db)
    source_offer_repository = SourceOfferRepository(db)
    decision_log_repository = DecisionLogRepository(db)
    missing_episodes_service = MissingEpisodesService(
        db,
        series_repository,
        decision_log_repository,
    )
    source_match_service = SourceMatchService(
        db,
        source_repository,
        decision_log_repository,
    )
    reconciliation_service = ReconciliationService(
        series_repository,
        source_repository,
        missing_episodes_service,
        source_match_service,
        decision_log_repository,
        event_bus=EventBus(),
        source_offer_repository=source_offer_repository,
    )
    return {
        "series_repository": series_repository,
        "source_repository": source_repository,
        "source_offer_repository": source_offer_repository,
        "decision_log_repository": decision_log_repository,
        "missing_episodes_service": missing_episodes_service,
        "source_match_service": source_match_service,
        "reconciliation_service": reconciliation_service,
    }


class BackgroundTaskSignals(QObject):
    log_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(dict)
    error_signal = pyqtSignal(str)
    done_signal = pyqtSignal()


class BackgroundTask(QRunnable):
    def __init__(self):
        super().__init__()
        self.signals = BackgroundTaskSignals()
        self._cancelled = False
        self.setAutoDelete(True)

    def stop(self):
        self._cancelled = True

    def is_cancelled(self):
        return self._cancelled

    def execute(self):
        raise NotImplementedError()

    def run(self):
        try:
            if self.is_cancelled():
                return
            result = self.execute()
            if not self.is_cancelled() and result is not None:
                self.signals.finished_signal.emit(result)
        except Exception as e:
            if not self.is_cancelled():
                self.signals.error_signal.emit(str(e))
        finally:
            self.signals.done_signal.emit()


class ManualScanTask(BackgroundTask):
    def execute(self):
        db = DatabaseManager()
        try:
            episodic_paths = (
                Settings.get_series_paths()
                + Settings.get_cartoons_paths()
                + Settings.get_docuseries_paths()
            )
            if not episodic_paths:
                raise RuntimeError(
                    "Por favor configura alguna ruta episódica: series, dibujos o documentales series"
                )

            if self.is_cancelled():
                return None

            self.signals.log_signal.emit("🔍 Iniciando escaneo manual en segundo plano...")
            series_list = LibraryScanner.scan_all_episodic_categories(db_manager=db)

            if self.is_cancelled():
                return None

            processed = 0
            for series in series_list:
                if self.is_cancelled():
                    return None
                db.insert_or_update_series(series)
                processed += 1

            matched = 0
            if not self.is_cancelled() and Settings.TMDB_API_KEY:
                services = _build_domain_services(db)
                series_repository = services["series_repository"]
                reconciliation_service = services["reconciliation_service"]
                tmdb_client = TMDBClient(Settings.TMDB_API_KEY)
                matcher = TMDBAutoMatcher(tmdb_client)
                unmatched_series = [
                    item for item in db.get_all_series(db.get_episodic_categories())
                    if not item.get("tmdb_id")
                ]

                for series in unmatched_series:
                    if self.is_cancelled():
                        return None
                    match_result = matcher.match_content(series)
                    if not match_result:
                        continue
                    update_result = series_repository.update_tmdb_identity(series["id"], match_result)
                    reconciliation_service.on_tmdb_changed(
                        series["id"],
                        update_result.get("previous_item") or {},
                        update_result.get("updated_item") or {},
                        rebuild_missing=False,
                    )
                    matched += 1

            # Recalcular episodios faltantes para todas las series episódicas con TMDB
            total_missing = 0
            if not self.is_cancelled() and Settings.TMDB_API_KEY:
                self.signals.log_signal.emit("🔄 Recalculando episodios faltantes...")
                services = _build_domain_services(db)
                missing_result = services["missing_episodes_service"].rebuild_for_categories(
                    db.get_episodic_categories()
                )
                total_missing = missing_result.get("total_missing", 0)

            return {
                "processed": processed,
                "matched": matched,
                "total_missing": total_missing,
            }
        finally:
            db.close()


class LibraryRefreshTask(BackgroundTask):
    def __init__(self, categories, run_identification=False):
        super().__init__()
        self.categories = list(categories or [])
        self.run_identification = run_identification

    def execute(self):
        db = DatabaseManager()
        try:
            categories = self.categories or Settings.get_single_file_categories()
            local_items = []

            for category in categories:
                if self.is_cancelled():
                    return None
                scanner = CategoryLibraryScanner(
                    category,
                    Settings.get_paths_for_category(category),
                    skip_paths=db.get_unmonitored_folder_paths(),
                )
                items = scanner.scan()
                for item in items:
                    if self.is_cancelled():
                        return None
                    item["category"] = category
                    item_path = item.get("path", "")
                    if item_path:
                        db.insert_or_update_series({
                            "name": item.get("name", ""),
                            "folder_path": item_path,
                            "content_category": category,
                        })
                    local_items.append(item)

            local_map = {}
            for item in local_items:
                item_path = item.get("path", "")
                if not item_path:
                    continue
                normalized = db.normalize_path(item_path).lower()
                local_map[normalized] = item

            matched = 0
            if not self.is_cancelled() and self.run_identification and Settings.TMDB_API_KEY:
                services = _build_domain_services(db)
                series_repository = services["series_repository"]
                reconciliation_service = services["reconciliation_service"]
                tmdb_client = TMDBClient(Settings.TMDB_API_KEY)
                matcher = TMDBAutoMatcher(tmdb_client)
                unmatched_items = [
                    item for item in db.get_all_series(categories)
                    if not item.get("tmdb_id")
                ]

                for item in unmatched_items:
                    if self.is_cancelled():
                        return None
                    match_result = matcher.match_content(item)
                    if not match_result:
                        continue
                    update_result = series_repository.update_tmdb_identity(item["id"], match_result)
                    reconciliation_service.on_tmdb_changed(
                        item["id"],
                        update_result.get("previous_item") or {},
                        update_result.get("updated_item") or {},
                        is_library=True,
                    )
                    matched += 1

            db_items = db.get_all_series(categories)
            merged_items = []
            for item in db_items:
                normalized = db.normalize_path(item.get("folder_path", "")).lower()
                local_info = local_map.get(normalized, {})
                merged = dict(item)
                merged["path"] = local_info.get("path") or item.get("folder_path", "")
                merged["video_count"] = local_info.get("video_count", 0)
                merged["cover_path"] = local_info.get("cover_path")
                merged_items.append(merged)

            return {
                "categories": categories,
                "items": merged_items,
                "total_titles": len(merged_items),
                "total_items": sum(item.get("video_count", 0) for item in merged_items),
                "matched": matched,
            }
        finally:
            db.close()


class ManualUpdateTask(BackgroundTask):
    def __init__(self, series_id, changes, *, is_library=False):
        super().__init__()
        self.series_id = series_id
        self.changes = dict(changes or {})
        self.is_library = is_library

    def execute(self):
        db = DatabaseManager()
        try:
            services = _build_domain_services(db)
            series_repository = services["series_repository"]
            reconciliation_service = services["reconciliation_service"]
            self.signals.log_signal.emit("💾 Guardando cambios manuales en segundo plano...")
            save_result = series_repository.save_manual_changes(
                self.series_id,
                self.changes,
                recalculate_missing=False,
            )
            result = reconciliation_service.on_manual_update(
                self.series_id,
                save_result.get("previous_item") or {},
                save_result.get("updated_item") or {},
                self.changes,
                is_library=self.is_library,
            )

            # Renombrar carpeta al nombre de TMDB si está habilitado y se asignó TMDB
            if (
                Settings.AUTO_RENAME_FOLDER_ON_TMDB_ASSIGN
                and 'tmdb_name' in self.changes
                and self.changes.get('tmdb_name')
            ):
                rename_result = db.rename_series_folder(self.series_id, self.changes['tmdb_name'])
                if rename_result.get('renamed'):
                    self.signals.log_signal.emit(
                        f"📁 Carpeta renombrada: '{rename_result['old_name']}' -> '{rename_result['new_name']}'"
                    )
                elif not rename_result.get('success'):
                    self.signals.log_signal.emit(
                        f"⚠️ No se pudo renombrar la carpeta: {rename_result.get('error', 'Error desconocido')}"
                    )

            updated_item = series_repository.get_by_id(self.series_id)
            return {
                "series_id": self.series_id,
                "is_library": self.is_library,
                "changes": self.changes,
                "updated_item": updated_item,
                "recalculated_missing": result.get("recalculated_missing", False),
                "missing_count": result.get("missing_count", 0),
                "reset_hispashare_url": result.get("reset_hispashare_url", False),
                "recheck_hispashare_url": result.get("recheck_hispashare_url", False),
                "previous_hispashare_url": result.get("previous_hispashare_url", ""),
            }
        finally:
            db.close()


class SeriesRefreshTask(BackgroundTask):
    def execute(self):
        db = DatabaseManager()
        try:
            return {
                "items": db.get_all_series(db.get_episodic_categories()),
            }
        finally:
            db.close()


class MissingRefreshTask(BackgroundTask):
    def execute(self):
        db = DatabaseManager()
        try:
            return {
                "items": db.get_missing_episodes(),
            }
        finally:
            db.close()


class LibrarySnapshotTask(BackgroundTask):
    def execute(self):
        db = DatabaseManager()
        try:
            categories = Settings.get_single_file_categories()
            return {
                "items": db.get_all_series(categories),
                "categories": categories,
            }
        finally:
            db.close()


class DownloadsRefreshTask(BackgroundTask):
    def execute(self):
        downloads_dir = Path("downloads")
        if not downloads_dir.exists():
            return {
                "text": "No hay archivos de descarga disponibles.",
                "count": 0,
            }

        all_links = []
        for file_path in sorted(downloads_dir.glob("*.txt")):
            try:
                content = file_path.read_text(encoding="utf-8").strip()
            except Exception as e:
                self.signals.log_signal.emit(f"⚠️ Error leyendo {file_path.name}: {e}")
                continue

            if content:
                all_links.append(f"# {file_path.name}\n{content}\n")

        return {
            "text": "\n".join(all_links) if all_links else "No se encontraron enlaces ed2k.",
            "count": len(all_links),
        }


class EmuleSendTask(BackgroundTask):
    def __init__(self, *, text=None, series_id=None):
        super().__init__()
        self.text = text
        self.series_id = series_id

    def execute(self):
        db = DatabaseManager()
        try:
            if self.series_id is not None:
                from core.hispashare.downloader import HispashareDownloader

                downloader = HispashareDownloader(None, db)
                result = downloader.send_links_to_emule(self.series_id)
                result["mode"] = "series"
                result["series_id"] = self.series_id
                return result

            from core.emule.client import EmuleClient

            emule_client = EmuleClient()
            if not emule_client.is_configured():
                raise RuntimeError(
                    "Por favor configura eMule en la pestaña de Configuración antes de enviar enlaces"
                )

            ed2k_links = emule_client.extract_ed2k_links_from_text(self.text or "")
            if not ed2k_links:
                raise RuntimeError("No se encontraron enlaces ed2k válidos")

            result = emule_client.add_multiple_ed2k_links(ed2k_links)
            result["mode"] = "all"
            return result
        finally:
            db.close()


class TMDBRescanTask(BackgroundTask):
    def __init__(self, item_data, *, is_library=False):
        super().__init__()
        self.item_data = dict(item_data or {})
        self.is_library = is_library

    def execute(self):
        if not Settings.TMDB_API_KEY:
            raise RuntimeError("TMDB_API_KEY no configurada")

        db = DatabaseManager()
        try:
            services = _build_domain_services(db)
            series_repository = services["series_repository"]
            reconciliation_service = services["reconciliation_service"]
            decision_log_repository = services["decision_log_repository"]
            tmdb_client = TMDBClient(Settings.TMDB_API_KEY)
            matcher = TMDBAutoMatcher(tmdb_client)

            if self.is_library:
                match_result = matcher.match_content(self.item_data)
            else:
                local_episodes = db.get_local_episodes(self.item_data["id"])
                seasons = {}
                for ep in local_episodes:
                    season = ep.get("season")
                    if season is None:
                        continue
                    seasons[season] = seasons.get(season, 0) + 1

                local_series = {
                    "id": self.item_data["id"],
                    "name": self.item_data["name"],
                    "folder_path": self.item_data.get("folder_path", ""),
                    "seasons": seasons,
                    "episodes": local_episodes,
                }
                match_result = matcher.match_series(local_series)

            updated_item = series_repository.get_by_id(self.item_data["id"])
            reconciliation_result = {
                "recalculated_missing": False,
                "missing_count": 0,
                "reset_hispashare_url": False,
                "recheck_hispashare_url": False,
                "previous_hispashare_url": "",
            }
            if match_result:
                update_result = series_repository.update_tmdb_identity(self.item_data["id"], match_result)
                updated_item = update_result.get("updated_item") or updated_item
                reconciliation_result = reconciliation_service.on_tmdb_changed(
                    self.item_data["id"],
                    update_result.get("previous_item") or {},
                    updated_item or {},
                    is_library=self.is_library,
                )
                decision_log_repository.append(
                    self.item_data["id"],
                    "tmdb_rescan_matched",
                    "TMDB reasignado desde reescaneo manual",
                    {
                        "tmdb_id": match_result.get("tmdb_id"),
                        "name": match_result.get("name"),
                        "year": match_result.get("year"),
                        "confidence_score": match_result.get("confidence_score"),
                    },
                )
            else:
                decision_log_repository.append(
                    self.item_data["id"],
                    "tmdb_rescan_no_match",
                    "No se encontró un match TMDB adecuado en el reescaneo manual",
                    {
                        "name": self.item_data.get("name", ""),
                        "is_library": self.is_library,
                    },
                )

            return {
                "series_id": self.item_data["id"],
                "is_library": self.is_library,
                "matched": bool(match_result),
                "match_result": match_result,
                "updated_item": updated_item,
                "recalculated_missing": reconciliation_result.get("recalculated_missing", False),
                "missing_count": reconciliation_result.get("missing_count", 0),
                "reset_hispashare_url": reconciliation_result.get("reset_hispashare_url", False),
                "recheck_hispashare_url": reconciliation_result.get("recheck_hispashare_url", False),
                "previous_hispashare_url": reconciliation_result.get("previous_hispashare_url", ""),
            }
        finally:
            db.close()


class AutoMatchTask(BackgroundTask):
    def execute(self):
        if not Settings.TMDB_API_KEY:
            raise RuntimeError("TMDB_API_KEY no configurada")

        db = DatabaseManager()
        try:
            series_list = db.get_all_series(db.get_episodic_categories())
            unmatched_series = [item for item in series_list if not item.get("tmdb_id")]
            if not unmatched_series:
                return {
                    "total": 0,
                    "matched": 0,
                }

            services = _build_domain_services(db)
            series_repository = services["series_repository"]
            reconciliation_service = services["reconciliation_service"]
            tmdb_client = TMDBClient(Settings.TMDB_API_KEY)
            matcher = TMDBAutoMatcher(tmdb_client)
            matched = 0

            for series in unmatched_series:
                if self.is_cancelled():
                    return None

                self.signals.log_signal.emit(f"   🔗 Matching: {series['name']}...")
                local_episodes = db.get_local_episodes(series["id"])
                seasons = {}
                for ep in local_episodes:
                    season = ep.get("season")
                    if season is None:
                        continue
                    seasons[season] = seasons.get(season, 0) + 1

                local_series = {
                    "id": series["id"],
                    "name": series["name"],
                    "folder_path": series.get("folder_path", ""),
                    "seasons": seasons,
                    "episodes": local_episodes,
                }
                match_result = matcher.match_series(local_series)

                if match_result:
                    update_result = series_repository.update_tmdb_identity(series["id"], match_result)
                    reconciliation_service.on_tmdb_changed(
                        series["id"],
                        update_result.get("previous_item") or {},
                        update_result.get("updated_item") or {},
                    )
                    matched += 1
                    confidence_emoji = "✅" if match_result["confidence_score"] >= 0.85 else "⚠️"
                    self.signals.log_signal.emit(
                        f"      {confidence_emoji} {match_result['name']} "
                        f"({match_result['year']}) - "
                        f"Confianza: {match_result['confidence_score']:.1%}"
                    )
                else:
                    self.signals.log_signal.emit(f"      ❌ No se encontró match para {series['name']}")

            return {
                "total": len(unmatched_series),
                "matched": matched,
            }
        finally:
            db.close()


class SearchReactivateTask(BackgroundTask):
    def __init__(self, series_id, *, is_library=False):
        super().__init__()
        self.series_id = series_id
        self.is_library = is_library

    def execute(self):
        db = DatabaseManager()
        try:
            services = _build_domain_services(db)
            series_repository = services["series_repository"]
            decision_log_repository = services["decision_log_repository"]
            series_repository.mark_search_enabled(self.series_id)
            updated_item = series_repository.get_by_id(self.series_id)
            decision_log_repository.append(
                self.series_id,
                "search_reactivated",
                "Se reactivó la búsqueda automática de Hispashare",
                {
                    "is_library": self.is_library,
                },
            )
            return {
                "series_id": self.series_id,
                "is_library": self.is_library,
                "updated_item": updated_item,
            }
        finally:
            db.close()


class SeriesMonitoredTask(BackgroundTask):
    def __init__(self, series_ids, monitored, *, is_library=False):
        super().__init__()
        self.series_ids = list(series_ids)
        self.monitored = monitored
        self.is_library = is_library

    def execute(self):
        db = DatabaseManager()
        try:
            services = _build_domain_services(db)
            series_repository = services["series_repository"]
            decision_log_repository = services["decision_log_repository"]
            updated_items = []
            for series_id in self.series_ids:
                series_repository.set_monitored(series_id, self.monitored)
                decision_log_repository.append(
                    series_id,
                    "monitored_changed",
                    "Serie monitorizada" if self.monitored else "Serie marcada como no monitorizada",
                    {"monitored": self.monitored, "is_library": self.is_library},
                )
                updated_item = series_repository.get_by_id(series_id)
                if updated_item:
                    updated_items.append(updated_item)
            return {
                "is_library": self.is_library,
                "monitored": self.monitored,
                "updated_items": updated_items,
            }
        finally:
            db.close()


class CoverUrlResolveTask(BackgroundTask):
    def __init__(self, series_id, hispashare_url):
        super().__init__()
        self.series_id = series_id
        self.hispashare_url = hispashare_url or ""

    def execute(self):
        db = DatabaseManager()
        try:
            cover_url = db.derive_hispashare_cover_url(self.hispashare_url) or ""
            if cover_url:
                db.update_hispashare_cover_url(self.series_id, cover_url)
            updated_item = db.get_series_by_id(self.series_id)
            return {
                "series_id": self.series_id,
                "cover_url": cover_url,
                "updated_item": updated_item,
            }
        finally:
            db.close()


class ImmediateHispashareSearchTask(BackgroundTask):
    def __init__(self, series_id, *, is_library=False, force_recheck=False):
        super().__init__()
        self.series_id = series_id
        self.is_library = is_library
        self.force_recheck = force_recheck

    def execute(self):
        db = DatabaseManager()
        try:
            services = _build_domain_services(db)
            source_match_service = services["source_match_service"]
            result = source_match_service.revalidate_or_search(
                self.series_id,
                force_recheck=self.force_recheck,
                log_callback=self.signals.log_signal.emit,
            )
            result["is_library"] = self.is_library
            return result
        finally:
            db.close()
