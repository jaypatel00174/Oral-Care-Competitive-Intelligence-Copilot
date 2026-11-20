"""Build the comparison dataset end to end.

    scrape both websites -> parse each product -> merge -> write files

Run it from the repo root:

    python -m src.pipeline.build_dataset            # uses cached pages if present
    python -m src.pipeline.build_dataset --refresh  # re-download everything (throttled)

What it writes (everything later steps read comes from these files)
-------------------------------------------------------------------
    outputs/comparison_table.csv  one row per product, normalized columns (the deliverable)
    data/products.json            full Product records, including the evidence ids
    data/snippets.jsonl           every verbatim source snippet, one JSON per line

"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

from src.common.schema import Product, Snippet
from src.scraping import oralb as po       # po = parser for Oral-B
from src.scraping import philips as pp     # pp = parser for Philips
from src.scraping.fetcher import Fetcher

# Output locations (parents[2] = repo root, since this file is src/pipeline/...).
ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
OUT = ROOT / "outputs"

# Column order of comparison_table.csv. The last column holds, as JSON, which
# snippet ids prove each value, so every cell in the CSV can be traced back.
CSV_COLUMNS = [
    "product_id", "brand", "name", "series", "sku", "price_usd", "mode_count", "modes",
    "intensity_levels", "pressure_sensor", "pressure_feedback", "app_connectivity",
    "app_details", "timer", "battery_life", "battery_type", "charging",
    "brushing_motion", "included_items", "warranty", "claims", "notes",
    "source_url", "captured_at", "evidence_snippet_ids",
]


def _strip_footnotes(p: Product) -> None:
    """Remove trailing footnote stars from normalized values.

    Philips writes e.g. "14 days*****". The stars point to a footnote that we do
    not keep, so we drop them from the clean value. The original text is still
    in the snippet, unchanged.
    """
    for f in ("battery_life", "intensity_levels", "timer", "warranty"):
        v = getattr(p, f)
        if isinstance(v, str):
            setattr(p, f, re.sub(r"\s*\*+$", "", v).strip())


def _enrich_from_compare(prods: dict[str, Product], snips: dict[str, list[Snippet]], html: str) -> None:
    """Add facts from Oral-B's compare page to the matching products.

    The compare page lists a few models with labelled fields ("Battery Life",
    "Charging"). We store the page text as an extra snippet (so it can be cited)
    and use it to fill battery life / charging. Own product-page text wins for
    battery life; the compare page only fills the gap.
    """
    for model, body in po.parse_compare(html).items():
        # The compare page says "io9"; our product may be "io9-genius".
        pid = next((c for c in (f"OB-{model}", f"OB-{model}-genius") if c in prods), None)
        if pid is None:
            continue
        p = prods[pid]
        if "Learn More" in body:  # drop the price/"Add to cart" header block
            body = body[body.index("Learn More") + 1 :]
        text = " | ".join(body)

        def after(label: str) -> str | None:
            """Value lines that follow a label such as 'Battery Life' (until the next label)."""
            if label in body:
                i = body.index(label) + 1
                vals = []
                while i < len(body) and body[i] not in ("Battery Life", "Charging", "Color options", "App Connectivity"):
                    vals.append(body[i])
                    i += 1
                return "; ".join(vals) or None
            return None

        sid = f"{pid}#{len(snips[pid]):03d}"  # next free snippet number for this product
        snips[pid].append(Snippet(id=sid, product_id=pid, brand=po.BRAND, section="Compare page",
                                  text=text, source_url=po.COMPARE_URL))
        if (b := after("Battery Life")) and p.battery_life is None:
            p.set("battery_life", b, sid)
        if (c := after("Charging")):
            p.set("charging", c, sid)


def collect(refresh: bool = False) -> tuple[list[Product], list[Snippet]]:
    """Scrape both brands and return (all products, all snippets).

    A product page that fails to download/parse is reported on stderr and
    skipped, so one bad page does not abort the whole build.
    """
    f = Fetcher()
    try:
        if refresh:  # re-download the Philips listing; product pages are refreshed below
            f.get(pp.LISTING_URL, use_cache=False)
        ph_urls = pp.product_urls(f.get(pp.LISTING_URL))
        ob_urls = po.product_urls(*[f.get(u) for u in po.LISTING_URLS])

        prods: dict[str, Product] = {}
        snips: dict[str, list[Snippet]] = {}
        # Pair every URL with the parser module that understands that website.
        for url, mod in [(u, pp) for u in ph_urls] + [(u, po) for u in ob_urls]:
            try:
                p, s = mod.parse_product(f.get(url, use_cache=not refresh), url)
            except Exception as e:  # keep going; the summary shows the final count
                print(f"WARN failed {url}: {e!r}", file=sys.stderr)
                continue
            if p.product_id in prods:  # another colour of a model we already have
                continue
            prods[p.product_id], snips[p.product_id] = p, s
        _enrich_from_compare(prods, snips, f.get(po.COMPARE_URL))
    finally:
        f.close()  # always release the connection, even on errors
    for p in prods.values():
        _strip_footnotes(p)
    return list(prods.values()), [s for lst in snips.values() for s in lst]


def write_outputs(products: list[Product], snippets: list[Snippet]) -> None:
    """Write the three output files (see the module docstring)."""
    DATA.mkdir(exist_ok=True)
    OUT.mkdir(exist_ok=True)

    # 1) Full records as JSON (lists stay lists, evidence stays a dict).
    (DATA / "products.json").write_text(
        json.dumps([p.model_dump() for p in products], indent=2, ensure_ascii=False), encoding="utf-8")

    # 2) Snippets as JSON Lines: one snippet per line, easy to stream or index.
    with (DATA / "snippets.jsonl").open("w", encoding="utf-8") as fh:
        for s in snippets:
            fh.write(s.model_dump_json() + "\n")

    # 3) The flat CSV table. Lists are joined with " | "; empty values become
    #    blank cells (= not found in sources).
    with (OUT / "comparison_table.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        w.writeheader()
        for p in products:
            d = p.model_dump()
            row = {c: d.get(c) for c in CSV_COLUMNS}
            for c in ("modes", "included_items", "claims"):
                row[c] = " | ".join(d[c]) if d.get(c) else None
            row["evidence_snippet_ids"] = json.dumps(d["evidence"], ensure_ascii=False)
            w.writerow({k: ("" if v is None else v) for k, v in row.items()})


def main() -> int:
    """Command-line entry point. Returns the process exit code."""
    ap = argparse.ArgumentParser(description="Scrape both brands and build the comparison dataset.")
    ap.add_argument("--refresh", action="store_true", help="re-download pages instead of using the cache")
    args = ap.parse_args()

    products, snippets = collect(refresh=args.refresh)
    write_outputs(products, snippets)

    by_brand: dict[str, int] = {}
    for p in products:
        by_brand[p.brand] = by_brand.get(p.brand, 0) + 1
    print(f"products: {by_brand}  snippets: {len(snippets)}")
    # Success only if BOTH brands reach the case-study minimum of 8 products.
    return 0 if all(v >= 8 for v in by_brand.values()) and len(by_brand) == 2 else 1


if __name__ == "__main__":
    raise SystemExit(main())
