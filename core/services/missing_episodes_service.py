from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class MissingEpisodesService:
    def __init__(self, db_manager, series_repository, decision_log_repository):
        self.db = db_manager
        self.series_repository = series_repository
        self.decision_log_repository = decision_log_repository

    def rebuild_for_series(self, series_id: int) -> dict:
        item = self.series_repository.get_by_id(series_id)
        if not item:
            return {
                "recalculated_missing": False,
                "missing_count": 0,
            }

        missing_count = self.db.recalculate_missing_episodes_for_series(series_id)
        self.decision_log_repository.append(
            series_id,
            "missing_rebuilt",
            f"Recalculados episodios faltantes para '{item.get('name', series_id)}'",
            {
                "tmdb_id": item.get("tmdb_id"),
                "missing_count": missing_count,
            },
        )
        return {
            "recalculated_missing": True,
            "missing_count": missing_count,
        }

    def rebuild_for_categories(self, categories: list[str] | tuple[str, ...]) -> dict:
        series_list = self.db.get_series_with_tmdb(categories)
        all_with_tmdb = self.db.get_series_with_tmdb(categories, include_needs_review=True)
        skipped_needs_review = len(all_with_tmdb) - len(series_list)
        total_missing = 0
        recalculated_series = 0
        series_results = []
        failed = []

        for series in series_list:
            # Una serie que falla (TMDB caído, BD bloqueada...) conserva sus
            # faltantes anteriores y no impide recalcular las demás.
            try:
                result = self.rebuild_for_series(series["id"])
            except Exception as e:
                logger.warning(f"No se pudieron recalcular los faltantes de '{series.get('name')}': {e}")
                failed.append({"series_id": series["id"], "series_name": series.get("name", ""), "error": str(e)})
                continue
            missing_count = int(result.get("missing_count", 0))
            total_missing += missing_count
            recalculated_series += 1
            series_results.append(
                {
                    "series_id": series["id"],
                    "series_name": series.get("name", ""),
                    "missing_count": missing_count,
                }
            )

        return {
            "recalculated_series": recalculated_series,
            "total_missing": total_missing,
            "series_results": series_results,
            "skipped_needs_review": skipped_needs_review,
            "failed": failed,
        }
