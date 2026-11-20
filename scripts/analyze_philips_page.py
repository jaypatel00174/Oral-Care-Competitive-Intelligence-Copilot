"""Explore what a Philips Sonicare product page contains.

Usage (from the repo root, virtual environment active):

    python scripts/analyze_philips_page.py                  # the default demo product
    python scripts/analyze_philips_page.py <product-url>    # any Philips /c-p/ product page
    python scripts/analyze_philips_page.py --all            # every product on the listing page
    python scripts/analyze_philips_page.py <url> --dump-spec spec.json   # save the raw spec JSON

Pages come from the cache in data/raw/ when present, so repeat runs send no requests. 

What the report shows
---------------------
  1. Page basics         size and number of <script> tags.
  2. JSON-LD blocks      the structured data format that adds machine-readable context to web pages (name, SKU, price).
  3. Spec blob           the embedded spec sheet.
  4. Parser coverage     which spec items are covered, and which is ignores.
  5. (--all) Frequency   all spec frequency insights

Real parser connection
------------------------------------------
This script REUSES the project's own code (philips._spec_blob, philips.parse_product,
Fetcher).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from bs4 import BeautifulSoup 
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.scraping import philips 
from src.scraping.fetcher import Fetcher 

# Demo product used when no URL is given 
# product : the DiamondClean Smart 9300.
# file_name : ba4aed4924e0cd51ffb437f1.html
DEFAULT_URL = (
    "https://www.usa.philips.com/c-p/HX9903_15/"
    "diamondclean-smart-9300-rechargeable-toothbrush"
)

# Purpose: count reviews containing suspicious content (Note: - this is NOT the project's security control.)
SUSPICIOUS = re.compile(
    r"<\s*(a|form|script|img|iframe)\b"
    r"|href\s*="
    r"|action\s*="
    r"|https?://\d+\.\d+\.\d+\.\d+",
    re.I
)


# ----------------------- helper functions
def heading(title: str) -> None:
    """Print a section title """
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def load_jsonld_blocks(soup: BeautifulSoup) -> list[dict]:
    """Return ALL JSON-LD objects on the page."""
    blocks = []
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except json.JSONDecodeError:
            continue  # a malformed block should not stop the analysis
        blocks.extend(data if isinstance(data, list) else [data])
    return blocks


def spec_as_table(spec: dict) -> list[tuple[str, str, str]]:
    """Flatten the nested spec blob into simple (chapter, item, value) rows.

    The raw blob looks like:
        {"csChapter": [{"csChapterName": "Ease of use",
                        "csItem": [{"csItemName": "Timer",
                                    "csValue": [{"csValueName": "BrushPacer and SmartTimer"}]}]}]}
    If one item can have several values; they are joined with "; ".
    """
    rows = []
    for chapter in spec.get("csChapter", []):
        for item in chapter.get("csItem", []):
            value = "; ".join(v.get("csValueName", "") for v in item.get("csValue", []))
            rows.append((chapter.get("csChapterName", ""), item.get("csItemName", ""), value))
    return rows


def short(text: str, n: int = 70) -> str:
    """Collapse whitespace and cut long text to n characters (adds '…') so each report line fits."""
    text = re.sub(r"\s+", " ", str(text))
    return text if len(text) <= n else text[: n - 1] + "…"


# --------- report generation sunctions
def report_basics(html: str, soup: BeautifulSoup) -> None:
    """Section 1: how big is the page and where could data be hiding?"""
    heading("1. PAGE BASICS")
    scripts = soup.find_all("script")
    print(f"HTML size            : {len(html):,} characters")
    print(f"<script> tags        : {len(scripts)}")
    # JSON-LD scripts hold the structured product data (name, price, ...).
    print(f"  JSON-LD scripts    : {sum(1 for s in scripts if s.get('type') == 'application/ld+json')}")
    # Next.js ships page data as self.__next_f.push(...) calls; the spec sheet is in one of them.
    print(f"  Next.js data chunks: {html.count('self.__next_f.push')}")
    print("(Decision point: Spec data is inside the Next.js chunks, so no browser is needed.)")
    print("Next.js is the React framework that renders this data server-side so search engines and AI systems can read it.")
    print("JSON-LD (JavaScript Object Notation for Linked Data) is a lightweight format used to add structured data to web pages")


def report_jsonld(soup: BeautifulSoup) -> None:
    """Section 2: the JSON-LD Product block, plus a safety check of its reviews."""
    heading("2. JSON-LD (structured data)")
    blocks = load_jsonld_blocks(soup)
    print(f"Blocks found: {[b.get('@type') for b in blocks]}")
    # The "Product" block is the one with the facts we want.
    product = next((b for b in blocks if b.get("@type") == "Product"), None)
    if not product:
        print("No schema.org Product block on this page.")
        return

    print("\nTop-level keys in the Product block:")
    for key, value in product.items():
        # Nested values (dicts/lists) are printed as JSON; plain values as they are.
        print(f"  {key:<16} {short(json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value)}")

    offer = product.get("offers") or {}
    print(f"\nPrice    : {offer.get('price')} {offer.get('priceCurrency')}   availability: {offer.get('availability')}")
    rating = product.get("aggregateRating") or {}
    print(f"Rating   : {rating.get('ratingValue')} from {rating.get('reviewCount')} reviews (as declared by the site)")

    # Review safety check: reviews are third-party text. Count the ones that
    # match the SUSPICIOUS pattern (see the comment where it is defined above).
    reviews = product.get("review") or []
    flagged = []
    for r in reviews:
        text = f"{r.get('name', '')} {r.get('reviewBody', '')}"  # check both the title and the body
        if SUSPICIOUS.search(text):
            flagged.append(r)
    print(f"\nReviews embedded in JSON-LD: {len(reviews)}  |  with HTML/links/IP URLs: {len(flagged)}")
    for r in flagged[:3]:  # show at most three examples
        print(f"  ! FLAGGED  author={short(r.get('author', {}).get('name', ''), 20)!r}  "
              f"body={short(r.get('reviewBody', ''), 90)!r}")
    if flagged:
        print("  -> philips.py deletes the 'review' key, so none of this reaches the dataset.")


def report_spec(html: str, dump_path: str | None) -> dict:
    """Section 3: print the spec sheet as a chapter -> item -> value table.

    Uses philips._spec_blob(), the same function the pipeline uses. If this
    section prints "No spec blob found", the pipeline would produce empty fields
    too, so that function is where to look.
    """
    heading("3. SPEC BLOB (chapter -> item -> value)")
    spec = philips._spec_blob(html)
    rows = spec_as_table(spec)
    if not rows:
        print("No spec blob found. The page layout may have changed: check philips._spec_blob().")
        return spec
    chapters = Counter(c for c, _, _ in rows)  # number of items per chapter
    print(f"{len(chapters)} chapters, {len(rows)} items\n")
    current = None
    for chapter, item, value in rows:
        if chapter != current:  # print a chapter title once, when it changes
            print(f"\n[{chapter}]  ({chapters[chapter]} items)")
            current = chapter
        print(f"   {short(item, 34):<34} = {short(value, 80)}")
    if dump_path:  # optional: save the raw JSON for closer study in an editor
        Path(dump_path).write_text(json.dumps(spec, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nRaw spec JSON saved to {dump_path}")
    return spec


def report_coverage(html: str, url: str) -> None:
    """Section 4: compare what the page offers with what current philips.parse_product() uses.

    We run the real parser, then look at which snippets ended up as evidence for a normalized field. 
    Spec items whose snippet is NOT used as evidence are ignored by the parser: candidates for new columns or new mappings (for enhancment/for accuracy).
    """
    heading("4. PARSER COVERAGE (what philips.py uses vs ignores)")
    product, snippets = philips.parse_product(html, url)
    by_id = {s.id: s for s in snippets}
    # Every snippet id that proves at least one normalized field.
    used_ids = {sid for ids in product.evidence.values() for sid in ids}
    # Spec snippets have a section like "Ease of use > Timer" (chapter > item).
    spec_snips = [s for s in snippets if " > " in s.section]

    used = [s for s in spec_snips if s.id in used_ids]
    unused = [s for s in spec_snips if s.id not in used_ids]
    print(f"Spec items used by a normalized field : {len(used)}")
    print(f"Spec items NOT used (candidates)      : {len(unused)}\n")

    print("Field -> evidence (what each normalized field was built from):")
    for attr, ids in product.evidence.items():
        first = by_id[ids[0]]
        extra = f" (+{len(ids) - 1} more)" if len(ids) > 1 else ""
        print(f"  {attr:<18} <- {first.section}{extra}")

    # An empty field means "not found in sources" (never "no"). claims/evidence/notes
    # are excluded because they are lists/metadata, not comparable features.
    print("\nFields that stayed EMPTY (= 'not found in sources'):")
    empty = [f for f in philips.Product.model_fields
             if f not in ("claims", "evidence", "notes") and getattr(product, f) in (None, [])]
    print("  " + (", ".join(empty) or "none"))

    print("\nIgnored spec items (can be added a mapping in philips.parse_product() to use one):")
    for s in unused:
        # Snippet text is "Item: value"; show only the value part.
        print(f"   {short(s.section, 40):<40} {short(s.text.split(': ', 1)[-1], 40)}")


def report_all(fetcher: Fetcher) -> None:
    """Section 5 (--all): count how often each spec item appears across all products.

    Items present on most products make good comparison columns. 
    Items on only a few products explain why some normalized fields are blank for other products.
    It also exposes the same fact stored under different names on different products 
    (for example 'Ease of use > Timer' versus 'Cleaning performance > Timer').
    """
    heading("5. ALL PRODUCTS: spec item frequency")
    urls = philips.product_urls(fetcher.get(philips.LISTING_URL))
    print(f"{len(urls)} products on the listing page\n")
    # (chapter, item) -> list of the product codes that have it
    seen: dict[tuple[str, str], list[str]] = defaultdict(list)
    for url in urls:
        rows = spec_as_table(philips._spec_blob(fetcher.get(url)))
        sku = url.split("/c-p/")[1].split("/")[0]  # e.g. "HX9903_15"
        for chapter, item, _ in rows:
            seen[(chapter, item)].append(sku)
    print(f"{'chapter > item':<52} products")
    # Most common items first; ties sorted alphabetically.
    for (chapter, item), skus in sorted(seen.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        print(f"  {short(chapter + ' > ' + item, 50):<50} {len(skus):>3}/{len(urls)}")
    print("\nItems present on most products make the best comparison columns;")
    print("Items on only a few products explain why some fields are blank.")


# --------------------------------------------------------------------------- main
def main() -> int:
    """Read the command-line options and print the requested report."""
    ap = argparse.ArgumentParser(description="Explore a Philips Sonicare product page.")
    ap.add_argument("url", nargs="?", default=DEFAULT_URL, help="Philips /c-p/ product URL")
    ap.add_argument("--all", action="store_true", help="scan every product on the listing page")
    ap.add_argument("--dump-spec", metavar="FILE", help="save the raw spec JSON to FILE")
    args = ap.parse_args()

    fetcher = Fetcher()  # the project's polite downloader: cache, robots.txt, throttle
    try:
        if args.all:
            report_all(fetcher)
            return 0
        html = fetcher.get(args.url)
        soup = BeautifulSoup(html, "lxml")
        print(f"URL: {args.url}")
        report_basics(html, soup)
        report_jsonld(soup)
        report_spec(html, args.dump_spec)
        report_coverage(html, args.url)
    finally:
        fetcher.close()  # always release the connection, even if a report fails
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


## Insight from default run -all
======================================
======================================

# chapter > item                                       products
#   Design and finishing > Color                        12/12
#   Items included > Handle                             12/12
#   Technical specifications > Operating time (full t…  12/12
#   Technical specifications > Battery                  11/12
#   Ease of use > Battery indicator                     10/12
#   Ease of use > Handle                                10/12
#   Items included > Charger                            10/12
#   Service > Warranty                                  10/12
#   Technical specifications > Battery type             10/12
#   Modes > Clean                                        9/12
#   Cleaning performance > Plaque removal                8/12
#   Ease of use > Handle compatibility                   8/12
#   Ease of use > Timer                                  8/12
#   Intensities > Low                                    8/12
#   Intensities > Medium                                 8/12
#   Power > Voltage                                      8/12
#   Technical specifications > Energy Consumption        8/12
#   Intensities > High                                   7/12
#   Items included > Travel case                         7/12
#   Cleaning performance > Speed                         6/12
#   Items included > Brush head                          6/12
#   Items included > Brush heads                         6/12
#   Smart sensor technology > Pressure feedback          6/12
#   Smart sensor technology > Replacement Reminder       6/12
#   Compatibility > Android compatibility                5/12
#   Compatibility > Bluetooth wireless technology        5/12
#   Compatibility > iOS compatibility                    5/12
#   Modes > Gum Health                                   5/12
#   Sonicare app > Tracking and progress reports         5/12
#   Cleaning performance > Gum Health                    4/12
#   Ease of use > USB-A Charging                         4/12
#   Modes > Sensitive                                    4/12
#   Cleaning performance > Whitening                     3/12
#   Modes > White                                        3/12
#   Cleaning performance > Timer                         2/12
#   Compatibility > Brush head compatibility             2/12
#   Compatibility > Handle compatibility                 2/12
#   Ease of use > Brush head system                      2/12
#   Ease of use > Travel case                            2/12
#   Modes > White+                                       2/12
#   Service > Software support                           2/12
#   Sonicare app > Personalized guidance                 2/12
#   Cleaning performance > Performance                   1/12
#   Cleaning performance > Pressure feedback             1/12
#   Ease of use > BrushSync Replacement Reminder         1/12
#   Items included > AAA Alkaline                        1/12
#   Items included > Stickers                            1/12
#   Modes > 2 intensities                                1/12
#   Modes > Deep Clean                                   1/12
#   Modes > Deep Clean+                                  1/12
#   Power > Power supply                                 1/12
#   Smart sensor technology > BrushSync Replacement R…   1/12
#   Smart sensor technology > Pressure sensor            1/12
#   Smart sensor technology > SenseIQ                    1/12
#   Sonicare app > Real-time coaching                    1/12
#   Sonicare app > Real-time coaching with SenseIQ       1/12

# -------------------------------------------------------------------------------------------------------------------------
# Items on all or most products (about 10 to 12 of 12). These make reliable comparison columns:
# - Color, Handle and Operating time (battery run time) are on 12/12.
# - Battery (11/12), Battery indicator, Charger, Warranty and Battery type (10/12 each) are near-universal.

# Items on about half the products or fewer. This is why some of our columns are blank for some products:
# - Timer is on 8/12 and pressure feedback on 6/12.
# - Bluetooth and the app features are on 5/12.
# - Modes are on 9/12. The three entry-level products (the kids brush and the two Philips One brushes) have no Modes chapter at all.

# Items on only 1 or 2 products. These are model-specific features and don't work as columns:
# - SenseIQ, AAA Alkaline batteries and Stickers are on 1/12 each. Stickers are probably the kids brush.
# - Deep Clean, Real-time coaching with SenseIQ and Power supply are also on 1/12.