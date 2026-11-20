"""Tests for the SQLite and Chroma stores.

Run the pipeline first:
    python -m src.pipeline.build_dataset
    python -m src.pipeline.build_stores
"""
import json

import pytest

from src.pipeline.build_dataset import DATA
from src.storage import sql_store, vector_store


@pytest.fixture(scope="module")
def db():
    conn = sql_store.connect()
    yield conn
    conn.close()


def test_sql_row_counts_match_the_json_files(db):
    n_products = len(json.loads((DATA / "products.json").read_text(encoding="utf-8")))
    n_snippets = len((DATA / "snippets.jsonl").read_text(encoding="utf-8").splitlines())
    assert db.execute("SELECT COUNT(*) FROM products").fetchone()[0] == n_products
    assert db.execute("SELECT COUNT(*) FROM snippets").fetchone()[0] == n_snippets


def test_sql_has_no_broken_foreign_keys(db):
    # Every snippet belongs to a product and every evidence row points at a real snippet.
    assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_boolean_fields_are_only_1_or_null(db):
    # "not found" must never be stored as 0 ("no").
    bad = db.execute("SELECT COUNT(*) FROM products WHERE pressure_sensor = 0 OR app_connectivity = 0").fetchone()[0]
    assert bad == 0


def test_evidence_lookup_returns_the_proving_snippet(db):
    rows = sql_store.get_evidence(db, "OB-io9-genius", "timer")
    assert rows and "timer" in rows[0]["text"].lower()


def test_unknown_ids_return_none(db):
    assert sql_store.get_product(db, "NOPE") is None
    assert sql_store.get_snippet(db, "NOPE#000") is None


def test_vector_search_returns_relevant_snippets():
    hits = vector_store.search("travel case included in the box", k=3)
    assert len(hits) == 3
    assert any("travel case" in h.text.lower() for h in hits)


def test_vector_search_respects_the_brand_filter():
    hits = vector_store.search("pressure sensor", k=5, brand="Oral-B")
    assert hits and all(h.brand == "Oral-B" for h in hits)


def test_every_vector_hit_exists_in_sql(db):
    for h in vector_store.search("battery life", k=5):
        assert sql_store.get_snippet(db, h.snippet_id) is not None
