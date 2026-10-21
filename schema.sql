PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS locations (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL CHECK(length(trim(name)) BETWEEN 1 AND 60),
    kind TEXT NOT NULL CHECK(kind IN ('House','Room','Storage','Drawer','Shelf','Container')),
    parent_id TEXT REFERENCES locations(id) ON DELETE RESTRICT
);
CREATE UNIQUE INDEX IF NOT EXISTS location_sibling_name ON locations(COALESCE(parent_id,''), lower(name));
CREATE TABLE IF NOT EXISTS items (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL CHECK(length(trim(name)) BETWEEN 1 AND 100),
    category TEXT NOT NULL CHECK(category IN ('Electronics','Tools','Kitchen','Outdoor','Books','Home')),
    quantity INTEGER NOT NULL CHECK(typeof(quantity)='integer' AND quantity BETWEEN 0 AND 999999),
    location_id TEXT NOT NULL REFERENCES locations(id) ON DELETE RESTRICT,
    value REAL NOT NULL DEFAULT 0 CHECK(value BETWEEN 0 AND 100000000),
    low_stock INTEGER NOT NULL DEFAULT 0 CHECK(typeof(low_stock)='integer' AND low_stock BETWEEN 0 AND 999999),
    notes TEXT NOT NULL DEFAULT '',
    photo BLOB NOT NULL DEFAULT X'',
    photo_name TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS items_location ON items(location_id);
CREATE TABLE IF NOT EXISTS item_tags (
    item_id TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    tag TEXT NOT NULL CHECK(length(tag) BETWEEN 1 AND 30),
    PRIMARY KEY(item_id, tag)
);
CREATE TABLE IF NOT EXISTS activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL
);
