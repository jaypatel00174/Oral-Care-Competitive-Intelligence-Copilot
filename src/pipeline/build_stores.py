"""Load the scraped dataset into SQLite and Chroma.

Run it after build_dataset.py (it reads data/products.json and
data/snippets.jsonl, so it never touches the internet):

    python -m src.pipeline.build_stores
    python -m src.pipeline.build_stores --skip-vectors   # SQL only (no model download)

The first run downloads the embedding model (about 130 MB) once.
Both stores are rebuilt from scratch every time, so results are repeatable.
"""
from __future__ import annotations

import argparse
import json

from src.common.schema import Product, Snippet
from src.pipeline.build_dataset import DATA
from src.storage import sql_store, vector_store


def load_dataset() -> tuple[list[Product], list[Snippet]]:
    """Read the files written by build_dataset.py."""
    products = [Product(**d) for d in json.loads((DATA / "products.json").read_text(encoding="utf-8"))]
    snippets = [Snippet(**json.loads(line))
                for line in (DATA / "snippets.jsonl").read_text(encoding="utf-8").splitlines() if line]
    return products, snippets


def main() -> int:
    ap = argparse.ArgumentParser(description="Load products and snippets into SQLite and Chroma.")
    ap.add_argument("--skip-vectors", action="store_true", help="only build the SQL database")
    args = ap.parse_args()

    products, snippets = load_dataset()
    sql_store.build_sql(products, snippets)
    print(f"SQLite : {len(products)} products, {len(snippets)} snippets -> {sql_store.DB_PATH}")

    if not args.skip_vectors:
        names = {p.product_id: p.name for p in products}
        n = vector_store.build_vectors(snippets, names)
        print(f"Chroma : {n} vectors ({vector_store.EMBEDDING_MODEL}) -> {vector_store.CHROMA_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
