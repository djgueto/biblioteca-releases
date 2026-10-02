from __future__ import annotations

from config.settings import Settings

from .workers import (
    DownloadsRefreshTask,
    LibraryRefreshTask,
    LibrarySnapshotTask,
    MissingRefreshTask,
    SeriesRefreshTask,
)


class RefreshCoordinator:
    def __init__(self, window):
        self.window = window
        self.library_refresh_task = None
        self.library_snapshot_task = None
        self.series_refresh_task = None
        self.missing_refresh_task = None
        self.downloads_refresh_task = None
        self.pending_library_refresh_requested = False
        self.pending_library_refresh_identification = False
        self.pending_series_refresh = False
        self.pending_missing_refresh = False
        self.pending_downloads_refresh = False

    def request_library_refresh(self, run_identification=False):
        self.window.library_list_dirty = True
        if self.window.is_library_tab_active():
            self.start_library_refresh(run_identification=run_identification, force=True)

    def start_library_snapshot_refresh(self, force=False):
        if self.window.is_closing:
            return
        if self.library_snapshot_task is not None:
            return
        if not force and self.window.all_single_file_list:
            return

        task = LibrarySnapshotTask()
        task.signals.finished_signal.connect(self.window.on_library_snapshot_finished)
        task.signals.error_signal.connect(self.window.on_library_snapshot_error)
        task.signals.done_signal.connect(self.window.on_library_snapshot_task_done)
        self.library_snapshot_task = task
        self.window.worker_pool.start(task)

    def start_library_refresh(self, run_identification=False, force=False):
        if self.window.is_closing:
            return
        if not force and not self.window.is_library_tab_active():
            self.window.library_list_dirty = True
            return

        categories = Settings.get_single_file_categories()
        if self.library_refresh_task is not None:
            self.pending_library_refresh_requested = True
            self.pending_library_refresh_identification = (
                self.pending_library_refresh_identification or run_identification
            )
            return

        self.window._set_library_refresh_busy(True)
        self.window.status_label.setText("📚 Refrescando bibliotecas...")
        if hasattr(self.window, "library_summary_label"):
            self.window.library_summary_label.setText("Refrescando bibliotecas en segundo plano...")

        task = LibraryRefreshTask(categories, run_identification=run_identification)
        task.signals.log_signal.connect(self.window.append_log)
        task.signals.finished_signal.connect(self.window.on_library_refresh_finished)
        task.signals.error_signal.connect(self.window.on_library_refresh_error)
        task.signals.done_signal.connect(self.window.on_library_refresh_task_done)
        self.library_refresh_task = task
        self.window.worker_pool.start(task)

    def on_library_refresh_task_done(self):
        self.library_refresh_task = None
        self.window._set_library_refresh_busy(False)
        if self.window.is_closing:
            self.pending_library_refresh_requested = False
            self.pending_library_refresh_identification = False
            return
        if self.pending_library_refresh_requested or self.pending_library_refresh_identification:
            rerun_with_identification = self.pending_library_refresh_identification
            self.pending_library_refresh_requested = False
            self.pending_library_refresh_identification = False
            self.start_library_refresh(run_identification=rerun_with_identification)
            return
        if self.window.scheduler_start_pending:
            self.window.defer_scheduler_start()

    def on_library_snapshot_task_done(self):
        self.library_snapshot_task = None

    def refresh_series_list(self):
        if self.window.is_closing:
            return
        if self.series_refresh_task is not None:
            self.pending_series_refresh = True
            return

        self.pending_series_refresh = False
        self.window._set_series_refresh_busy(True)
        if hasattr(self.window, "status_label"):
            self.window.status_label.setText("📺 Cargando series...")

        task = SeriesRefreshTask()
        task.signals.finished_signal.connect(self.window.on_series_refresh_finished)
        task.signals.error_signal.connect(self.window.on_series_refresh_error)
        task.signals.done_signal.connect(self.window.on_series_refresh_task_done)
        self.series_refresh_task = task
        self.window.worker_pool.start(task)

    def on_series_refresh_task_done(self):
        self.series_refresh_task = None
        self.window._set_series_refresh_busy(False)
        if self.window.is_closing:
            self.pending_series_refresh = False
            return
        if self.pending_series_refresh:
            self.pending_series_refresh = False
            self.refresh_series_list()

    def refresh_missing_list(self, force=False):
        if not force and not self.window.is_missing_tab_active():
            self.window.missing_list_dirty = True
            return
        if self.window.is_closing:
            return
        if self.missing_refresh_task is not None:
            self.pending_missing_refresh = True
            return

        self.pending_missing_refresh = False
        self.window._set_missing_refresh_busy(True)
        if hasattr(self.window, "status_label"):
            self.window.status_label.setText("❌ Cargando episodios faltantes...")

        task = MissingRefreshTask()
        task.signals.finished_signal.connect(self.window.on_missing_refresh_finished)
        task.signals.error_signal.connect(self.window.on_missing_refresh_error)
        task.signals.done_signal.connect(self.window.on_missing_refresh_task_done)
        self.missing_refresh_task = task
        self.window.worker_pool.start(task)

    def on_missing_refresh_task_done(self):
        self.missing_refresh_task = None
        self.window._set_missing_refresh_busy(False)
        if self.window.is_closing:
            self.pending_missing_refresh = False
            return
        if self.pending_missing_refresh and self.window.is_missing_tab_active():
            self.pending_missing_refresh = False
            self.refresh_missing_list(force=True)

    def refresh_downloads(self):
        if self.window.is_closing:
            return
        if self.downloads_refresh_task is not None:
            self.pending_downloads_refresh = True
            return

        self.pending_downloads_refresh = False
        self.window._set_downloads_busy(True)
        if hasattr(self.window, "status_label"):
            self.window.status_label.setText("📥 Cargando enlaces de descarga...")

        task = DownloadsRefreshTask()
        task.signals.log_signal.connect(self.window.append_log)
        task.signals.finished_signal.connect(self.window.on_downloads_refresh_finished)
        task.signals.error_signal.connect(self.window.on_downloads_refresh_error)
        task.signals.done_signal.connect(self.window.on_downloads_refresh_task_done)
        self.downloads_refresh_task = task
        self.window.worker_pool.start(task)

    def on_downloads_refresh_task_done(self):
        self.downloads_refresh_task = None
        self.window._set_downloads_busy()
        if self.window.is_closing:
            self.pending_downloads_refresh = False
            return
        if self.pending_downloads_refresh:
            self.pending_downloads_refresh = False
            self.refresh_downloads()

    def cancel_managed_tasks(self, stop_task):
        return {
            "library_refresh": stop_task(self.library_refresh_task, "library_refresh"),
            "library_snapshot": stop_task(self.library_snapshot_task, "library_snapshot"),
            "series_refresh": stop_task(self.series_refresh_task, "series_refresh"),
            "missing_refresh": stop_task(self.missing_refresh_task, "missing_refresh"),
            "downloads_refresh": stop_task(self.downloads_refresh_task, "downloads_refresh"),
        }
