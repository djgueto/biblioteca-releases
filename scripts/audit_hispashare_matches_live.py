"""Live audit of Hispashare matches: re-fetches the real page title for every
series whose match has no historical title on record (i.e. everything the
static audit in audit_hispashare_matches.py can't check), and rescores it
with the fixed HispashareSearcher algorithm.

Read-only: this script never writes to series_manager.db. It only appends
findings to a JSON-lines checkpoint file, one line per series checked, so the
crawl can be safely interrupted (Ctrl+C, closed terminal, etc.) and resumed
later by re-running the same command -- already-checked series are skipped.

Usage:
    python scripts/audit_hispashare_matches_live.py
    python scripts/audit_hispashare_matches_live.py --limit 200      # do it in chunks
    python scripts/audit_hispashare_matches_live.py --sleep 0.5      # faster, less polite

After a run (or several resumed runs), summarize with:
    python scripts/audit_hispashare_matches_live.py --report-only
"""
import argparse
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bs4 import BeautifulSoup
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from config.settings import Settings
from core.auth.hispashare_login import HispashareAuth
from core.hispashare.searcher import HispashareSearcher, _MIN_TITLE_SIMILARITY as MIN_TITLE_SIMILARITY

DEFAULT_OUT = Path(__file__).resolve().parent / "hispashare_live_audit.jsonl"


def load_checkpoint(out_path: Path):
    rows = []
    if out_path.exists():
        with out_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    return rows


def load_statically_audited_ids(conn) -> set:
    """Series ids already covered by decision_logs/source_matches -- the
    static audit (audit_hispashare_matches.py) already rescored these and
    found 0 issues, so they're skipped here by default."""
    cur = conn.cursor()
    ids = set()
    cur.execute(
        """
        SELECT DISTINCT series_id FROM decision_logs
        WHERE decision_type IN (
            'source_confirmed', 'source_selected', 'source_replaced',
            'source_selected_scheduler'
        )
        AND context_json LIKE '%matched_title%'
        """
    )
    ids.update(r[0] for r in cur.fetchall())
    cur.execute(
        """
        SELECT DISTINCT series_id FROM source_matches
        WHERE provider = 'hispashare' AND match_status = 'confirmed'
          AND source_title IS NOT NULL AND source_title != ''
        """
    )
    ids.update(r[0] for r in cur.fetchall())
    return ids


def fetch_title(driver, url: str, timeout: float = 12) -> str | None:
    driver.get(url)
    try:
        WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((By.TAG_NAME, "h1"))
        )
    except TimeoutException:
        return None
    soup = BeautifulSoup(driver.page_source, "html.parser")
    h1 = soup.find("h1")
    return h1.get_text(strip=True) if h1 else None


