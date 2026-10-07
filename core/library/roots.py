"""Reubicación automática de bibliotecas cuando cambia su ruta.

La BD guarda rutas absolutas (fichas en series.folder_path, episodios en
local_episodes.file_path), y en formatos mezclados para una misma carpeta:
letra de unidad (`Z:\\SERIES\\...`), UNC (`\\\\192.168.1.101\\Multimedia\\SERIES\\...`)
o UNC largo (`\\\\?\\UNC\\192.168.1.101\\...`).

Para que cambiar de sitio una biblioteca (pasar a Docker, cambiar la IP de un
NAS, mover un disco) sea cambiar una línea de configuración, se guarda en la
tabla `library_roots` una foto de cada ruta configurada (por categoría y
posición) con todos sus alias. Al arrancar, `sync()` compara esa foto con la
configuración actual y, si una ruta cambió, reescribe el principio de las
rutas de esa biblioteca en la BD. La foto viaja dentro de la BD, así que una
copia hecha en Windows sabe en Docker cuáles eran sus rutas de origen.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field

from config.settings import Settings

logger = logging.getLogger(__name__)

PATH_COLUMNS = (("series", "folder_path"), ("local_episodes", "file_path"))


def canon(path: str) -> str:
    """Forma comparable de una ruta: separadores '/', minúsculas, sin '/' final."""
    return (path or "").replace("\\", "/").rstrip("/").lower()


@dataclass
class Root:
    category: str
    position: int
    path: str
    aliases: list[str] = field(default_factory=list)

    @property
    def key(self) -> tuple[str, int]:
        return self.category, self.position


@dataclass
class SyncReport:
    status: str  # 'unchanged' | 'initialized' | 'relocated' | 'pending' | 'unknown_previous' | 'error'
    relocations: list[dict] = field(default_factory=list)  # {old, new, series, episodes}
    pending: list[dict] = field(default_factory=list)  # nuevas rutas aún no accesibles
    message: str = ""

    @property
    def changes(self) -> int:
        return sum(r["series"] + r["episodes"] for r in self.relocations)


class LibraryRoots:
    def __init__(self, db_manager):
        self.db = db_manager
        self._ensure_table()

    def _ensure_table(self):
        conn = self.db.get_connection()
        conn.execute('''
            CREATE TABLE IF NOT EXISTS library_roots (
                category TEXT NOT NULL,
                position INTEGER NOT NULL,
                path TEXT NOT NULL,
                aliases_json TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (category, position)
            )
        ''')
        conn.commit()

    # --- rutas ----------------------------------------------------------
    def aliases_for(self, path: str) -> list[str]:
        aliases = {canon(path)}
        try:
            aliases.add(canon(self.db.normalize_path(path)))
        except Exception:
            pass
        for alias in list(aliases):
            if alias.startswith("//?/unc/"):
                aliases.add("//" + alias[len("//?/unc/"):])
            elif alias.startswith("//"):
                aliases.add("//?/unc/" + alias[2:])
        aliases.discard("")
        return sorted(aliases)

    def configured(self) -> list[Root]:
        roots = []
        for category in Settings.get_library_categories():
            for position, path in enumerate(Settings.get_paths_for_category(category)):
                roots.append(Root(category, position, path, self.aliases_for(path)))
        return roots

    def snapshot(self) -> list[Root]:
        rows = self.db.get_connection().execute(
            "SELECT category, position, path, aliases_json FROM library_roots"
        ).fetchall()
        return [Root(r[0], r[1], r[2], json.loads(r[3])) for r in rows]

    def _save(self, roots: list[Root]):
        conn = self.db.get_connection()
        conn.execute("DELETE FROM library_roots")
        conn.executemany(
            "INSERT INTO library_roots (category, position, path, aliases_json) VALUES (?, ?, ?, ?)",
            [(r.category, r.position, r.path, json.dumps(r.aliases)) for r in roots],
        )
        conn.commit()

    # --- sincronización -----------------------------------------------
    def sync(self, *, dry_run: bool = False) -> SyncReport:
        current = self.configured()
        previous = {r.key: r for r in self.snapshot()}

        if not previous:
            from core.repositories.library_repository import LibraryRepository

            if not LibraryRepository(self.db).path_health()["ok"]:
                return SyncReport(
                    "unknown_previous",
                    message="La BD no tiene foto de rutas y sus rutas no coinciden con las configuradas: "
                            "no se puede saber dónde estaban las bibliotecas.",
                )
            if not dry_run:
                self._save(current)
            return SyncReport("initialized")

        # alias antiguo -> ruta nueva, para cada biblioteca cuya ruta cambió
        mapping: dict[str, str] = {}
        pending: list[dict] = []
        for root in current:
            old = previous.get(root.key)
            if old is None or canon(old.path) == canon(root.path) or set(old.aliases) & set(root.aliases):
                continue
            if not os.path.exists(root.path):
                pending.append({"category": root.category, "position": root.position,
                                "old": old.path, "new": root.path})
                continue
            for alias in old.aliases:
                if mapping.get(alias, root.path) != root.path:
                    return SyncReport("error", message=f"La ruta {old.path} se ha cambiado a dos rutas distintas")
                mapping[alias] = root.path

        if not mapping:
            if not dry_run and not pending:
                self._save(current)
            return SyncReport("pending" if pending else "unchanged", pending=pending)

        plan = self._plan(mapping)
        relocations = self._summarize(plan, mapping)
        if dry_run:
            return SyncReport("relocated", relocations=relocations, pending=pending)

        conn = self.db.get_connection()
        try:
            for (table, column), updates in plan.items():
                conn.executemany(f"UPDATE {table} SET {column} = ? WHERE id = ?", updates)
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Reubicación de bibliotecas cancelada: {e}")
            return SyncReport("error", message=str(e))

        # Las bibliotecas pendientes conservan su foto antigua para
        # reubicarse cuando su nueva ruta sea accesible.
        pending_keys = {(p["category"], p["position"]) for p in pending}
        self._save([previous[r.key] if r.key in pending_keys else r for r in current])
        for r in relocations:
            logger.info(f"Biblioteca reubicada: {r['old']} -> {r['new']} ({r['series']} fichas, {r['episodes']} episodios)")
        return SyncReport("relocated", relocations=relocations, pending=pending)

    def _plan(self, mapping: dict[str, str]) -> dict[tuple[str, str], list[tuple[str, int]]]:
        # El alias más largo primero: '/peliculas de animacion' antes que '/peliculas'.
        aliases = sorted(mapping, key=len, reverse=True)
        plan = {}
        conn = self.db.get_connection()
        for table, column in PATH_COLUMNS:
            updates = []
            for row_id, path in conn.execute(f"SELECT id, {column} FROM {table}").fetchall():
                new_path = self._relocate(path, aliases, mapping)
                if new_path is None:
                    continue
                if table == "series":
                    # Mismo formato que guarda insert_or_update_series; si no,
                    # el siguiente escaneo no reconocería la ficha.
                    new_path = self.db.normalize_path(new_path)
                updates.append((new_path, row_id))
            plan[(table, column)] = updates
        return plan

    @staticmethod
    def _relocate(path: str, aliases: list[str], mapping: dict[str, str]) -> str | None:
        c = canon(path)
        for alias in aliases:
            if c == alias or c.startswith(alias + "/"):
                # Se conserva el resto tal cual (mayúsculas incluidas), con los
                # separadores del sistema actual.
                rest = path.replace("\\", "/").rstrip("/")[len(alias):].strip("/")
                parts = [p for p in re.split(r"/", rest) if p]
                return os.path.normpath(os.path.join(mapping[alias], *parts))
        return None

    @staticmethod
    def _summarize(plan, mapping) -> list[dict]:
        by_new: dict[str, dict] = {}
        for new in sorted(set(mapping.values())):
            olds = sorted(a for a, n in mapping.items() if n == new)
            by_new[new] = {"old": olds[0], "new": new, "series": 0, "episodes": 0}
        for (table, _), updates in plan.items():
            key = "series" if table == "series" else "episodes"
            for new_path, _ in updates:
                for new in sorted(by_new, key=len, reverse=True):
                    if canon(new_path).startswith(canon(new)):
                        by_new[new][key] += 1
                        break
        return list(by_new.values())


def sync_library_roots(db_manager) -> SyncReport:
    """Arranque de la app: reubica bibliotecas movidas, con copia previa de la BD."""
    roots = LibraryRoots(db_manager)
    preview = roots.sync(dry_run=True)
    if preview.status == "relocated" and preview.changes:
        from utils.db_backup import backup_sqlite_db

        backup = backup_sqlite_db(db_manager.db_path)
        logger.info(f"Copia de seguridad antes de reubicar bibliotecas: {backup}")
    report = roots.sync()
    for p in report.pending:
        logger.warning(f"Biblioteca '{p['category']}' movida a {p['new']}, pero esa ruta no es accesible: se reubicará cuando lo sea")
    if report.status in ("unknown_previous", "error"):
        logger.error(f"Reubicación de bibliotecas: {report.message}")
    return report
