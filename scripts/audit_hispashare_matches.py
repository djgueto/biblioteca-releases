"""Static audit of stored Hispashare matches using the fixed scoring algorithm.

Recomputes HispashareSearcher.calculate_result_score() against the title text
that was actually matched at assignment time (recovered from decision_logs /
source_matches), and flags series whose match would NOT clear the acceptance
threshold under the fixed algorithm (see core/hispashare/searcher.py).

This only covers series for which we still have the matched title text on
record -- decision_logs entries with a 'matched_title' context field, or a
source_matches row with a non-empty source_title. Series whose hispashare_url
was assigned before that instrumentation existed can't be statically audited;
they would need a live re-check against hispashare.org (fetch the page at the
stored URL and compare its real title).

Usage:
    python scripts/audit_hispashare_matches.py [--db PATH] [--threshold-margin 0.0]
"""
import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import Settings
from core.hispashare.searcher import HispashareSearcher


def load_candidate_titles(conn):
    """series_id -> (matched_title, match_score, source_url, origin)"""
    cur = conn.cursor()
    result = {}

    # Prefer decision_logs: it records the title/score exactly as seen at
    # match time, even in cases where the source_matches row was later
    # overwritten with a stale/generic source_title (see confirm_current()).
    cur.execute(
        """
        SELECT series_id, decision_type, message, context_json, created_at
        FROM decision_logs
        WHERE decision_type IN (
            'source_confirmed', 'source_selected', 'source_replaced',
            'source_selected_scheduler'
        )
        ORDER BY series_id, created_at ASC
        """
    )
    for series_id, decision_type, message, context_json, created_at in cur.fetchall():
        try:
            ctx = json.loads(context_json or "{}")
        except (TypeError, ValueError):
            continue
        matched_title = ctx.get("matched_title")
        if not matched_title:
            continue
        result[series_id] = {
            "matched_title": matched_title,
            "match_score": ctx.get("match_score"),
            "source_url": ctx.get("source_url"),
            "origin": f"decision_logs:{decision_type}@{created_at}",
        }

    # Fill gaps with source_matches.source_title for series not covered above.
    cur.execute(
        """
        SELECT series_id, source_title, match_score, source_url, updated_at
        FROM source_matches
        WHERE provider = 'hispashare'
          AND match_status = 'confirmed'
          AND source_title IS NOT NULL AND source_title != ''
        ORDER BY series_id, updated_at ASC
        """
    )
    for series_id, source_title, match_score, source_url, updated_at in cur.fetchall():
        if series_id in result:
            continue
        result[series_id] = {
            "matched_title": source_title,
            "match_score": match_score,
            "source_url": source_url,
            "origin": f"source_matches@{updated_at}",
        }

    return result


def audit(db_path: str, threshold_margin: float = 0.0):
    conn = sqlite3.connect(db_path, timeout=60.0)
    conn.row_factory = sqlite3.Row
    candidates = load_candidate_titles(conn)

    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, name, tmdb_name, year, content_category, hispashare_url
        FROM series
        WHERE hispashare_url IS NOT NULL AND hispashare_url != ''
        """
    )
    series_rows = {row["id"]: row for row in cur.fetchall()}

    searcher = HispashareSearcher(driver=None)
    flagged = []
    checked = 0

    for series_id, info in candidates.items():
        series = series_rows.get(series_id)
        if not series:
            continue  # match recorded but URL since cleared/series removed
        if series["hispashare_url"] != info.get("source_url") and info.get("source_url"):
            # The recorded match is not the one currently stored on the
            # series (it was superseded or cleared) -- skip, nothing to flag.
            continue

        checked += 1
        content_mode = Settings.get_category_content_mode(series["content_category"])
        threshold = (0.42 if content_mode == "episodic" else 0.40) + threshold_margin

        query = series["tmdb_name"] or series["name"] or ""
        candidates_titles = searcher.build_comparison_titles(query)
        # Recover the year the same way parse_result_row() does in production,
        # since decision_logs only stored the title text, not the parsed
        # fields. 'last_publication' (recency, up to 15% of the score) can't
        # be recovered at all, so recomputed scores here are a conservative
        # lower bound on what the real search would have produced.
        year_match = re.search(r'\(.*?(\d{4})\)', info["matched_title"])
        result = {
            "title": info["matched_title"],
            "year": int(year_match.group(1)) if year_match else None,
            "last_publication": "",
        }
        new_score = searcher.calculate_result_score(result, candidates_titles, series["year"])

        if new_score < threshold:
            flagged.append(
                {
                    "series_id": series_id,
                    "name": series["name"],
                    "tmdb_name": series["tmdb_name"],
                    "matched_title": info["matched_title"],
                    "old_score": info.get("match_score"),
                    "new_score": new_score,
                    "threshold": threshold,
                    "hispashare_url": series["hispashare_url"],
                    "origin": info["origin"],
                }
            )

    flagged.sort(key=lambda r: r["new_score"])
    return checked, flagged, len(series_rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(Settings.DB_PATH))
    parser.add_argument(
        "--threshold-margin",
        type=float,
        default=-0.15,
        help="Margin added to the acceptance threshold. Defaults to -0.15 to "
        "compensate for the recency component (up to 15%% of the score) that "
        "can't be recovered from historical logs, so legitimate matches "
        "that passed partly on recency aren't flagged as false positives. "
        "Use 0.0 for the exact production threshold, or a positive value "
        "to also surface matches that passed but only barely.",
    )
    args = parser.parse_args()

    checked, flagged, total_with_url = audit(args.db, args.threshold_margin)

    print(f"Series con hispashare_url: {total_with_url}")
    print(f"Series auditables (con título registrado en decision_logs/source_matches): {checked}")
    print(f"Series NO auditables estáticamente (sin título registrado): {total_with_url - checked}")
    print(f"Coincidencias sospechosas con el algoritmo corregido: {len(flagged)}\n")

    for row in flagged:
        print(f"[series_id={row['series_id']}] {row['name']!r} (tmdb: {row['tmdb_name']!r})")
        print(f"    URL: {row['hispashare_url']}")
        print(f"    Título emparejado: {row['matched_title']!r}")
        print(
            f"    Score antiguo: {row['old_score']}  ->  Score con fix: "
            f"{row['new_score']:.3f}  (umbral: {row['threshold']:.2f})"
        )
        print(f"    Origen del dato: {row['origin']}")
        print()

    if total_with_url - checked:
        print(
            "Nota: los matches sin título registrado son anteriores a que se "
            "empezara a guardar 'matched_title'/source_title en decision_logs / "
            "source_matches, y no se pueden auditar sin volver a consultar "
            "hispashare.org en vivo."
        )


if __name__ == "__main__":
    main()
