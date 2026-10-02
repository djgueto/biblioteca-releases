from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


@dataclass
class CollectionStore:
    name: str
    filter_key: str | None = None
    all_items: list[dict] = field(default_factory=list)
    visible_items: list[dict] = field(default_factory=list)
    dirty: bool = True
    # Índices posicionales: id -> índice en la lista (O(1) lookup y update)
    _all_pos: dict = field(default_factory=dict)
    _visible_pos: dict = field(default_factory=dict)

    def _build_index(self, items: list[dict]) -> dict:
        return {item["id"]: i for i, item in enumerate(items) if item.get("id") is not None}

    def set_all_items(self, items: list[dict] | None, *, mark_clean: bool = False) -> None:
        self.all_items = list(items or [])
        self._all_pos = self._build_index(self.all_items)
        if mark_clean:
            self.dirty = False

    def set_visible_items(self, items: list[dict] | None) -> list[dict]:
        self.visible_items = list(items or [])
        self._visible_pos = self._build_index(self.visible_items)
        return self.visible_items

    def filter_by_values(self, values: list[str] | None) -> list[dict]:
        if not values or not self.filter_key:
            return self.set_visible_items(self.all_items)
        return self.set_visible_items(
            [item for item in self.all_items if item.get(self.filter_key) in values]
        )

    def get_item_by_row(self, row: int) -> dict | None:
        if row < 0 or row >= len(self.visible_items):
            return None
        return self.visible_items[row]

    def get_item_by_id(self, item_id) -> dict | None:
        if item_id is None:
            return None
        pos = self._visible_pos.get(item_id)
        if pos is not None:
            return dict(self.visible_items[pos])
        pos = self._all_pos.get(item_id)
        if pos is not None:
            return dict(self.all_items[pos])
        return None

    def mark_dirty(self) -> None:
        self.dirty = True

    def mark_clean(self) -> None:
        self.dirty = False

    def update_item(self, item_id, item_data: dict | None) -> bool:
        if item_id is None or not item_data:
            return False
        updated = False
        pos = self._all_pos.get(item_id)
        if pos is not None:
            merged = dict(self.all_items[pos])
            merged.update(item_data)
            self.all_items[pos] = merged
            updated = True
        pos = self._visible_pos.get(item_id)
        if pos is not None:
            merged = dict(self.visible_items[pos])
            merged.update(item_data)
            self.visible_items[pos] = merged
            updated = True
        return updated

    def replace_item(self, item_id, item_data: dict | None) -> bool:
        if item_id is None or not item_data:
            return False
        replaced = False
        pos = self._all_pos.get(item_id)
        if pos is not None:
            self.all_items[pos] = dict(item_data)
            replaced = True
        pos = self._visible_pos.get(item_id)
        if pos is not None:
            self.visible_items[pos] = dict(item_data)
            replaced = True
        return replaced

    def any_items(self) -> bool:
        return bool(self.all_items)


class ViewState:
    def __init__(self):
        self.series = CollectionStore("series", filter_key="content_category")
        self.libraries = CollectionStore("libraries", filter_key="content_category")
        self.missing = CollectionStore("missing")

    def reset(self) -> None:
        self.series = CollectionStore("series", filter_key="content_category")
        self.libraries = CollectionStore("libraries", filter_key="content_category")
        self.missing = CollectionStore("missing")
