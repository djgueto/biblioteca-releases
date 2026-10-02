"""Corrige series que comparten literalmente el mismo hispashare_url.

Detectado a raíz de "Kung Fu Panda: El caballero del dragón" (temporada 2
completa) descargándose repetidamente aunque ya estaba íntegra en la
biblioteca: su hispashare_url (id=38965) también estaba asignado a otras dos
series distintas ("La Leyenda de Po" y "The Paws of Destiny"), así que cada
vez que el scheduler buscaba episodios faltantes de esas dos, encontraba y
volvía a descargar los episodios de "El caballero del dragón".

Ni el audit estático (audit_hispashare_matches.py) ni el audit en vivo
(audit_hispashare_matches_live.py) detectan este caso: ambos puntúan el
título registrado contra el nombre de UNA serie a la vez, y "Kung Fu Panda:
La Leyenda de Po" puntúa razonablemente bien contra sí misma aunque la URL
en realidad apunte a otro spin-off. Este script agrupa por hispashare_url,
visita la página real UNA vez por grupo, y usa el H1 real para decidir cuál
de las series del grupo es la dueña legítima con el mismo
HispashareSearcher.calculate_result_score() que usa el resto de la app.

Para las perdedoras del grupo, usa SourceRepository.clear_current(), que:
  - vacía series.hispashare_url / hispashare_cover_url
  - añade una fila 'cleared' en source_matches con el motivo

Así vuelven a quedar disponibles para que el flujo normal
(scheduler.search_hispashare) las re-busque desde cero.

Uso:
    python scripts/dedupe_hispashare_matches.py                # dry-run (no escribe nada)
    python scripts/dedupe_hispashare_matches.py --apply         # aplica los cambios
    python scripts/dedupe_hispashare_matches.py --apply --limit 20
    python scripts/dedupe_hispashare_matches.py --report-only   # resume el checkpoint acumulado
"""
import argparse
import json
import re
import sqlite3
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bs4 import BeautifulSoup
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from config.settings import Settings
from core.auth.hispashare_login import HispashareAuth
from core.database.db_manager import DatabaseManager
from core.hispashare.searcher import HispashareSearcher, _STOP_WORDS
from core.repositories.source_repository import SourceRepository

DEFAULT_OUT = Path(__file__).resolve().parent / "hispashare_dedupe.jsonl"

# Umbral mínimo de "recall": qué fracción de las palabras significativas
# (no vacías) del título real fetched debe cubrir el nombre completo del
# candidato. calculate_result_score() por sí solo no basta como única
# señal aquí: su atajo de contención de subcadenas puede dar un score alto
# a un prefijo de franquicia genérico compartido ("Snoopy presenta", "The
# King") aunque el resto del título (la parte que de verdad distingue la
# obra) no tenga nada que ver. Este recall exige que el candidato cubra
# una porción sustancial del contenido real del título, no solo un
# prefijo/fragmento común.
MIN_RECALL = 0.5


def _significant_words(searcher, text):
    norm = searcher.normalize_title_for_matching(text or "")
    return {w for w in norm.split() if w not in _STOP_WORDS}


def recall_against_title(searcher, candidate_text, fetched_title):
    title_words = _significant_words(searcher, fetched_title)
    if not title_words:
        return 0.0
    candidate_words = _significant_words(searcher, candidate_text)
    return len(candidate_words & title_words) / len(title_words)


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


def find_duplicate_groups(conn):
    """url -> [series rows]"""
    cur = conn.cursor()
    cur.execute(
        "SELECT id, name, tmdb_name, tmdb_id, year, content_category, hispashare_url "
        "FROM series WHERE hispashare_url IS NOT NULL AND hispashare_url != ''"
    )
    by_url = defaultdict(list)
    for row in cur.fetchall():
        by_url[row["hispashare_url"]].append(dict(row))
    return {url: rows for url, rows in by_url.items() if len(rows) > 1}


def is_duplicate_library_entry(members) -> bool:
    """True si todos los miembros del grupo comparten el mismo tmdb_id: no es
    un error de emparejamiento con Hispashare, son fichas repetidas de la
    misma obra en la biblioteca (p. ej. 'La lista final' / 'La lista final
    Lobo negro', ambas con tmdb_id 219760). En ese caso la URL le pertenece
    por igual a todas -- no hay nada que corregir automáticamente aquí; el
    usuario debe fusionar/eliminar la ficha duplicada a mano.

    Se usa tmdb_id, NO tmdb_name: dos películas distintas pueden compartir
    un título genérico en texto ('Dogman' 2018 vs 2023, 'Quién es quién'
    2021 vs 2024) con tmdb_id completamente distinto -- comparar solo el
    texto las habría tratado erróneamente como duplicadas de biblioteca en
    vez de auditar cuál de las dos tiene la URL de Hispashare correcta."""
    tmdb_ids = {m.get("tmdb_id") for m in members}
    return len(tmdb_ids) == 1 and next(iter(tmdb_ids)) is not None


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