def print_report(rows):
    flagged = [r for r in rows if r.get("status") == "flagged"]
    unreachable = [r for r in rows if r.get("status") == "unreachable"]
    ok = [r for r in rows if r.get("status") == "ok"]

    print(f"Total revisadas (checkpoint acumulado): {len(rows)}")
    print(f"  OK: {len(ok)}")
    print(f"  Sospechosas: {len(flagged)}")
    print(f"  Inalcanzables (id borrado / error de red): {len(unreachable)}")
    print()

    if flagged:
        print("=== Coincidencias sospechosas ===")
        for r in sorted(flagged, key=lambda r: r.get("score", 0)):
            print(f"[series_id={r['series_id']}] {r['name']!r} (tmdb: {r['tmdb_name']!r})")
            print(f"    URL: {r['hispashare_url']}")
            print(f"    Título real en Hispashare: {r['fetched_title']!r}")
            print(f"    Score: {r['score']:.3f}  (umbral: {r['threshold']:.2f})")
            if "title_similarity" in r:
                print(f"    Similitud de título: {r['title_similarity']:.2f}  (mínimo: {MIN_TITLE_SIMILARITY:.2f})")
            print()

    if unreachable:
        print("=== Inalcanzables (revisar manualmente: puede que el título se haya borrado de Hispashare) ===")
        for r in unreachable:
            print(f"[series_id={r['series_id']}] {r['name']!r}  URL: {r['hispashare_url']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=str(Settings.DB_PATH))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--sleep", type=float, default=1.0, help="Segundos de espera entre páginas (cortesía con el sitio)")
    parser.add_argument("--limit", type=int, default=None, help="Máximo de series a revisar en esta ejecución")
    parser.add_argument(
        "--include-static-audited", action="store_true",
        help="También re-revisar series ya cubiertas por la auditoría estática (por defecto se saltan, ya dieron 0 sospechosas)",
    )
    parser.add_argument(
        "--threshold-margin", type=float, default=-0.15,
        help="Margen sobre el umbral de aceptación, para compensar que la recencia de publicación no se puede "
        "recuperar desde la página directa de título (por defecto -0.15, igual que el script estático).",
    )
    parser.add_argument("--report-only", action="store_true", help="No navega nada, solo resume el checkpoint acumulado hasta ahora")
    args = parser.parse_args()

    out_path = Path(args.out)

    if args.report_only:
        print_report(load_checkpoint(out_path))
        return

    out_path.parent.mkdir(parents=True, exist_ok=True)
    already_checked = {r["series_id"] for r in load_checkpoint(out_path) if "series_id" in r}

    conn = sqlite3.connect(args.db, timeout=60.0)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        "SELECT id, name, tmdb_name, year, content_category, hispashare_url "
        "FROM series WHERE hispashare_url IS NOT NULL AND hispashare_url != ''"
    )
    all_series = [dict(r) for r in cur.fetchall()]

    skip_ids = set() if args.include_static_audited else load_statically_audited_ids(conn)

    pending = [s for s in all_series if s["id"] not in already_checked and s["id"] not in skip_ids]
    if args.limit:
        pending = pending[: args.limit]

    print(f"Total series con URL: {len(all_series)}")
    print(f"Ya revisadas (checkpoint): {len(already_checked)}")
    print(f"Excluidas (cubiertas por auditoría estática): {len(skip_ids - already_checked)}")
    print(f"Pendientes en esta ejecución: {len(pending)}")
    if not pending:
        print("Nada que hacer. Usa --report-only para ver el resumen acumulado.")
        return

    if not Settings.HISPASHARE_USER or not Settings.HISPASHARE_PASS:
        print("ERROR: HISPASHARE_USER / HISPASHARE_PASS no configurados.")
        sys.exit(1)

    auth = HispashareAuth(Settings.HISPASHARE_USER, Settings.HISPASHARE_PASS, headless=Settings.SELENIUM_HEADLESS)
    if not auth.login() or auth.driver is None:
        print("ERROR: no se pudo iniciar sesión en Hispashare.")
        sys.exit(1)

    searcher = HispashareSearcher(driver=None)

    flagged = 0
    errors = 0
    try:
        with out_path.open("a", encoding="utf-8") as out_f:
            for i, series in enumerate(pending, 1):
                series_id = series["id"]
                url = series["hispashare_url"]
                try:
                    title = fetch_title(auth.driver, url)
                except WebDriverException as e:
                    title = None
                    print(f"[{i}/{len(pending)}] id={series_id}: error de red/driver: {e}")

                row = {
                    "series_id": series_id,
                    "name": series["name"],
                    "tmdb_name": series["tmdb_name"],
                    "hispashare_url": url,
                    "fetched_title": title,
                }

                if title is None:
                    row["status"] = "unreachable"
                    errors += 1
                else:
                    content_mode = Settings.get_category_content_mode(series["content_category"])
                    threshold = (0.42 if content_mode == "episodic" else 0.40) + args.threshold_margin
                    year_match = re.search(r"\(.*?(\d{4})\)", title)
                    result = {
                        "title": title,
                        "year": int(year_match.group(1)) if year_match else None,
                        "last_publication": "",
                    }
                    query = series["tmdb_name"] or series["name"] or ""
                    candidates = searcher.build_comparison_titles(query)
                    score = searcher.calculate_result_score(result, candidates, series["year"])
                    title_similarity = result.get("title_similarity", 0.0)
                    row["score"] = score
                    row["threshold"] = threshold
                    row["title_similarity"] = title_similarity
                    if score < threshold or title_similarity < MIN_TITLE_SIMILARITY:
                        row["status"] = "flagged"
                        flagged += 1
                        print(
                            f"[{i}/{len(pending)}] id={series_id} SOSPECHOSO: "
                            f"{series['name']!r} -> {title!r} (score {score:.3f} / umbral {threshold:.2f}, "
                            f"título {title_similarity:.2f} / mínimo {MIN_TITLE_SIMILARITY:.2f})"
                        )
                    else:
                        row["status"] = "ok"

                out_f.write(json.dumps(row, ensure_ascii=False) + "\n")
                out_f.flush()

                if i % 50 == 0:
                    print(f"... progreso: {i}/{len(pending)} revisadas, {flagged} sospechosas, {errors} inalcanzables")

                time.sleep(args.sleep)
    finally:
        auth.close()

    print(f"\nCompletado este tramo: {len(pending)} revisadas, {flagged} sospechosas, {errors} inalcanzables.")
    print(f"Resultados acumulados en: {out_path}")
    print("Ejecuta con --report-only para ver el resumen completo en cualquier momento.")


if __name__ == "__main__":
    main()
