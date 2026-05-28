from PyQt6.QtCore import QThread, pyqtSignal

from config.settings import Settings
from core.database.db_manager import DatabaseManager
from core.tmdb.api_client import TMDBClient
from core.tmdb.auto_matcher import TMDBAutoMatcher


class TMDBRecalcThread(QThread):
    progress_signal = pyqtSignal(int, str)
    log_signal = pyqtSignal(str)
    series_updated_signal = pyqtSignal(int)
    finished_signal = pyqtSignal(int, int, int)  # total, updated, failed
    error_signal = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.running = True

    def stop(self):
        self.running = False

    def run(self):
        try:
            if not Settings.TMDB_API_KEY:
                self.error_signal.emit("TMDB_API_KEY no configurada")
                return

            db = DatabaseManager()
            tmdb = TMDBClient(Settings.TMDB_API_KEY)
            matcher = TMDBAutoMatcher(tmdb)

            series_list = db.get_all_series()
            total = len(series_list)
            updated = 0
            failed = 0

            if total == 0:
                self.finished_signal.emit(0, 0, 0)
                return

            for idx, series in enumerate(series_list, 1):
                if not self.running:
                    break

                name = series.get("name", "")
                series_id = series.get("id")
                self.progress_signal.emit(int((idx - 1) * 100 / total), f"🎬 TMDB: {idx}/{total} {name}")

                try:
                    tmdb_id = series.get("tmdb_id")

                    if tmdb_id:
                        details = tmdb.get_series_details(tmdb_id)
                        if not details:
                            failed += 1
                            self.log_signal.emit(f"⚠️ TMDB sin respuesta para '{name}' (tmdb_id={tmdb_id})")
                            continue

                        score = matcher.calculate_fast_score(
                            {'name': name, 'folder_path': series.get('folder_path', '')},
                            details
                        )

                        year_str = details.get('first_air_date', '') or ''
                        year = int(year_str[:4]) if len(year_str) >= 4 and year_str[:4].isdigit() else None
                        match_data = {
                            'tmdb_id': tmdb_id,
                            'name': details.get('name', series.get('tmdb_name') or name),
                            'year': year,
                            'confidence_score': score,
                            'needs_review': score < Settings.AUTO_MATCH_THRESHOLD
                        }
                        db.update_tmdb_match(series_id, match_data)
                        updated += 1
                        self.series_updated_signal.emit(series_id)
                        continue

                    match_result = matcher.match_series(
                        {
                            'id': series_id,
                            'name': name,
                            'folder_path': series.get('folder_path', ''),
                            'seasons': {},
                            'episodes': []
                        }
                    )

                    if not match_result:
                        failed += 1
                        self.log_signal.emit(f"⚠️ Sin match TMDB para '{name}'")
                        continue

                    db.update_tmdb_match(series_id, match_result)
                    updated += 1
                    self.series_updated_signal.emit(series_id)
                    self.log_signal.emit(
                        f"✅ {name} -> {match_result['name']} ({match_result.get('year', '')}) "
                        f"{match_result['confidence_score']:.1%}"
                    )

                except Exception as e:
                    failed += 1
                    self.log_signal.emit(f"❌ Error TMDB en '{name}': {str(e)}")

            self.progress_signal.emit(100, "✅ Recalculo TMDB completado")
            self.finished_signal.emit(total, updated, failed)

        except Exception as e:
            self.error_signal.emit(str(e))
