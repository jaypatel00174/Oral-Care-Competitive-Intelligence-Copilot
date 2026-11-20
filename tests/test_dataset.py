"""Sanity checks on the files written by src.pipeline.build_dataset.

Run the pipeline first:  python -m src.pipeline.build_dataset
"""
import json
from pathlib import Path

import pytest

DATA = Path(__file__).resolve().parents[1] / "data"


@pytest.fixture(scope="module")
def products():
    return json.loads((DATA / "products.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def snippets():
    lines = (DATA / "snippets.jsonl").read_text(encoding="utf-8").splitlines()
    return {s["id"]: s for s in map(json.loads, lines)}


def test_minimum_products_per_brand(products):
    for brand in ("Philips Sonicare", "Oral-B"):
        assert sum(p["brand"] == brand for p in products) >= 8


def test_product_ids_are_unique(products):
    ids = [p["product_id"] for p in products]
    assert len(ids) == len(set(ids))


def test_every_evidence_id_points_to_a_real_snippet(products, snippets):
    for p in products:
        for attr, ids in p["evidence"].items():
            for sid in ids:
                assert sid in snippets, f"{p['product_id']}.{attr} -> missing {sid}"
                assert snippets[sid]["product_id"] == p["product_id"] or snippets[sid]["brand"] == p["brand"]


def test_every_filled_attribute_has_evidence(products):
    attrs = ["price_usd", "modes", "pressure_sensor", "app_connectivity", "timer",
             "battery_life", "charging", "included_items", "warranty"]
    for p in products:
        for a in attrs:
            if p.get(a) is not None:
                assert p["evidence"].get(a), f"{p['product_id']}.{a} has no evidence"


def test_prices_are_positive(products):
    for p in products:
        assert p["price_usd"] is None or p["price_usd"] > 0


def test_untrusted_review_content_is_excluded(products, snippets):
    blob = json.dumps(products) + json.dumps(list(snippets.values()))
    assert "96.62.143.209" not in blob
