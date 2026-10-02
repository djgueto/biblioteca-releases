# -*- coding: utf-8 -*-
"""
Escaneo completo de resolución con pymediainfo para todos los episodios
que no tienen resolución identificada en la DB.
Ejecutar directamente: python scripts/scan_all_resolutions.py
"""
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.database.db_manager import DatabaseManager
from core.library.parser import detect_resolution_from_file

BATCH_SIZE = 500

def count_pending(db):
    conn = db.get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM local_episodes WHERE resolution_height IS NULL AND COALESCE(quality_rank, 0) >= 0"
    )
    return cur.fetchone()[0]

def main():
    db = DatabaseManager()
    db.initialize_database()

    total_pending = count_pending(db)
    if total_pending == 0:
        print("Ya no hay archivos pendientes de escaneo.")
        db.close()
        return

    print(f"Archivos pendientes: {total_pending}")
    print(f"Procesando en lotes de {BATCH_SIZE}...\n")

    processed = 0
    resolved_total = 0
    failed_total = 0
    t_start = time.time()

    while True:
        episodes = db.get_episodes_missing_resolution(limit=BATCH_SIZE)
        if not episodes:
            break

        resolved = []
        failed_ids = []

        for ep in episodes:
            result = detect_resolution_from_file(ep['file_path'])
            if result:
                resolved.append({'id': ep['id'], **result})
            else:
                failed_ids.append(ep['id'])

        if resolved:
            db.batch_update_episode_resolutions(resolved)
        if failed_ids:
            db.mark_episode_resolution_unreadable(failed_ids)

        processed += len(episodes)
        resolved_total += len(resolved)
        failed_total += len(failed_ids)

        elapsed = time.time() - t_start
        rate = processed / elapsed if elapsed > 0 else 0
        remaining = total_pending - processed
        eta_s = int(remaining / rate) if rate > 0 else 0
        eta_min = eta_s // 60
        eta_sec = eta_s % 60

        pct = processed * 100 // total_pending
        print(
            f"\r[{pct:3d}%] {processed}/{total_pending}  "
            f"ok={resolved_total}  no-leibles={failed_total}  "
            f"velocidad={rate:.1f}/s  ETA={eta_min}m{eta_sec:02d}s",
            end='', flush=True
        )

    elapsed = time.time() - t_start
    print(f"\n\nFinalizado en {int(elapsed//60)}m{int(elapsed%60):02d}s")
    print(f"  Resueltos:      {resolved_total}")
    print(f"  No leibles:     {failed_total}")
    print(f"  Total procesado:{processed}")

    # Mostrar distribucion de calidades en la DB
    conn = db.get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT resolution_height, COUNT(*) as cnt
        FROM local_episodes
        WHERE resolution_height IS NOT NULL
        GROUP BY resolution_height
        ORDER BY resolution_height DESC
        """
    )
    rows = cur.fetchall()
    if rows:
        print("\nDistribucion de resoluciones en la biblioteca:")
        for r in rows:
            print(f"  {r['resolution_height']}p : {r['cnt']} archivos")

    db.close()

if __name__ == '__main__':
    main()
