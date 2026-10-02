import argparse
import re
import sqlite3
from pathlib import Path


def parse_log(log_path: Path) -> dict[str, str]:
    pattern = re.compile(r"URL guardada para '(.+?)': (https?://[^\s]+)")
    found: dict[str, str] = {}
    with log_path.open("r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            m = pattern.search(line)
            if not m:
                continue
            name = m.group(1).strip()
            url = m.group(2).strip()
            found[name] = url
    return found


def recover(db_path: Path, log_path: Path) -> tuple[int, int, int]:
    pairs = parse_log(log_path)
    updated_rows = 0
    missing_names = 0
    multi_match_names = 0

    conn = sqlite3.connect(str(db_path), timeout=60.0)
    conn.execute("PRAGMA busy_timeout = 60000")
    try:
        cur = conn.cursor()
        for name, url in pairs.items():
            cur.execute("select count(*) from series where lower(name)=lower(?)", (name,))
            matches = cur.fetchone()[0]
            if matches == 0:
                missing_names += 1
                continue
            if matches > 1:
                multi_match_names += 1

            cur.execute(
                "update series set hispashare_url=?, updated_at=CURRENT_TIMESTAMP "
                "where lower(name)=lower(?) and (hispashare_url is null or length(hispashare_url)=0)",
                (url, name),
            )
            updated_rows += cur.rowcount

        conn.commit()
    finally:
        conn.close()

    return updated_rows, missing_names, multi_match_names


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=str, default=None)
    parser.add_argument("--log", type=str, default=None)
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[1]
    db_path = Path(args.db) if args.db else (project_root / "series_manager.db")
    log_path = Path(args.log) if args.log else (project_root / "series_manager.log")

    if not db_path.exists():
        raise SystemExit(f"DB no encontrada: {db_path}")
    if not log_path.exists():
        raise SystemExit(f"Log no encontrado: {log_path}")

    updated_rows, missing_names, multi_match_names = recover(db_path, log_path)
    print(f"updated_rows={updated_rows} missing_names={missing_names} multi_match_names={multi_match_names}")


if __name__ == "__main__":
    main()
