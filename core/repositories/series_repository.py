from __future__ import annotations


class SeriesRepository:
    def __init__(self, db_manager):
        self.db = db_manager

    def get_by_id(self, series_id: int) -> dict | None:
        return self.db.get_series_by_id(series_id)

    def list_by_categories(self, categories: list[str] | None = None) -> list[dict]:
        return self.db.get_all_series(categories)

    def save_manual_changes(
        self,
        series_id: int,
        changes: dict,
        *,
        recalculate_missing: bool = False,
    ) -> dict:
        previous_item = self.get_by_id(series_id)
        raw_result = self.db.update_series_manual(
            series_id,
            changes,
            recalculate_missing=recalculate_missing,
        ) or {}
        updated_item = self.get_by_id(series_id)
        return {
            "previous_item": previous_item,
            "updated_item": updated_item,
            **raw_result,
        }

    def update_tmdb_identity(self, series_id: int, match_data: dict) -> dict:
        previous_item = self.get_by_id(series_id)
        self.db.update_tmdb_match(series_id, match_data)
        updated_item = self.get_by_id(series_id)
        return {
            "previous_item": previous_item,
            "updated_item": updated_item,
        }

    def mark_search_enabled(self, series_id: int):
        self.db.enable_series_search(series_id)

    def mark_search_disabled(self, series_id: int, reason: str | None = None):
        self.db.disable_series_search(series_id)

    def set_monitored(self, series_id: int, monitored: bool):
        self.db.set_series_monitored(series_id, monitored)
