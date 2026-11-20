"""Query the two data stores from the command line (a developer tool).

Run from the repo root with the virtual environment active. Build the stores first:
    python -m src.pipeline.build_stores

Commands
--------
  tables                          list the SQL tables and their columns
  sql "<SELECT ...>"              run a read-only SQL query on data/comparison.db
  search "<question>" [options]   search the snippets by MEANING (Chroma vector search)
  snippet <snippet_id>            show one snippet in full (text + URL), e.g. OB-io9-genius#003
  evidence <product_id> <field>   show the snippets that prove a field, e.g. OB-io9-genius timer

Search options:   -k 5   number of results     --brand "Oral-B"   or   --brand "Philips Sonicare"
                  --product PH-HX9903_15       only this product

Examples
--------
  python scripts/query_stores.py tables
  python scripts/query_stores.py sql "SELECT name, price_usd FROM products WHERE pressure_sensor = 1 ORDER BY price_usd"
  python scripts/query_stores.py sql "SELECT brand, COUNT(*) n, ROUND(AVG(price_usd),2) avg_price FROM products GROUP BY brand"
  python scripts/query_stores.py search "which brush is gentle on gums" -k 3
  python scripts/query_stores.py search "what does the app do" --brand "Philips Sonicare"
  python scripts/query_stores.py evidence OB-io9-genius timer
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

# Make "import src...." work when this file is run directly; silence a harmless Windows warning.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

from src.storage import sql_store  # noqa: E402


def open_readonly() -> sqlite3.Connection:
    """Open the database in read-only mode: INSERT/UPDATE/DELETE/DROP will fail."""
    if not sql_store.DB_PATH.exists():
        sys.exit("No database found. Run: python -m src.pipeline.build_stores")
    db = sqlite3.connect(f"file:{sql_store.DB_PATH.as_posix()}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    return db


def print_rows(rows: list[sqlite3.Row], max_width: int = 60) -> None:
    """Print rows as a simple aligned table, trimming long cells."""
    if not rows:
        print("(no rows)")
        return
    cols = rows[0].keys()
    cell = lambda v: "" if v is None else (str(v) if len(str(v)) <= max_width else str(v)[: max_width - 1] + "…")
    widths = {c: max(len(c), *(len(cell(r[c])) for r in rows)) for c in cols}
    print("  ".join(c.ljust(widths[c]) for c in cols))
    print("  ".join("-" * widths[c] for c in cols))
    for r in rows:
        print("  ".join(cell(r[c]).ljust(widths[c]) for c in cols))
    print(f"\n{len(rows)} row(s)")


def cmd_tables(_: argparse.Namespace) -> None:
    db = open_readonly()
    for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        count = db.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
        cols = ", ".join(r["name"] for r in db.execute(f"PRAGMA table_info({name})"))
        print(f"{name} ({count} rows)\n    {cols}\n")


def cmd_sql(a: argparse.Namespace) -> None:
    db = open_readonly()
    try:
        print_rows(db.execute(a.query).fetchall())
    except sqlite3.Error as e:
        sys.exit(f"SQL error: {e}")


def cmd_search(a: argparse.Namespace) -> None:
    from src.storage import vector_store  # imported here: loading the model takes a moment

    hits = vector_store.search(a.question, k=a.k, brand=a.brand, product_id=a.product)
    for h in hits:
        print(f"{h.score:.2f}  {h.snippet_id}  [{h.section}]  ({h.brand})")
        print(f"      {h.text[:200]}")
        print(f"      {h.source_url}\n")


def cmd_snippet(a: argparse.Namespace) -> None:
    s = sql_store.get_snippet(open_readonly(), a.snippet_id)
    if not s:
        sys.exit(f"No snippet {a.snippet_id!r}")
    print(f"{s['snippet_id']}  [{s['section']}]  ({s['brand']})\n{s['text']}\n{s['source_url']}")


def cmd_evidence(a: argparse.Namespace) -> None:
    rows = sql_store.get_evidence(open_readonly(), a.product_id, a.field)
    if not rows:
        sys.exit(f"No evidence for {a.product_id}.{a.field} (the field is empty = 'not found in sources')")
    for s in rows:
        print(f"{s['snippet_id']}  [{s['section']}]\n   {s['text']}\n   {s['source_url']}\n")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("tables").set_defaults(fn=cmd_tables)

    p = sub.add_parser("sql")
    p.add_argument("query")
    p.set_defaults(fn=cmd_sql)

    p = sub.add_parser("search")
    p.add_argument("question")
    p.add_argument("-k", type=int, default=5)
    p.add_argument("--brand")
    p.add_argument("--product")
    p.set_defaults(fn=cmd_search)

    p = sub.add_parser("snippet")
    p.add_argument("snippet_id")
    p.set_defaults(fn=cmd_snippet)

    p = sub.add_parser("evidence")
    p.add_argument("product_id")
    p.add_argument("field")
    p.set_defaults(fn=cmd_evidence)

    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
