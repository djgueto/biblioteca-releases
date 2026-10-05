import logging
import re
from pathlib import Path

from config.settings import Settings
from core.library.unreadable_names import is_unreadable_name


# Un episodio suelto (Serie S02E01.mp4, Serie 2x01.mkv). Más estricto que el
# parser de series: en una biblioteca de películas solo se aparta lo que es
# claramente un episodio.
LOOSE_EPISODE = re.compile(r"\b[Ss]\d{1,2}[Ee]\d{1,3}\b|\b\d{1,2}x\d{2,3}\b")


class CategoryLibraryScanner:
    """Escanea bibliotecas por categorías episódicas o de archivo único."""

    VIDEO_EXTENSIONS = {
        '.mkv', '.mp4', '.avi', '.mov', '.wmv',
        '.flv', '.m4v', '.mpg', '.mpeg', '.webm'
    }
    COVER_FILENAMES = (
        'poster.jpg', 'poster.png',
        'folder.jpg', 'folder.png',
        'cover.jpg', 'cover.png',
    )

    def __init__(self, category, library_paths=None, skip_paths=None):
        self.category = category
        self.skip_paths = set(skip_paths or ())
        self.library_paths = library_paths or Settings.get_paths_for_category(category)
        self.paths = [Path(path) for path in self.library_paths if str(path).strip()]
        self.content_mode = Settings.get_category_content_mode(category)
        self.logger = logging.getLogger(__name__)
        # Episodios sueltos encontrados en una biblioteca de archivo único: no
        # son películas (buscarlos en TMDB como tales nunca da resultado).
        self.loose_episodes: list[str] = []

    def scan(self):
        items = []
        seen_paths = set()

        for base_path in self.paths:
            if not base_path.exists():
                self.logger.warning(f"Ruta no encontrada para {self.category}: {base_path}")
                continue

            for item in self._scan_base_path(base_path):
                normalized_path = str(Path(item['path']).resolve()).lower()
                if normalized_path in seen_paths:
                    continue
                seen_paths.add(normalized_path)
                item['content_mode'] = self.content_mode
                items.append(item)

        items.sort(key=lambda item: item['name'].lower())
        return items

    def _scan_base_path(self, base_path):
        if self.content_mode == 'episodic':
            return self._scan_episodic_base_path(base_path)
        return self._scan_single_file_base_path(base_path)

    def _scan_episodic_base_path(self, base_path):
        discovered = []

        try:
            entries = list(base_path.iterdir())
        except Exception as exc:
            self.logger.warning(f"No se pudo leer {base_path}: {exc}")
            return discovered

        for entry in entries:
            if entry.name.startswith('.') or is_unreadable_name(entry):
                continue

            if self._is_skipped(entry):
                continue

            if entry.is_dir():
                info = self._build_directory_item(entry)
                if info:
                    discovered.append(info)
                continue

            if entry.is_file() and self.is_video_file(entry):
                discovered.append({
                    'name': entry.stem,
                    'path': str(entry),
                    'kind': 'file',
                    'video_count': 1,
                    'cover_path': self.find_cover_for_file(entry),
                })

        return discovered

    def _scan_single_file_base_path(self, base_path):
        discovered = []

        try:
            entries = list(base_path.iterdir())
        except Exception as exc:
            self.logger.warning(f"No se pudo leer {base_path}: {exc}")
            return discovered

        for entry in entries:
            if entry.name.startswith('.') or is_unreadable_name(entry):
                continue

            if self._is_skipped(entry):
                continue

            if entry.is_dir():
                info = self._build_directory_item(entry)
                if info:
                    discovered.append(info)
                continue

            if entry.is_file() and self.is_video_file(entry):
                if LOOSE_EPISODE.search(entry.stem):
                    self.loose_episodes.append(str(entry))
                    self.logger.warning(f"Episodio suelto en {self.category}, no se trata como película: {entry}")
                    continue
                discovered.append({
                    'name': entry.stem,
                    'path': str(entry),
                    'kind': 'file',
                    'video_count': 1,
                    'item_count': 1,
                    'cover_path': self.find_cover_for_file(entry),
                })

        return discovered

    def _is_skipped(self, entry):
        if not self.skip_paths:
            return False
        try:
            return str(entry.resolve()).lower() in self.skip_paths
        except Exception:
            return False

    def _build_directory_item(self, directory):
        video_count = 0

        try:
            for file_path in directory.rglob('*'):
                if file_path.is_file() and self.is_video_file(file_path):
                    video_count += 1
        except Exception as exc:
            self.logger.warning(f"No se pudo recorrer {directory}: {exc}")
            return None

        if video_count == 0:
            return None

        return {
            'name': directory.name,
            'path': str(directory),
            'kind': 'folder',
            'video_count': video_count,
            'item_count': 1,
            'cover_path': self.find_cover_for_directory(directory),
        }

    def is_video_file(self, file_path):
        return file_path.suffix.lower() in self.VIDEO_EXTENSIONS

    def find_cover_for_directory(self, directory):
        for filename in self.COVER_FILENAMES:
            candidate = directory / filename
            if candidate.is_file():
                return str(candidate)
        return None

    def find_cover_for_file(self, file_path):
        base_name = file_path.stem
        possible_files = [
            file_path.with_suffix('.jpg'),
            file_path.with_suffix('.png'),
            file_path.parent / f"{base_name}-poster.jpg",
            file_path.parent / f"{base_name}-poster.png",
            file_path.parent / f"{base_name}-cover.jpg",
            file_path.parent / f"{base_name}-cover.png",
        ]
        for candidate in possible_files:
            if candidate.is_file():
                return str(candidate)

        for filename in self.COVER_FILENAMES:
            candidate = file_path.parent / filename
            if candidate.is_file():
                return str(candidate)

        return None
