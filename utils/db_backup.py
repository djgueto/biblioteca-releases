import os
import sqlite3
from datetime import datetime
from pathlib import Path


def backup_sqlite_db(db_path: str, backups_dir: str = "backups", keep_last: int = 20) -> str | None:
    src = Path(db_path)
    if not src.exists():
        return None

    backups_path = Path(backups_dir)
    if not backups_path.is_absolute():
        backups_path = (Path.cwd() / backups_path).resolve()
    backups_path.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = backups_path / f"{src.stem}_{timestamp}.db"

    src_conn = sqlite3.connect(str(src))
    try:
        dest_conn = sqlite3.connect(str(dest))
        try:
            src_conn.backup(dest_conn)
            dest_conn.commit()
        finally:
            dest_conn.close()
    finally:
        src_conn.close()

    backups = sorted(backups_path.glob(f"{src.stem}_*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in backups[keep_last:]:
        try:
            os.remove(old)
        except OSError:
            pass

    return str(dest)
