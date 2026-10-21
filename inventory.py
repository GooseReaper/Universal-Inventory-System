"""Local inventory rules and SQLite persistence. No network or GUI dependencies."""
from __future__ import annotations

import csv
import json
import math
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

CATEGORIES = ("Electronics", "Tools", "Kitchen", "Outdoor", "Books", "Home")
LOCATION_TYPES = ("House", "Room", "Storage", "Drawer", "Shelf", "Container")
ROOT = Path(__file__).resolve().parent
MAX_PHOTO_BYTES = 10 * 1024 * 1024


class InventoryError(ValueError):
    """An input or operation the interface should explain to the user."""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def required_text(value, label, limit=100):
    text = str(value or "").strip()
    if not text:
        raise InventoryError(f"{label} is required.")
    if len(text) > limit:
        raise InventoryError(f"{label} must be {limit} characters or fewer.")
    return text


def whole_number(value, label):
    text = str(value).strip()
    if not re.fullmatch(r"[0-9]{1,6}", text):
        raise InventoryError(f"{label} must be a whole number from 0 to 999,999.")
    return int(text)


class Inventory:
    """Owns every database transaction used by the desktop application."""

    def __init__(self, database_path, sample=True):
        self.path = Path(database_path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, isolation_level=None, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.execute("PRAGMA busy_timeout = 10000")
        self.db.executescript((ROOT / "schema.sql").read_text(encoding="utf-8"))
        initialized = self.db.execute("SELECT value FROM metadata WHERE key='initialized'").fetchone()
        if initialized is None:
            with self.transaction():
                if sample:
                    self._load_samples()
                self.db.execute("INSERT INTO metadata(key, value) VALUES('initialized', '1')")
        result = self.db.execute("PRAGMA foreign_key_check").fetchone()
        if result:
            raise InventoryError("The database has a broken location reference. Restore a backup.")

    def close(self):
        self.db.close()

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    def _log(self, text):
        self.db.execute("INSERT INTO activity(message, created_at) VALUES(?, ?)", (text, now()))
        self.db.execute("DELETE FROM activity WHERE id NOT IN (SELECT id FROM activity ORDER BY id DESC LIMIT 200)")

    def _load_samples(self):
        data = json.loads((ROOT / "sample_data.json").read_text(encoding="utf-8"))
        for location in data["locations"]:
            self.db.execute("INSERT INTO locations(id,name,kind,parent_id) VALUES(?,?,?,?)",
                            (location["id"], location["name"], location["type"], location["parentId"]))
        for item in data["items"]:
            self._insert_item({
                "id": item["id"], "name": item["name"], "category": item["category"],
                "quantity": item["quantity"], "location_id": item["locationId"],
                "value": item["value"], "low_stock": item["lowStock"],
                "notes": item["notes"], "tags": item["tags"], "photo": b"", "photo_name": "",
                "created_at": item["createdAt"], "updated_at": item["updatedAt"]
            })
        self._log("Sample inventory ready: 18 items across 16 locations")

    def locations(self):
        return [dict(row) for row in self.db.execute("SELECT * FROM locations ORDER BY rowid")]

    def get_location(self, location_id):
        row = self.db.execute("SELECT * FROM locations WHERE id=?", (location_id,)).fetchone()
        if row is None:
            raise InventoryError("This location no longer exists.")
        return dict(row)

    def path_names(self, location_id):
        parts, seen = [], set()
        while location_id:
            if location_id in seen:
                raise InventoryError("Locations cannot contain a cycle.")
            seen.add(location_id)
            location = self.get_location(location_id)
            parts.insert(0, location["name"])
            location_id = location["parent_id"]
        return parts

    def path_text(self, location_id):
        return " → ".join(self.path_names(location_id))

    def descendants(self, location_id):
        self.get_location(location_id)
        found = {location_id}
        locations = self.locations()
        changed = True
        while changed:
            changed = False
            for location in locations:
                if location["parent_id"] in found and location["id"] not in found:
                    found.add(location["id"])
                    changed = True
        return found

    def save_location(self, data, location_id=None):
        name = required_text(data.get("name"), "Location name", 60)
        kind = data.get("kind", "Storage")
        if kind not in LOCATION_TYPES:
            raise InventoryError("Choose a valid location type.")
        parent_id = data.get("parent_id") or None
        with self.transaction():
            if location_id:
                self.get_location(location_id)
            if parent_id:
                self.get_location(parent_id)
                if location_id and parent_id in self.descendants(location_id):
                    raise InventoryError("A location cannot be moved into itself or one of its children.")
            for sibling in self.locations():
                if (sibling["id"] != location_id and sibling["parent_id"] == parent_id
                        and sibling["name"].casefold() == name.casefold()):
                    raise InventoryError("That name already exists inside this parent location.")
            key = location_id or f"loc_{uuid.uuid4().hex}"
            if location_id:
                self.db.execute("UPDATE locations SET name=?,kind=?,parent_id=? WHERE id=?",
                                (name, kind, parent_id, key))
            else:
                self.db.execute("INSERT INTO locations(id,name,kind,parent_id) VALUES(?,?,?,?)",
                                (key, name, kind, parent_id))
            self._log(f"{'Updated' if location_id else 'Created'} location: {name}")
        return self.get_location(key)

    def delete_location(self, location_id):
        with self.transaction():
            location = self.get_location(location_id)
            has_items = self.db.execute("SELECT 1 FROM items WHERE location_id=? LIMIT 1", (location_id,)).fetchone()
            has_children = self.db.execute("SELECT 1 FROM locations WHERE parent_id=? LIMIT 1", (location_id,)).fetchone()
            if has_items or has_children:
                raise InventoryError("This location is not empty. Move its items and child locations first.")
            self.db.execute("DELETE FROM locations WHERE id=?", (location_id,))
            self._log(f"Deleted location: {location['name']}")

    def all_tags(self):
        return [row[0] for row in self.db.execute("SELECT DISTINCT tag FROM item_tags ORDER BY tag COLLATE NOCASE")]

    def _item_dict(self, row):
        item = dict(row)
        item["photo"] = bytes(item["photo"] or b"")
        item["tags"] = [r[0] for r in self.db.execute("SELECT tag FROM item_tags WHERE item_id=? ORDER BY rowid", (item["id"],))]
        return item

    def get_item(self, item_id):
        row = self.db.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        if row is None:
            raise InventoryError("This item no longer exists.")
        return self._item_dict(row)

    def items(self, query="", location_id=None, category="", tag="", low_only=False, sort="Recently updated"):
        result = [self._item_dict(row) for row in self.db.execute("SELECT * FROM items ORDER BY updated_at DESC")]
        ids = self.descendants(location_id) if location_id else None
        query = query.strip().casefold()
        filtered = []
        for item in result:
            if ids is not None and item["location_id"] not in ids:
                continue
            if category and item["category"] != category:
                continue
            if tag and tag not in item["tags"]:
                continue
            if low_only and item["quantity"] > item["low_stock"]:
                continue
            if query:
                text = " ".join([item["name"], item["category"], item["notes"], *item["tags"], self.path_text(item["location_id"])])
                if query not in text.casefold():
                    continue
            filtered.append(item)
        if sort == "Name A–Z":
            filtered.sort(key=lambda item: item["name"].casefold())
        elif sort == "Quantity: low first":
            filtered.sort(key=lambda item: (item["quantity"], item["name"].casefold()))
        elif sort == "Value: high first":
            filtered.sort(key=lambda item: item["quantity"] * item["value"], reverse=True)
        return filtered

    def stats(self):
        row = self.db.execute("SELECT COUNT(*) AS items, COALESCE(SUM(quantity),0) AS units, "
                              "COALESCE(SUM(quantity*value),0) AS value, "
                              "COALESCE(SUM(quantity<=low_stock),0) AS low FROM items").fetchone()
        result = dict(row)
        result["locations"] = self.db.execute("SELECT COUNT(*) FROM locations").fetchone()[0]
        return result

    def _validated_item(self, data, old=None):
        name = required_text(data.get("name"), "Item name")
        category = data.get("category")
        if category not in CATEGORIES:
            raise InventoryError("Choose a valid category.")
        quantity = whole_number(data.get("quantity", ""), "Quantity")
        low_stock = whole_number(data.get("low_stock", 0), "Low stock threshold")
        try:
            value = float(data.get("value") or 0)
        except (ValueError, TypeError):
            raise InventoryError("Unit value must be a number.") from None
        if not math.isfinite(value) or not 0 <= value <= 100_000_000:
            raise InventoryError("Unit value must be between $0 and $100,000,000.")
        if len(str(data.get("value") or 0)) > 30:
            raise InventoryError("Unit value is too long.")
        value = round(value, 2)
        notes = str(data.get("notes") or "").strip()
        if len(notes) > 2000:
            raise InventoryError("Notes must be 2,000 characters or fewer.")
        raw_tags = data.get("tags", [])
        if isinstance(raw_tags, str):
            raw_tags = raw_tags.split(",")
        if not isinstance(raw_tags, (list, tuple)):
            raise InventoryError("Tags must be separated by commas.")
        tags = list(dict.fromkeys(str(tag).strip().casefold() for tag in raw_tags if str(tag).strip()))
        if len(tags) > 12 or any(len(tag) > 30 for tag in tags):
            raise InventoryError("Use up to 12 tags, each 30 characters or fewer.")
        location_id = data.get("location_id")
        if not location_id:
            raise InventoryError("Choose a location for this item.")
        self.get_location(location_id)
        photo = data.get("photo", old["photo"] if old else b"")
        if not isinstance(photo, bytes):
            raise InventoryError("Photo data must come from an image file.")
        if len(photo) > MAX_PHOTO_BYTES:
            raise InventoryError("Choose a photo under 10 MB.")
        if photo:
            supported = photo.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a")) or (photo.startswith(b"RIFF") and photo[8:12] == b"WEBP")
            if not supported:
                raise InventoryError("Use a PNG, GIF, JPG, or WebP photo.")
        return {
            "id": old["id"] if old else f"item_{uuid.uuid4().hex}",
            "name": name, "category": category, "quantity": quantity,
            "location_id": location_id, "value": value, "low_stock": low_stock,
            "notes": notes, "tags": tags, "photo": photo,
            "photo_name": str(data.get("photo_name", old["photo_name"] if old else ""))[:255],
            "created_at": old["created_at"] if old else now(), "updated_at": now()
        }

    def _insert_item(self, item):
        self.db.execute("INSERT INTO items(id,name,category,quantity,location_id,value,low_stock,notes,photo,photo_name,created_at,updated_at) "
                        "VALUES(:id,:name,:category,:quantity,:location_id,:value,:low_stock,:notes,:photo,:photo_name,:created_at,:updated_at)", item)
        self.db.executemany("INSERT INTO item_tags(item_id,tag) VALUES(?,?)", [(item["id"], tag) for tag in item["tags"]])

    def save_item(self, data, item_id=None):
        with self.transaction():
            old = self.get_item(item_id) if item_id else None
            item = self._validated_item(data, old)
            if old:
                self.db.execute("UPDATE items SET name=:name,category=:category,quantity=:quantity,location_id=:location_id,"
                                "value=:value,low_stock=:low_stock,notes=:notes,photo=:photo,photo_name=:photo_name,"
                                "updated_at=:updated_at WHERE id=:id", item)
                self.db.execute("DELETE FROM item_tags WHERE item_id=?", (item_id,))
                self.db.executemany("INSERT INTO item_tags(item_id,tag) VALUES(?,?)", [(item_id, tag) for tag in item["tags"]])
            else:
                self._insert_item(item)
            self._log(f"{'Updated' if old else 'Added'} {item['name']}")
        return self.get_item(item["id"])

    def adjust_quantity(self, item_id, delta):
        if not isinstance(delta, int) or isinstance(delta, bool) or delta not in (-1, 1):
            raise InventoryError("Adjust quantity one unit at a time.")
        with self.transaction():
            item = self.get_item(item_id)
            quantity = item["quantity"] + delta
            whole_number(quantity, "Quantity")
            self.db.execute("UPDATE items SET quantity=?,updated_at=? WHERE id=?", (quantity, now(), item_id))
            self._log(f"Set {item['name']} quantity to {quantity}")
        return self.get_item(item_id)

    def delete_item(self, item_id):
        with self.transaction():
            item = self.get_item(item_id)
            self.db.execute("DELETE FROM items WHERE id=?", (item_id,))
            self._log(f"Deleted {item['name']}")
        return item

    def restore_item(self, item):
        with self.transaction():
            if self.db.execute("SELECT 1 FROM items WHERE id=?", (item["id"],)).fetchone():
                raise InventoryError("This item is already restored.")
            restored = self._validated_item(item)
            restored.update(id=item["id"], created_at=item["created_at"])
            self._insert_item(restored)
            self._log(f"Restored {restored['name']}")
        return self.get_item(item["id"])

    def activity(self):
        return [dict(row) for row in self.db.execute("SELECT * FROM activity ORDER BY id DESC LIMIT 200")]

    def reset_samples(self):
        with self.transaction():
            self.db.execute("DELETE FROM items")
            for location in sorted(self.locations(), key=lambda loc: len(self.path_names(loc["id"])), reverse=True):
                self.db.execute("DELETE FROM locations WHERE id=?", (location["id"],))
            self.db.execute("DELETE FROM activity")
            self._load_samples()
            self._log("Reset to the original sample inventory")

    def backup(self, destination):
        target = Path(destination).resolve()
        if target == self.path:
            raise InventoryError("Choose a different file for your backup.")
        if not target.parent.is_dir():
            raise InventoryError("Choose an existing folder for your backup.")
        connection = sqlite3.connect(target)
        try:
            self.db.backup(connection)
        finally:
            connection.close()

    def export_csv(self, destination, items=None):
        rows = self.items() if items is None else items
        with Path(destination).open("w", newline="", encoding="utf-8-sig") as stream:
            writer = csv.writer(stream)
            writer.writerow(["Name", "Category", "Quantity", "Location", "Tags", "Unit value", "Total value", "Low stock at", "Notes"])
            for item in rows:
                cells = [item["name"], item["category"], item["quantity"], self.path_text(item["location_id"]),
                         ", ".join(item["tags"]), f"{item['value']:.2f}", f"{item['value'] * item['quantity']:.2f}", item["low_stock"], item["notes"]]
                # Treat text that looks like a spreadsheet formula as literal text.
                writer.writerow(["'" + v if isinstance(v, str) and v.startswith(("=", "+", "-", "@", "\t", "\r")) else v for v in cells])
