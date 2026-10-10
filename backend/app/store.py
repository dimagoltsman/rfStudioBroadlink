"""Tiny JSON document store: one file, a handful of collections, a lock."""
from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path

COLLECTIONS = ("devices", "codes", "remotes")
LEGACY_NAMES = {"remotes": "curtains"}  # renamed collections; old files load transparently and save under the new name


def new_id() -> str:
    return uuid.uuid4().hex[:10]


class Store:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()
        self._data: dict[str, list[dict]] = {c: [] for c in COLLECTIONS}
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            loaded = json.loads(self.path.read_text() or "{}")
            for c in COLLECTIONS:
                self._data[c] = list(loaded.get(c) or loaded.get(LEGACY_NAMES.get(c, ""), []))

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=2))
        tmp.replace(self.path)

    def list(self, collection: str) -> list[dict]:
        with self._lock:
            return [dict(item) for item in self._data[collection]]

    def get(self, collection: str, item_id: str) -> dict | None:
        with self._lock:
            return next((dict(i) for i in self._data[collection] if i["id"] == item_id), None)

    def upsert(self, collection: str, item: dict) -> dict:
        with self._lock:
            item.setdefault("id", new_id())
            items = self._data[collection]
            for idx, existing in enumerate(items):
                if existing["id"] == item["id"]:
                    items[idx] = item
                    break
            else:
                items.append(item)
            self._save()
            return dict(item)

    def delete(self, collection: str, item_id: str) -> bool:
        with self._lock:
            before = len(self._data[collection])
            self._data[collection] = [i for i in self._data[collection] if i["id"] != item_id]
            changed = len(self._data[collection]) != before
            if changed:
                self._save()
            return changed
