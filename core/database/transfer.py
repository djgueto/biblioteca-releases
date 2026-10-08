"""Exportar e importar la base de datos entera (pasar la biblioteca de una
instalación a otra: escritorio <-> NAS/Docker).

- Exportar: copia coherente con la API de copias de SQLite (incluye lo que
  aún esté en el WAL), sin parar la app.
- Importar: el archivo se valida y se deja preparado junto a la BD
  (`<bd>.import-pending`); se aplica en el siguiente arranque, antes de abrir
  la BD (con la BD abierta Windows no deja sustituirla). Antes se guarda una
  copia de la BD actual en las copias de seguridad.

Las rutas de las bibliotecas no hace falta tocarlas: la BD lleva la foto de
sus rutas (core/library/roots.py) y al arrancar se reubican a las que tenga
configuradas esta instalación. La configuración (.env: claves, rutas,
contraseña) no viaja: es de cada instalación.
"""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path
from urllib.parse import quote

logger = logging.getLogger(__name__)

REQUIRED_TABLES = {"series", "local_episodes"}
SQLITE_HEADER = b"SQLite format 3\x00"


def pending_path(db_path: str | os.PathLike) -> Path:
    return Path(f"{db_path}.import-pending")


def export_copy(db_path: str | os.PathLike, dest_dir: str | os.PathLike | None = None) -> Path:
    """Copia coherente de la BD en un archivo temporal (lo borra quien la sirve)."""
    fd, name = tempfile.mkstemp(prefix="biblioteca-export-", suffix=".db", dir=dest_dir)
    os.close(fd)
    src = sqlite3.connect(str(db_path), timeout=60.0)
    try:
        src.execute("PRAGMA busy_timeout = 60000")
        dst = sqlite3.connect(name)
        try:
            src.backup(dst)
            dst.commit()
        finally:
            dst.close()
    finally:
        src.close()
    return Path(name)


def inspect(path: str | os.PathLike) -> dict:
    """Comprueba que el archivo es una BD de la app; devuelve un resumen.
    Lanza ValueError con el motivo si no sirve."""
    path = Path(path)
    with open(path, "rb") as f:
        if f.read(len(SQLITE_HEADER)) != SQLITE_HEADER:
            raise ValueError("El archivo no es una base de datos de la app")
    try:
        # immutable: ni bloqueos ni archivos -wal/-shm junto al subido (un
        # -wal suelto con el mismo nombre se mezclaría con otra importación).
        conn = sqlite3.connect(f"file:{quote(path.resolve().as_posix(), safe='/:')}?mode=ro&immutable=1", uri=True)
    except sqlite3.Error as e:
        raise ValueError(f"No se puede abrir la base de datos: {e}") from e
    try:
        if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("La base de datos está dañada")
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        missing = REQUIRED_TABLES - tables
        if missing:
            raise ValueError("No es una base de datos de la app (faltan tablas: " + ", ".join(sorted(missing)) + ")")
        summary = {
            "series": conn.execute("SELECT COUNT(*) FROM series").fetchone()[0],
            "episodes": conn.execute("SELECT COUNT(*) FROM local_episodes").fetchone()[0],
            "roots": [],
        }
        if "library_roots" in tables:
            summary["roots"] = [r[0] for r in conn.execute("SELECT path FROM library_roots ORDER BY category, position")]
        return summary
    except sqlite3.Error as e:
        raise ValueError(f"No se puede leer la base de datos: {e}") from e
    finally:
        conn.close()


def stage_import(upload: str | os.PathLike, db_path: str | os.PathLike) -> dict:
    """Valida el archivo subido y lo deja listo para el próximo arranque."""
    summary = inspect(upload)
    target = pending_path(db_path)
    _remove_sidecars(target)
    shutil.move(str(upload), target)
    return summary


def apply_pending_import(db_path: str | os.PathLike) -> bool:
    """Arranque: si hay una importación preparada, sustituye la BD (antes de
    abrirla). Devuelve True si se aplicó."""
    pending = pending_path(db_path)
    if not pending.exists():
        return False
    db_path = Path(db_path)
    try:
        inspect(pending)
    except ValueError as e:
        logger.error(f"Importación de base de datos descartada: {e}")
        pending.unlink(missing_ok=True)
        return False
    if db_path.exists():
        from utils.db_backup import backup_sqlite_db

        backup = backup_sqlite_db(str(db_path))
        logger.info(f"Copia de la base de datos antes de importar: {backup}")
    for suffix in ("-wal", "-shm"):
        Path(f"{db_path}{suffix}").unlink(missing_ok=True)
    _remove_sidecars(pending)
    os.replace(pending, db_path)
    _forget_other_install(db_path)
    logger.info(f"Base de datos importada: {db_path}")
    return True


def _remove_sidecars(path: Path):
    for suffix in ("-wal", "-shm", "-journal"):
        Path(f"{path}{suffix}").unlink(missing_ok=True)


def _forget_other_install(db_path: Path):
    """El bloqueo de ciclo de la otra instalación no vale aquí."""
    conn = sqlite3.connect(str(db_path))
    try:
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'cycle_lock'").fetchone():
            conn.execute("DELETE FROM cycle_lock")
            conn.commit()
    finally:
        conn.close()
