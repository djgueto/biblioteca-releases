"""Importación automática de lo descargado a la biblioteca (como Radarr/Sonarr).

Opcional y desactivada por defecto: si las descargas pasan antes por otro
programa (recodificar, subir a servidores...), ese programa decide cuándo
llegan a la biblioteca.

Solo importa lo que la app necesita o pidió, nunca otros archivos de la
carpeta de descargas:
- episodios que faltan en una serie vigilada (incluidas las "en camino");
- películas "en camino" (novedades aprobadas);
- mejoras que la app envió a descargar (grab_history), y solo si MediaInfo
  confirma que el archivo nuevo es mejor según el perfil de calidad. El
  archivo antiguo no se borra: se mueve a la carpeta de reemplazados.

Los nombres siguen las convenciones de la biblioteca:
  Serie/Season 03/Serie S03E01 - Título.ext  (o sin "Season" si la serie no
  usa subcarpetas) y Película (Año).ext en la raíz de su biblioteca.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path

from config.settings import Settings
from core.library.parser import EpisodeParser, detect_resolution_from_file
from core.services.quality import quality_from_local_episode
from core.services.quality_profile import QualityProfile
from core.wolfmax.category_scanner import _normalize, _title_score

logger = logging.getLogger(__name__)

SETTINGS_KEY = "importer"
VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".m4v", ".mov", ".wmv", ".ts", ".m2ts", ".mpg", ".mpeg", ".webm"}
PARTIAL_SUFFIXES = (".part", ".!qb", ".crdownload", ".tmp", ".importing")
_INVALID = re.compile(r'[<>:"/\\|?*]')
_YEAR = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)")
_CUT_TOKENS = re.compile(
    r"[\[(]|(?<![a-z])(2160p|1080p|720p|480p|4k|uhd|hdr|web-?dl|webrip|bluray|bdremux|brrip|hdtv|x26[45]|h\.?26[45]|hevc)(?![a-z])",
    re.IGNORECASE,
)
_EPISODE_MARK = re.compile(r"(?i)(?<![a-z0-9])(?:s\d{1,2}\s*[._ -]*e\d{1,3}|t\d{1,2}e\d{1,3}|\d{1,2}x\d{1,3})")
MATCH_THRESHOLD = 0.8
GRAB_WINDOW_DAYS = 30


@dataclass
class ImportSettings:
    enabled: bool = False
    source_dirs: list[str] = field(default_factory=list)
    mode: str = "copy"  # 'copy' (el original sigue compartiéndose en el cliente) | 'move'
    min_age_minutes: int = 10  # sin cambios en este tiempo = descarga terminada
    recycle_dir: str = ""  # adónde van los archivos sustituidos por una mejora

    @classmethod
    def load(cls, db) -> "ImportSettings":
        from core.repositories.app_settings_repository import AppSettingsRepository

        data = AppSettingsRepository(db).get(SETTINGS_KEY) or {}
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    def save(self, db) -> None:
        from core.repositories.app_settings_repository import AppSettingsRepository

        AppSettingsRepository(db).set(SETTINGS_KEY, asdict(self))

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ImportMatch:
    kind: str  # 'episode' | 'movie'
    series: dict
    season: int | None = None
    episode: int | None = None
    upgrade: bool = False
    old_path: str | None = None


def _comparable(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    return _normalize("".join(c for c in text if not unicodedata.combining(c)))


def _clean_name(stem: str) -> str:
    return re.sub(r"[._]+", " ", stem).strip()


def _safe(text: str) -> str:
    return re.sub(r"\s+", " ", _INVALID.sub("", text or "")).strip(" .")


class ImportService:
    def __init__(self, db, *, settings: ImportSettings | None = None, log=None, now=None):
        self.db = db
        self.settings = settings or ImportSettings.load(db)
        self.log = log or (lambda message: logger.info(message))
        self.now = now or time.time
        self.parser = EpisodeParser()
        self.profile = QualityProfile.load(db)
        # Tablas que se consultan al identificar archivos: se crean si faltan.
        from core.discovery.repository import WantedRepository
        from core.repositories.grab_history_repository import GrabHistoryRepository

        WantedRepository(db)
        GrabHistoryRepository(db)
        conn = db.get_connection()
        conn.execute('''
            CREATE TABLE IF NOT EXISTS import_log (
                source_path TEXT NOT NULL,
                size INTEGER NOT NULL,
                series_id INTEGER,
                destination TEXT,
                result TEXT,
                imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (source_path, size)
            )
        ''')
        conn.commit()

    # --- archivos terminados ----------------------------------------------------
    def finished_files(self) -> list[Path]:
        files = []
        cutoff = self.now() - self.settings.min_age_minutes * 60
        watch = os.path.normcase(os.path.abspath(Settings.get_torrent_watch_dir())) if Settings.get_torrent_watch_dir() else None
        for source in self.settings.source_dirs:
            base = Path(source)
            if not base.is_dir():
                continue
            for path in base.rglob("*"):
                try:
                    if not path.is_file() or path.suffix.lower() not in VIDEO_EXTENSIONS:
                        continue
                    if path.name.lower().endswith(PARTIAL_SUFFIXES) or "sample" in path.stem.lower():
                        continue
                    if watch and os.path.normcase(str(path)).startswith(watch):
                        continue
                    if path.stat().st_mtime > cutoff:
                        continue  # aún descargándose
                except OSError:
                    continue
                files.append(path)
        return files

    def _already_imported(self, path: Path) -> bool:
        return self.db.get_connection().execute(
            "SELECT 1 FROM import_log WHERE source_path = ? AND size = ?", (str(path), path.stat().st_size)
        ).fetchone() is not None

    def _log_import(self, path: Path, series_id, destination, result, size: int | None = None):
        conn = self.db.get_connection()
        conn.execute(
            "INSERT OR REPLACE INTO import_log (source_path, size, series_id, destination, result) VALUES (?, ?, ?, ?, ?)",
            (str(path), size if size is not None else path.stat().st_size, series_id, destination, result),
        )
        conn.commit()

    # --- identificar qué es cada archivo ------------------------------------------
    def match(self, path: Path) -> ImportMatch | None:
        parsed = self.parser.parse_episode(path.name)
        marker = _EPISODE_MARK.search(path.stem)
        if parsed and marker:
            # El título es lo que va antes de "S04E07" / "4x07".
            match = self._match_episode(_clean_name(path.stem[: marker.start()]), parsed["season"], parsed["episode"])
            if match:
                return match
        return self._match_movie(path)

    def _best_series(self, title: str, rows) -> dict | None:
        wanted = _comparable(title)
        best, best_score = None, 0.0
        for row in rows:
            for name in (row["tmdb_name"], row["name"]):
                score = _title_score(_comparable(name or ""), wanted) if name else 0
                if score > best_score:
                    best, best_score = dict(row), score
        return best if best_score >= MATCH_THRESHOLD else None

    def _match_episode(self, title: str, season: int, episode: int) -> ImportMatch | None:
        categories = Settings.get_episodic_categories()
        rows = self.db.get_connection().execute(
            f"""SELECT id, name, tmdb_name, year, content_category, folder_path, COALESCE(placeholder, 0) AS placeholder
                FROM series WHERE COALESCE(monitored, 1) = 1 AND content_category IN ({','.join('?' for _ in categories)})""",
            categories,
        ).fetchall()
        series = self._best_series(title, rows)
        if series is None:
            return None
        conn = self.db.get_connection()
        missing = conn.execute(
            "SELECT 1 FROM missing_episodes WHERE series_id = ? AND season = ? AND episode = ? AND status = 'missing'",
            (series["id"], season, episode)).fetchone()
        if missing:
            return ImportMatch("episode", series, season, episode)
        local = conn.execute(
            "SELECT file_path FROM local_episodes WHERE series_id = ? AND season = ? AND episode = ?",
            (series["id"], season, episode)).fetchone()
        grabbed = conn.execute(
            "SELECT 1 FROM grab_history WHERE series_id = ? AND season = ? AND episode = ? AND created_at >= datetime('now', ?)",
            (series["id"], season, episode, f"-{GRAB_WINDOW_DAYS} days")).fetchone()
        if local and grabbed:
            return ImportMatch("episode", series, season, episode, upgrade=True, old_path=local[0])
        return None

    def _match_movie(self, path: Path) -> ImportMatch | None:
        stem = _clean_name(path.stem)
        year_match = _YEAR.search(stem)
        year = int(year_match.group(1)) if year_match else None
        cut = _CUT_TOKENS.search(stem)
        title = stem[: min(year_match.start() if year_match else len(stem), cut.start() if cut else len(stem))]
        categories = Settings.get_single_file_categories()
        rows = self.db.get_connection().execute(
            f"""SELECT s.id, s.name, s.tmdb_name, s.year, s.content_category, s.folder_path,
                       COALESCE(s.placeholder, 0) AS placeholder
                FROM series s
                WHERE COALESCE(s.monitored, 1) = 1 AND s.content_category IN ({','.join('?' for _ in categories)})
                  AND (COALESCE(s.placeholder, 0) = 1 OR EXISTS (
                       SELECT 1 FROM grab_history g WHERE g.series_id = s.id AND g.season IS NULL
                       AND g.created_at >= datetime('now', ?)))""",
            (*categories, f"-{GRAB_WINDOW_DAYS} days"),
        ).fetchall()
        rows = [r for r in rows if not (year and r["year"] and abs(int(r["year"]) - year) > 1)]
        series = self._best_series(title, rows)
        if series is None:
            return None
        if series["placeholder"]:
            return ImportMatch("movie", series)
        return ImportMatch("movie", series, upgrade=True, old_path=series["folder_path"])

    # --- destino ------------------------------------------------------------------
    def destination(self, match: ImportMatch, path: Path) -> Path:
        series = match.series
        name = series.get("tmdb_name") or series["name"]
        ext = path.suffix.lower()
        if match.kind == "movie":
            if match.upgrade and match.old_path and Path(match.old_path).suffix:
                folder = Path(match.old_path).parent
            else:
                roots = Settings.get_paths_for_category(series["content_category"])
                folder = Path(roots[0]) if roots else Path(series["folder_path"]).parent
            year = f" ({series['year']})" if series.get("year") else ""
            return folder / f"{_safe(name)}{year}{ext}"

        series_dir = Path(series["folder_path"])
        if match.upgrade and match.old_path:
            folder = Path(match.old_path).parent
        elif self._uses_season_folders(series):
            folder = series_dir / f"Season {match.season:02d}"
        else:
            folder = series_dir
        title = self._episode_title(series["id"], match.season, match.episode)
        return folder / f"{_safe(name)} S{match.season:02d}E{match.episode:02d} - {_safe(title)}{ext}"

    def _uses_season_folders(self, series: dict) -> bool:
        rows = self.db.get_connection().execute(
            "SELECT file_path FROM local_episodes WHERE series_id = ? LIMIT 20", (series["id"],)).fetchall()
        if not rows:
            return True  # serie nueva: como la mayoría de la biblioteca
        base = len(Path(series["folder_path"]).parts)
        return sum(len(Path(r[0]).parts) - base > 1 for r in rows) * 2 >= len(rows)

    def _episode_title(self, series_id: int, season: int, episode: int) -> str:
        conn = self.db.get_connection()
        for query in ("SELECT name FROM missing_episodes WHERE series_id = ? AND season = ? AND episode = ?",
                      "SELECT title FROM agenda_episodes WHERE series_id = ? AND season = ? AND episode = ?"):
            try:
                row = conn.execute(query, (series_id, season, episode)).fetchone()
            except Exception:
                row = None
            if row and row[0]:
                return row[0]
        return f"Episodio {episode}"

    # --- importar -------------------------------------------------------------------
    def run(self) -> dict:
        summary = {"imported": 0, "upgraded": 0, "skipped": 0}
        if not self.settings.enabled:
            return summary
        for path in self.finished_files():
            try:
                if self._already_imported(path):
                    continue
                match = self.match(path)
                if match is None:
                    continue  # no es algo que la app haya pedido: no se toca
                result = self._import(path, match)
                summary[result] = summary.get(result, 0) + 1
            except Exception as e:
                logger.error(f"Importación: error con {path}: {e}", exc_info=True)
                self.log(f"   ❌ No se pudo importar {path.name}: {e}")
        return summary

    def _import(self, path: Path, match: ImportMatch) -> str:
        series_id = match.series["id"]
        if match.upgrade:
            new = detect_resolution_from_file(str(path))
            old = detect_resolution_from_file(match.old_path) if match.old_path else None
            if not new or not old or not self.profile.is_upgrade(
                    quality_from_local_episode({**old, "hdr": old.get("hdr") or "sdr"}),
                    quality_from_local_episode({**new, "hdr": new.get("hdr") or "sdr"})):
                self._log_import(path, series_id, None, "no_mejora")
                self.log(f"   ⏭️ {path.name}: no mejora la calidad de lo que ya tienes")
                return "skipped"
            if not self.settings.recycle_dir:
                self.log(f"   ⏭️ {path.name}: es una mejora, pero falta la carpeta de reemplazados en Ajustes")
                return "skipped"

        destination = self.destination(match, path)
        size = path.stat().st_size  # antes de mover: luego el original ya no existe
        recycled = None
        if match.upgrade and match.old_path and Path(match.old_path).exists():
            # El nuevo suele llamarse igual que el viejo: primero se aparta el
            # viejo (nunca se borra) y, si algo falla, se devuelve a su sitio.
            recycle = Path(self.settings.recycle_dir)
            recycle.mkdir(parents=True, exist_ok=True)
            recycled = recycle / Path(match.old_path).name
            shutil.move(match.old_path, str(recycled))
        if destination.exists():
            if recycled:
                shutil.move(str(recycled), match.old_path)
            self._log_import(path, series_id, str(destination), "ya_existe", size)
            return "skipped"
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(destination.name + ".importing")
        try:
            # Se copia con nombre temporal y se renombra al final: el escáner
            # de la biblioteca nunca ve un archivo a medias.
            shutil.copy2(path, temporary)
            os.replace(temporary, destination)
        except Exception:
            temporary.unlink(missing_ok=True)
            if recycled:
                shutil.move(str(recycled), match.old_path)
            raise
        if self.settings.mode == "move":
            path.unlink()
        if match.kind == "movie" and not match.upgrade:
            self._resolve_movie_placeholder(series_id, destination)
        self._log_import(path, series_id, str(destination), "mejora" if match.upgrade else "importado", size)
        label = f"{match.season}x{match.episode:02d}" if match.kind == "episode" else "película"
        self.log(f"   📥 {match.series.get('tmdb_name') or match.series['name']} {label} → {destination}")
        return "upgraded" if match.upgrade else "imported"

    def _resolve_movie_placeholder(self, series_id: int, destination: Path):
        """La película "en camino" pasa a ser la ficha real del archivo importado."""
        conn = self.db.get_connection()
        conn.execute("UPDATE series SET folder_path = ?, name = ?, placeholder = 0, status = 'pending' WHERE id = ?",
                     (self.db.normalize_path(str(destination)), destination.stem, series_id))
        conn.execute("UPDATE wanted_items SET status = 'in_library', reason = 'Importada a la biblioteca' "
                     "WHERE series_id = ?", (series_id,))
        conn.commit()
