import sys
import sqlite3
from urllib.parse import parse_qs, urlparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.settings import Settings


def main() -> None:
    db_path = Path(Settings.DB_PATH)
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        cur.execute(
            "select id, hispashare_url from series "
            "where hispashare_url is not null and length(hispashare_url) != 0 "
            "and (hispashare_cover_url is null or length(hispashare_cover_url) = 0)"
        )
        rows = cur.fetchall()

        updated = 0
        for series_id, url in rows:
            try:
                qs = parse_qs(urlparse(url).query)
                title_id = (qs.get("id") or [None])[0]
                if not title_id or not str(title_id).isdigit():
                    continue
                cover = f"{Settings.HISPASHARE_BASE_URL}/images/titles/{title_id}/cover.jpg"
                cur.execute("update series set hispashare_cover_url=? where id=?", (cover, series_id))
                updated += cur.rowcount
            except Exception:
                continue

        conn.commit()
        print(f"updated={updated} candidates={len(rows)} db={db_path}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
