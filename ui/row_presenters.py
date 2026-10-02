from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QTableWidgetItem

from config.settings import Settings

# Singletons de color — se crean una sola vez al importar el módulo
_COLOR_GREEN = QColor("green")
_COLOR_ORANGE = QColor("orange")
_COLOR_RED = QColor("red")
_COLOR_GRAY = QColor("gray")


def _build_confidence_item(confidence_value):
    confidence = confidence_value or 0
    item = QTableWidgetItem(f"{confidence:.2%}")
    if confidence >= 0.85:
        item.setForeground(_COLOR_GREEN)
    elif confidence >= 0.70:
        item.setForeground(_COLOR_ORANGE)
    else:
        item.setForeground(_COLOR_RED)
    return item


def _build_cover_placeholder_item(text="..."):
    item = QTableWidgetItem(text)
    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
    return item


def build_series_row_items(series_data: dict):
    name_item = QTableWidgetItem(series_data.get("name", ""))
    name_item.series_id = series_data.get("id")

    category_item = QTableWidgetItem(
        Settings.get_category_label(series_data.get("content_category", "series"))
    )
    year_item = QTableWidgetItem(str(series_data.get("year", "")))
    tmdb_item = QTableWidgetItem(str(series_data.get("tmdb_id", "")))
    confidence_item = _build_confidence_item(series_data.get("confidence_score", 0))
    missing_item = QTableWidgetItem(str(series_data.get("missing_count", 0)))

    url = series_data.get("hispashare_url", "")
    url_item = QTableWidgetItem(url)
    if series_data.get("search_disabled", 0) == 1 and not url:
        url_item.setText("🚫 Búsqueda deshabilitada")
        url_item.setForeground(_COLOR_GRAY)

    status_item = QTableWidgetItem(series_data.get("status", "Pendiente"))
    if series_data.get("search_disabled", 0) == 1:
        status_item.setForeground(_COLOR_GRAY)
    if series_data.get("monitored", 1) == 0:
        status_item.setText("No monitorizada")
        status_item.setForeground(_COLOR_GRAY)
        name_item.setForeground(_COLOR_GRAY)

    return [
        name_item,
        category_item,
        year_item,
        tmdb_item,
        confidence_item,
        missing_item,
        url_item,
        None,  # col 7: carátula — lo gestiona _set_cover_placeholder, no se usa aquí
        status_item,
    ]


def build_library_row_items(item_data: dict):
    name_item = QTableWidgetItem(item_data.get("name", ""))
    name_item.series_id = item_data.get("id")

    category_item = QTableWidgetItem(
        Settings.get_category_label(item_data.get("content_category", ""))
    )
    year_item = QTableWidgetItem(str(item_data.get("year") or ""))
    tmdb_item = QTableWidgetItem(str(item_data.get("tmdb_id") or ""))
    confidence_item = _build_confidence_item(item_data.get("confidence_score", 0))
    videos_item = QTableWidgetItem(str(item_data.get("video_count", 0)))

    url = item_data.get("hispashare_url", "")
    url_item = QTableWidgetItem(url)
    if item_data.get("search_disabled", 0) == 1 and not url:
        url_item.setText("🚫 Búsqueda deshabilitada")
        url_item.setForeground(_COLOR_GRAY)

    status_text = "Local"
    if item_data.get("search_disabled", 0) == 1 and not item_data.get("hispashare_url"):
        status_text = "Sin match URL"
    elif not item_data.get("tmdb_id"):
        status_text = "Pendiente TMDB"
    elif item_data.get("hispashare_url"):
        status_text = "Identificado"

    if item_data.get("monitored", 1) == 0:
        status_text = "No monitorizada"
        name_item.setForeground(_COLOR_GRAY)

    return [
        name_item,
        category_item,
        year_item,
        tmdb_item,
        confidence_item,
        videos_item,
        url_item,
        None,  # col 7: carátula — lo gestiona _set_cover_placeholder, no se usa aquí
        QTableWidgetItem(status_text),
    ]