def score_candidate(searcher, series, fetched_title):
    year_match = re.search(r"\(.*?(\d{4})\)", fetched_title)
    result = {
        "title": fetched_title,
        "year": int(year_match.group(1)) if year_match else None,
        "last_publication": "",
    }
    query = series["tmdb_name"] or series["name"] or ""
    candidates = searcher.build_comparison_titles(query)
    score = searcher.calculate_result_score(result, candidates, series["year"])
    return score, query


def print_report(rows):
    resolved = [r for r in rows if r.get("status") == "resolved"]
    unclear = [r for r in rows if r.get("status") == "unclear"]
    unreachable = [r for r in rows if r.get("status") == "unreachable"]
    duplicates = [r for r in rows if r.get("status") == "duplicate_library_entry"]

    print(f"Grupos revisados (checkpoint acumulado): {len(rows)}")
    print(f"  Resueltos (ganador claro): {len(resolved)}")
    print(f"  Ambiguos (umbral no superado o empate): {len(unclear)}")
    print(f"  Fichas duplicadas de biblioteca (mismo tmdb_name, sin tocar): {len(duplicates)}")
    print(f"  Inalcanzables: {len(unreachable)}")
    print()

    if duplicates:
        print("=== Fichas duplicadas de biblioteca (requieren fusión/borrado manual) ===")
        for r in duplicates:
            print(f"URL: {r['url']}  series: {[c['name'] for c in r['candidates']]}")
        print()

    if resolved:
        print("=== Resueltos ===")
        for r in resolved:
            print(f"URL: {r['url']}")
            print(f"  Título real: {r['fetched_title']!r}")
            print(f"  Ganador: [{r['winner_id']}] {r['winner_name']!r} (score {r['winner_score']:.3f})")
            for loser in r["losers"]:
                print(f"  Perdedor: [{loser['id']}] {loser['name']!r} (score {loser['score']:.3f})"
                      + ("  -> LIMPIADO" if loser.get("applied") else "  -> (dry-run, no aplicado)"))
            print()

    if unclear:
        print("=== Ambiguos (revisar a mano) ===")
        for r in unclear:
            print(f"URL: {r['url']}")
            print(f"  Título real: {r['fetched_title']!r}")
            for c in r["candidates"]:
                print(f"    [{c['id']}] {c['name']!r} (score {c['score']:.3f})")
            print()

    if unreachable:
        print("=== Inalcanzables ===")
        for r in unreachable:
            print(f"URL: {r['url']}  series: {[c['name'] for c in r['candidates']]}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=str(Settings.DB_PATH))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--sleep", type=float, default=1.0)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--threshold-margin", type=float, default=-0.15)
    parser.add_argument("--apply", action="store_true", help="Escribe los cambios (clear_current) en la base de datos. Sin esto, solo simula.")
    parser.add_argument("--report-only", action="store_true")
    parser.add_argument(
        "--apply-from-checkpoint", action="store_true",
        help="Aplica las filas 'resolved' ya presentes en --out sin volver a visitar Hispashare "
        "(usar tras revisar un dry-run previo).",
    )
    args = parser.parse_args()

    out_path = Path(args.out)

    if args.report_only:
        print_report(load_checkpoint(out_path))
        return

    if args.apply_from_checkpoint:
        rows = load_checkpoint(out_path)
        resolved_rows = [r for r in rows if r.get("status") == "resolved"]
        db_manager = DatabaseManager(args.db)
        source_repo = SourceRepository(db_manager)
        applied = 0
        for row in resolved_rows:
            for loser in row["losers"]:
                if loser.get("applied"):
                    continue  # ya se aplicó en un --apply anterior (live)
                source_repo.clear_current(
                    loser["id"],
                    reason=(
                        f"hispashare_url duplicada con serie id={row['winner_id']} "
                        f"({row['winner_name']!r}); título real en Hispashare: {row['fetched_title']!r}"
                    ),
                    provider="hispashare",
                )
                print(f"Limpiado: [{loser['id']}] {loser['name']!r} (URL era de [{row['winner_id']}] {row['winner_name']!r})")
                applied += 1
        print(f"\nAplicados {applied} clear_current() desde el checkpoint {out_path}.")
        return

    conn = sqlite3.connect(args.db, timeout=60.0)
    conn.row_factory = sqlite3.Row
    groups = find_duplicate_groups(conn)

    already_done = {r["url"] for r in load_checkpoint(out_path)}
    pending_urls = [u for u in groups if u not in already_done]
    if args.limit:
        pending_urls = pending_urls[: args.limit]

    print(f"Grupos con hispashare_url duplicado: {len(groups)}")
    print(f"Ya revisados (checkpoint): {len(already_done)}")
    print(f"Pendientes en esta ejecución: {len(pending_urls)}")
    print(f"Modo: {'APLICANDO CAMBIOS' if args.apply else 'dry-run (sin escribir)'}")
    if not pending_urls:
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
    db_manager = DatabaseManager(args.db) if args.apply else None
    source_repo = SourceRepository(db_manager) if args.apply else None

    resolved = unclear = errors = duplicates = 0
    try:
        with out_path.open("a", encoding="utf-8") as out_f:
            for i, url in enumerate(pending_urls, 1):
                members = groups[url]

                if is_duplicate_library_entry(members):
                    row = {
                        "url": url,
                        "status": "duplicate_library_entry",
                        "candidates": [{"id": m["id"], "name": m["name"], "tmdb_name": m["tmdb_name"]} for m in members],
                    }
                    print(
                        f"[{i}/{len(pending_urls)}] url={url}: mismo tmdb_name en todas -- "
                        f"fichas duplicadas de biblioteca ({[m['name'] for m in members]}), no se toca"
                    )
                    out_f.write(json.dumps(row, ensure_ascii=False) + "\n")
                    out_f.flush()
                    duplicates += 1
                    continue

                content_mode = Settings.get_category_content_mode(members[0]["content_category"])
                # Suelo propio, más estricto que el umbral de producción: aquí
                # se está a punto de BORRAR la URL de otra serie, así que
                # conviene un margen de seguridad mayor que el que usa el
                # matching normal (que solo decide si acepta o no un
                # resultado nuevo, sin tocar nada de otra serie).
                threshold = max((0.42 if content_mode == "episodic" else 0.40) + args.threshold_margin, 0.5)

                try:
                    title = fetch_title(auth.driver, url)
                except WebDriverException as e:
                    title = None
                    print(f"[{i}/{len(pending_urls)}] url={url}: error de red/driver: {e}")

                if title is None:
                    row = {"url": url, "status": "unreachable", "candidates": [{"id": m["id"], "name": m["name"]} for m in members]}
                    out_f.write(json.dumps(row, ensure_ascii=False) + "\n")
                    out_f.flush()
                    errors += 1
                    time.sleep(args.sleep)
                    continue

                scored = []
                for m in members:
                    sc, query = score_candidate(searcher, m, title)
                    recall = recall_against_title(searcher, query, title)
                    scored.append({"id": m["id"], "name": m["name"], "score": sc, "recall": recall})
                scored.sort(key=lambda c: c["score"], reverse=True)
                best = scored[0]
                TIE_MARGIN = 0.02
                is_tie = len(scored) > 1 and (best["score"] - scored[1]["score"]) < TIE_MARGIN
                low_recall = best["recall"] < MIN_RECALL

                if best["score"] < threshold or is_tie or low_recall:
                    row = {"url": url, "status": "unclear", "fetched_title": title, "candidates": scored}
                    if best["score"] < threshold:
                        reason = "ningún candidato supera el umbral"
                    elif is_tie:
                        reason = "empate entre los dos mejores candidatos"
                    else:
                        reason = f"recall insuficiente ({best['recall']:.2f}): el ganador solo cubre un fragmento genérico del título real"
                    print(f"[{i}/{len(pending_urls)}] AMBIGUO url={url} titulo={title!r} -- {reason} ({threshold:.2f})")
                    unclear += 1
                else:
                    losers = []
                    for c in scored[1:]:
                        loser_row = {"id": c["id"], "name": c["name"], "score": c["score"]}
                        if args.apply:
                            source_repo.clear_current(
                                c["id"],
                                reason=(
                                    f"hispashare_url duplicada con serie id={best['id']} "
                                    f"({best['name']!r}); título real en Hispashare: {title!r}"
                                ),
                                provider="hispashare",
                            )
                            loser_row["applied"] = True
                        losers.append(loser_row)

                    row = {
                        "url": url,
                        "status": "resolved",
                        "fetched_title": title,
                        "winner_id": best["id"],
                        "winner_name": best["name"],
                        "winner_score": best["score"],
                        "losers": losers,
                    }
                    print(
                        f"[{i}/{len(pending_urls)}] url={url} titulo={title!r} "
                        f"-> gana [{best['id']}] {best['name']!r} ({best['score']:.3f}); "
                        f"limpiadas: {[l['name'] for l in losers]}"
                    )
                    resolved += 1

                out_f.write(json.dumps(row, ensure_ascii=False) + "\n")
                out_f.flush()
                time.sleep(args.sleep)
    finally:
        auth.close()

    print(f"\nCompletado este tramo: {resolved} resueltos, {unclear} ambiguos, {duplicates} fichas duplicadas (sin tocar), {errors} inalcanzables.")
    print(f"Resultados acumulados en: {out_path}")
    if not args.apply:
        print("Esto fue un dry-run: no se escribió nada en la base de datos. Repite con --apply para aplicar los cambios.")


if __name__ == "__main__":
    main()
