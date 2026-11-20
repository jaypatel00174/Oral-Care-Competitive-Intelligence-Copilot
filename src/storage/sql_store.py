"""SQLite store: the exact, structured facts.

Three tables
------------
    products   one row per toothbrush; the normalized columns of comparison_table.csv
    snippets   the verbatim source texts (citation units) with their URL
    evidence   which snippet proves which field of which product
               (product_id, attribute, snippet_id)  <- real foreign keys,
               so a citation can never point at a snippet that does not exist

Typical use
-----------
    build_sql(products, snippets)           # (re)create the database from scratch
    with connect() as db:
        rows = db.execute("SELECT name, price_usd FROM products ORDER BY price_usd").fetchall()
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from src.common.schema import Product, Snippet

# data/comparison.db (parents[2] = repo root; this file is src/storage/sql_store.py)
DB_PATH = Path(__file__).resolve().parents[2] / "data" / "comparison.db"

SCHEMA = """
CREATE TABLE products (
    product_id         TEXT PRIMARY KEY,
    brand              TEXT NOT NULL,
    name               TEXT NOT NULL,
    series             TEXT,
    sku                TEXT,
    source_url         TEXT NOT NULL,
    captured_at        TEXT NOT NULL,
    price_usd          REAL,
    mode_count         INTEGER,
    intensity_levels   TEXT,
    pressure_sensor    INTEGER,          -- 1 = page says so, NULL = not found
    pressure_feedback  TEXT,
    app_connectivity   INTEGER,          -- 1 = page says so, NULL = not found
    app_details        TEXT,
    timer              TEXT,
    battery_life       TEXT,
    battery_type       TEXT,
    charging           TEXT,
    brushing_motion    TEXT,
    warranty           TEXT,
    notes              TEXT,
    modes_json         TEXT,             -- JSON list, e.g. ["Clean", "White+"]
    included_items_json TEXT,            -- JSON list
    claims_json        TEXT              -- JSON list of quoted marketing claims
);

CREATE TABLE snippets (
    snippet_id  TEXT PRIMARY KEY,
    product_id  TEXT NOT NULL REFERENCES products(product_id),
    brand       TEXT NOT NULL,
    section     TEXT NOT NULL,
    text        TEXT NOT NULL,
    source_url  TEXT NOT NULL
);

CREATE TABLE evidence (
    product_id  TEXT NOT NULL REFERENCES products(product_id),
    attribute   TEXT NOT NULL,           -- e.g. "timer"
    snippet_id  TEXT NOT NULL REFERENCES snippets(snippet_id),
    PRIMARY KEY (product_id, attribute, snippet_id)
);

CREATE INDEX idx_snippets_product ON snippets(product_id);
CREATE INDEX idx_evidence_snippet ON evidence(snippet_id);
"""

# Plain text/number columns copied straight from Product.
_SCALAR_COLUMNS = [
    "product_id", "brand", "name", "series", "sku", "source_url", "captured_at",
    "price_usd", "mode_count", "intensity_levels", "pressure_feedback", "app_details",
    "timer", "battery_life", "battery_type", "charging", "brushing_motion", "warranty", "notes",
]


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    """Open the database. Rows can be read by column name: row["price_usd"]."""
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")  # SQLite ignores foreign keys unless asked
    return db


def build_sql(products: list[Product], snippets: list[Snippet], path: Path = DB_PATH) -> None:
    """Create the database from scratch (deletes any old file, so it is repeatable)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    db = connect(path)
    try:
        db.executescript(SCHEMA)

        for p in products:
            d = p.model_dump()
            row = {c: d[c] for c in _SCALAR_COLUMNS}
            # True -> 1; None stays NULL. We never store 0 because the pipeline
            # never learns that a feature is absent, only that it is not mentioned.
            row["pressure_sensor"] = 1 if p.pressure_sensor else None
            row["app_connectivity"] = 1 if p.app_connectivity else None
            row["modes_json"] = json.dumps(p.modes, ensure_ascii=False) if p.modes else None
            row["included_items_json"] = json.dumps(p.included_items, ensure_ascii=False) if p.included_items else None
            row["claims_json"] = json.dumps(p.claims, ensure_ascii=False) if p.claims else None
            cols = ", ".join(row)
            marks = ", ".join(":" + c for c in row)
            db.execute(f"INSERT INTO products ({cols}) VALUES ({marks})", row)

        db.executemany(
            "INSERT INTO snippets (snippet_id, product_id, brand, section, text, source_url) "
            "VALUES (:id, :product_id, :brand, :section, :text, :source_url)",
            [s.model_dump() for s in snippets],
        )

        db.executemany(
            "INSERT OR IGNORE INTO evidence (product_id, attribute, snippet_id) VALUES (?, ?, ?)",
            [(p.product_id, attr, sid) for p in products for attr, ids in p.evidence.items() for sid in ids],
        )
        db.commit()
    finally:
        db.close()


# ------------------- helper functions
# Small, safe, read-only functions the assistant and the MCP tools can call.

def list_products(db: sqlite3.Connection, brand: str | None = None) -> list[sqlite3.Row]:
    """All products (optionally one brand), cheapest first; unknown prices last."""
    sql = "SELECT * FROM products"
    args: tuple = ()
    if brand:
        sql += " WHERE brand = ?"
        args = (brand,)
    return db.execute(sql + " ORDER BY price_usd IS NULL, price_usd", args).fetchall()


def get_product(db: sqlite3.Connection, product_id: str) -> sqlite3.Row | None:
    return db.execute("SELECT * FROM products WHERE product_id = ?", (product_id,)).fetchone()


def get_snippet(db: sqlite3.Connection, snippet_id: str) -> sqlite3.Row | None:
    return db.execute("SELECT * FROM snippets WHERE snippet_id = ?", (snippet_id,)).fetchone()


def get_evidence(db: sqlite3.Connection, product_id: str, attribute: str) -> list[sqlite3.Row]:
    """The snippets (text + URL) that prove one field of one product."""
    return db.execute(
        "SELECT s.* FROM evidence e JOIN snippets s ON s.snippet_id = e.snippet_id "
        "WHERE e.product_id = ? AND e.attribute = ? ORDER BY s.snippet_id",
        (product_id, attribute),
    ).fetchall()
