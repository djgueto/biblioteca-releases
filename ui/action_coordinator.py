from __future__ import annotations

from config.settings import Settings

from .workers import (
    AutoMatchTask,
    CoverUrlResolveTask,
    EmuleSendTask,
    ImmediateHispashareSearchTask,
    ManualUpdateTask,
    SearchReactivateTask,
    SeriesMonitoredTask,
    TMDBRescanTask,
)


class ActionCoordinator:
    def __init__(self, window):
        self.window = window
        self.manual_update_task = None
        self.emule_send_task = None
        self.tmdb_rescan_task = None
        self.auto_match_task = None
        self.search_reactivate_task = None
        self.immediate_hispashare_search_task = None
        self.cover_url_tasks = {}
        self.monitored_tasks = set()
        self.emule_send_context = None
        self.tmdb_rescan_context = None
        self.search_reactivate_context = None
        self.immediate_hispashare_search_context = None

    def start_manual_update(self, item_data, changes, *, is_library=False):
        if self.manual_update_task is not None:
            self.window.show_information(
                "Actualización en curso",
                "Ya hay una actualización manual en progreso. Espera a que termine.",
            )
            return

        item_name = item_data.get("name", "Elemento")
        self.window._set_manual_update_busy(True, "⏳ Guardando cambios manuales...")
        self.window.append_log(f"💾 Guardando cambios manuales para '{item_name}'...")

        task = ManualUpdateTask(
            item_data["id"],
            changes,
            is_library=is_library,
        )
        task.signals.log_signal.connect(self.window.append_log)
        task.signals.finished_signal.connect(self.window.on_manual_update_finished)
        task.signals.error_signal.connect(self.window.on_manual_update_error)
        task.signals.done_signal.connect(self.window.on_manual_update_done)
        self.manual_update_task = task
        self.window.worker_pool.start(task)

    def on_manual_update_done(self):
        self.manual_update_task = None

    def start_emule_send(self, *, text=None, series_id=None, series_name=None):
        if self.emule_send_task is not None:
            self.window.show_information("eMule", "Ya hay un envío a eMule en curso")
            return

        self.emule_send_context = {
            "series_name": series_name,
            "series_id": series_id,
        }
        self.window._set_downloads_busy(True)
        if hasattr(self.window, "status_label"):
            self.window.status_label.setText("🔗 Enviando enlaces a eMule...")

        if series_name:
            self.window.append_log(f"🔗 Enviando enlaces de '{series_name}' a eMule...")
        else:
            self.window.append_log("🔗 Enviando enlaces a eMule...")

        task = EmuleSendTask(text=text, series_id=series_id)
        task.signals.finished_signal.connect(self.window.on_emule_send_finished)
        task.signals.error_signal.connect(self.window.on_emule_send_error)
        task.signals.done_signal.connect(self.window.on_emule_send_task_done)
        self.emule_send_task = task
        self.window.worker_pool.start(task)

    def on_emule_send_task_done(self):
        self.emule_send_task = None
        self.emule_send_context = None

    def start_tmdb_rescan(self, item_data, *, is_library=False):
        if self.tmdb_rescan_task is not None:
            self.window.show_information("TMDB", "Ya hay un reescaneo TMDB en curso")
            return

        if not Settings.TMDB_API_KEY:
            self.window.show_warning("Configuración", "TMDB_API_KEY no configurada")
            return

        item_name = item_data.get("name", "Elemento")
        scope = "biblioteca" if is_library else "serie"
        self.tmdb_rescan_context = {
            "name": item_name,
            "is_library": is_library,
        }
        self.window.append_log(f"🔄 Reescaneando {scope} '{item_name}' en TMDB...")
        if hasattr(self.window, "status_label"):
            self.window.status_label.setText("🎬 Reescaneando TMDB...")

        task = TMDBRescanTask(item_data, is_library=is_library)
        task.signals.finished_signal.connect(self.window.on_tmdb_rescan_finished)
        task.signals.error_signal.connect(self.window.on_tmdb_rescan_error)
        task.signals.done_signal.connect(self.window.on_tmdb_rescan_task_done)
        self.tmdb_rescan_task = task
        self.window.worker_pool.start(task)

    def on_tmdb_rescan_task_done(self):
        self.tmdb_rescan_task = None
        self.tmdb_rescan_context = None

    def auto_match_new_series(self):
        if self.auto_match_task is not None:
            self.window.show_information("TMDB", "Ya hay un auto-match TMDB en curso")
            return

        if not Settings.TMDB_API_KEY:
            self.window.show_warning("Configuración", "TMDB_API_KEY no configurada")
            return

        self.window.append_log("🎬 Iniciando auto-match de series sin TMDB...")
        if hasattr(self.window, "status_label"):
            self.window.status_label.setText("🎬 Ejecutando auto-match TMDB...")

        task = AutoMatchTask()
        task.signals.log_signal.connect(self.window.append_log)
        task.signals.finished_signal.connect(self.window.on_auto_match_finished)
        task.signals.error_signal.connect(self.window.on_auto_match_error)
        task.signals.done_signal.connect(self.window.on_auto_match_task_done)
        self.auto_match_task = task
        self.window.worker_pool.start(task)

    def on_auto_match_task_done(self):
        self.auto_match_task = None

    def start_search_reactivation(self, item_data, *, is_library=False):
        if self.search_reactivate_task is not None:
            self.window.show_information(
                "Búsqueda en curso",
                "Ya hay una reactivación de búsqueda en progreso. Espera a que termine.",
            )
            return

        item_name = item_data.get("name", "Elemento")
        scope = "biblioteca" if is_library else "serie"
        self.search_reactivate_context = {
            "name": item_name,
            "is_library": is_library,
            "series_id": item_data.get("id"),
        }
        self.window.append_log(f"🔍 Reactivando búsqueda Hispashare para {scope} '{item_name}'...")
        if hasattr(self.window, "status_label"):
            self.window.status_label.setText("🔍 Reactivando búsqueda Hispashare...")

        task = SearchReactivateTask(item_data["id"], is_library=is_library)
        task.signals.finished_signal.connect(self.window.on_search_reactivate_finished)
        task.signals.error_signal.connect(self.window.on_search_reactivate_error)
        task.signals.done_signal.connect(self.window.on_search_reactivate_task_done)
        self.search_reactivate_task = task
        self.window.worker_pool.start(task)

    def start_set_monitored(self, items, monitored, *, is_library=False):
        state = "monitorizadas" if monitored else "NO monitorizadas"
        self.window.append_log(f"👁 Marcando {len(items)} elemento(s) como {state}...")

        task = SeriesMonitoredTask([item["id"] for item in items], monitored, is_library=is_library)
        task.signals.finished_signal.connect(self.window.on_set_monitored_finished)
        task.signals.error_signal.connect(self.window.on_set_monitored_error)
        task.signals.done_signal.connect(lambda t=task: self.monitored_tasks.discard(t))
        self.monitored_tasks.add(task)
        self.window.worker_pool.start(task)

    def on_search_reactivate_task_done(self):
        self.search_reactivate_task = None
        self.search_reactivate_context = None

    def start_immediate_hispashare_search(self, item_data, *, is_library=False, force_recheck=False):
        if self.immediate_hispashare_search_task is not None:
            self.window.append_log(
                "ℹ️ Ya hay una búsqueda inmediata de Hispashare en curso. Esta se omitirá."
            )
            return False

        item_name = item_data.get("tmdb_name") or item_data.get("name", "Elemento")
        scope = "biblioteca" if is_library else "serie"
        self.immediate_hispashare_search_context = {
            "name": item_name,
            "is_library": is_library,
            "series_id": item_data.get("id"),
        }
        if force_recheck:
            self.window.append_log(
                f"🔁 Revalidando URL de Hispashare para {scope} '{item_name}' con el TMDB corregido..."
            )
        else:
            self.window.append_log(
                f"🚀 Lanzando búsqueda inmediata de Hispashare para {scope} '{item_name}'..."
            )
        if hasattr(self.window, "status_label"):
            self.window.status_label.setText(
                "🔁 Revalidando URL de Hispashare..."
                if force_recheck
                else "🔎 Buscando URL de Hispashare ahora..."
            )

        task = ImmediateHispashareSearchTask(
            item_data["id"],
            is_library=is_library,
            force_recheck=force_recheck,
        )
        task.signals.log_signal.connect(self.window.append_log)
        task.signals.finished_signal.connect(self.window.on_immediate_hispashare_search_finished)
        task.signals.error_signal.connect(self.window.on_immediate_hispashare_search_error)
        task.signals.done_signal.connect(self.window.on_immediate_hispashare_search_done)
        self.immediate_hispashare_search_task = task
        self.window.worker_pool.start(task)
        return True

    def on_immediate_hispashare_search_done(self):
        self.immediate_hispashare_search_task = None
        self.immediate_hispashare_search_context = None

    def start_cover_url_resolve(self, series_id, hispashare_url):
        if not series_id or not hispashare_url or series_id in self.cover_url_tasks:
            return

        task = CoverUrlResolveTask(series_id, hispashare_url)
        task.signals.finished_signal.connect(self.window.on_cover_url_resolve_finished)
        task.signals.error_signal.connect(self.window.on_cover_url_resolve_error)
        task.signals.done_signal.connect(
            lambda sid=series_id: self.window.on_cover_url_resolve_task_done(sid)
        )
        self.cover_url_tasks[series_id] = task
        self.window.worker_pool.start(task)

    def on_cover_url_resolve_task_done(self, series_id):
        self.cover_url_tasks.pop(series_id, None)

    def cancel_managed_tasks(self, stop_task):
        cover_url_ok = all(
            stop_task(task, f"cover_url_{series_id}")
            for series_id, task in list(self.cover_url_tasks.items())
        )
        return {
            "manual_update": stop_task(self.manual_update_task, "manual_update"),
            "emule_send": stop_task(self.emule_send_task, "emule_send"),
            "tmdb_rescan": stop_task(self.tmdb_rescan_task, "tmdb_rescan"),
            "auto_match": stop_task(self.auto_match_task, "auto_match"),
            "search_reactivate": stop_task(self.search_reactivate_task, "search_reactivate"),
            "immediate_hispashare_search": stop_task(
                self.immediate_hispashare_search_task,
                "immediate_hispashare_search",
            ),
            "cover_url": cover_url_ok,
        }
