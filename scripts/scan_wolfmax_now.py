# -*- coding: utf-8 -*-
"""
Ejecuta scan_wolfmax_categories() de forma standalone para poblar
las URLs wolfmax en source_matches sin esperar al ciclo del scheduler.
Uso: python scripts/scan_wolfmax_now.py [max_paginas]
"""
import sys
import os
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.WARNING)

from core.database.db_manager import DatabaseManager
from core.wolfmax.category_scanner import WolfmaxCategoryScanner, _normalize, _title_score
from config.settings import Settings

max_pages = int(sys.argv[1]) if len(sys.argv) > 1 else 3

db = DatabaseManager()
db.initialize_database()

all_series = db.get_all_series()
db_index = [
    {
        'id': s['id'],
        'name': s.get('name', ''),
        'category': s.get('content_category', ''),
        'norm': _normalize(s.get('tmdb_name') or s.get('name', '')),
    }
    for s in all_series
]

print(f"Escaneando wolfmax4k.com ({max_pages} pagina(s) por categoria)...")
scanner = WolfmaxCategoryScanner(max_pages=max_pages)
items = scanner.scan_categories()
print(f"{len(items)} items encontrados\n")

threshold = getattr(Settings, 'WOLFMAX_MATCH_THRESHOLD', 0.55)
matched = 0
updated = 0
skipped = 0

for item in items:
    item_norm = _normalize(item['title'])
    cat_candidates = [s for s in db_index if s['category'] == item['app_category'] and s['norm']]
    best, best_score = None, 0.0
    for s in cat_candidates:
        score = _title_score(item_norm, s['norm'])
        if score > best_score:
            best_score = score
            best = s

    if not best or best_score < threshold:
        skipped += 1
        continue

    existing = db.get_wolfmax_source_for_series(best['id'])
    existing_q = int((existing or {}).get('match_score', 0) * 2160)
    new_q = item.get('quality_height') or 0

    if not existing or new_q > existing_q:
        db.save_wolfmax_source(best['id'], item['wolfmax_url'], item.get('quality_height'), item['title'])
        action = 'NUEVA' if not existing else f'ACTUALIZADA ({existing_q}p->{new_q}p)'
        q_label = f"{new_q}p" if new_q else "?"
        print(f"  [{q_label}] {best['name'][:50]} <- {item['title'][:40]} ({action})")
        matched += 1
    else:
        updated += 1  # ya tiene una URL igual o mejor

print(f"\nResumen:")
print(f"  URLs guardadas/actualizadas: {matched}")
print(f"  Ya tenian URL igual o mejor: {updated}")
print(f"  Sin match en biblioteca:     {skipped}")

# Cuantos candidatos a upgrade hay ahora
all_with_wolfmax = db.get_series_with_wolfmax_url()
upgrade_candidates = [
    s for s in all_with_wolfmax
    if s.get('max_local_resolution') is not None
    and int(round((s.get('wolfmax_score') or 0) * 2160)) > s['max_local_resolution']
]
print(f"\nCandidatos a upgrade (wolfmax mejor que local): {len(upgrade_candidates)}")
if upgrade_candidates:
    print("  Ejemplos:")
    for c in upgrade_candidates[:10]:
        wq = int(round((c.get('wolfmax_score') or 0) * 2160))
        print(f"    {c['name'][:45]} {c['max_local_resolution']}p -> {wq}p  {c['wolfmax_url']}")

db.close()
