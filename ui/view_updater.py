from __future__ import annotations

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QTableWidgetItem

_COLOR_HIGHLIGHT = QColor(173, 216, 230)
_COLOR_CONF_HIGH = QColor(144, 238, 144)
_COLOR_CONF_MED = QColor(255, 255, 224)
_COLOR_CONF_LOW = QColor(255, 182, 193)


class ViewUpdater:
    def __init__(self, window):
        self.window = window

    def update_single_file_item_cache(self, series_id, item_data=None):
        if series_id is None:
            return
        if item_data is None:
            item_data = self.window.view_state.libraries.get_item_by_id(series_id)
        if not item_data:
            return

        updated = self.window.view_state.libraries.update_item(series_id, item_data)
        if updated:
            if self.window.is_library_tab_active():
                self.refresh_library_row(series_id, item_data)
            else:
                self.window.library_list_dirty = True
        else:
            self.window.library_list_dirty = True

    def update_series_tmdb_in_table(self, series_id, match_data):
        try:
            row = self._find_row_by_series_id("series", self.window.series_table, series_id)
            if row is None:
                return

            table = self.window.series_table
            year_item = QTableWidgetItem(str(match_data.get("year", "")))
            table.setItem(row, 2, year_item)

            tmdb_item = QTableWidgetItem(str(match_data.get("tmdb_id", "")))
            table.setItem(row, 3, tmdb_item)

            confidence = match_data.get("confidence_score", 0)
            confidence_item = QTableWidgetItem(f"{confidence:.1%}")
            if confidence >= 0.85:
                confidence_item.setBackground(_COLOR_CONF_HIGH)
            elif confidence >= 0.7:
                confidence_item.setBackground(_COLOR_CONF_MED)
            else:
                confidence_item.setBackground(_COLOR_CONF_LOW)
            table.setItem(row, 4, confidence_item)

            original_bg = year_item.background()
            year_item.setBackground(_COLOR_HIGHLIGHT)
            tmdb_item.setBackground(_COLOR_HIGHLIGHT)
            QTimer.singleShot(3000, lambda: self.window.restore_row_background(row, [2, 3, 4], original_bg))
        except Exception as e:
            self.window.logger.error(f"Error actualizando TMDB en tabla: {e}")

    def refresh_series_row(self, series_id, series_data=None):
        try:
            cached_series_data = self.window.view_state.series.get_item_by_id(series_id)
            if series_data is None:
                series_data = cached_series_data
            if not series_data:
                return

            if cached_series_data:
                merged_series_data = dict(cached_series_data)
                merged_series_data.update(series_data)
                series_data = merged_series_data

            self.window.view_state.series.update_item(series_id, series_data)
            row = self._find_row_by_series_id("series", self.window.series_table, series_id)
            if row is not None:
                self.window.populate_series_row(row, series_data)
                self.window.schedule_visible_cover_refresh("series", delay_ms=0)
        except Exception as e:
            self.window.logger.error(f"Error refrescando fila de serie: {e}")

    def refresh_library_row(self, series_id, item_data=None):
        try:
            if item_data is None:
                item_data = self.window.view_state.libraries.get_item_by_id(series_id)
            if not item_data:
                return

            self.window.view_state.libraries.update_item(series_id, item_data)
            item_data = next(
                (item for item in self.window.current_single_file_list if item.get("id") == series_id),
                item_data,
            )

            row = self._find_row_by_series_id("library", self.window.library_table, series_id)
            if row is not None:
                self.window.populate_single_file_library_row(row, item_data)
                self.window.schedule_visible_cover_refresh("library", delay_ms=0)
        except Exception as e:
            self.window.logger.error(f"Error refrescando fila de biblioteca: {e}")

    def _find_row_by_series_id(self, table_kind, table, series_id):
        indexed_row = self.window.get_indexed_row(table_kind, series_id)
        if indexed_row is not None and indexed_row < table.rowCount():
            item = table.item(indexed_row, 0)
            if item and hasattr(item, "series_id") and item.series_id == series_id:
                return indexed_row
        for row in range(table.rowCount()):
            item = table.item(row, 0)
            if item and hasattr(item, "series_id") and item.series_id == series_id:
                self.window.remember_row_index(table_kind, series_id, row)
                return row
        return None
