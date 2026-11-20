"""Explore what an Oral-B product page contains.

Why this differs from the Philips script
----------------------------------------
Philips pages carry a structured spec table (chapter -> item -> value). 
Oral-B pages do NOT: so oralb.py reads facts with regular expressions. 
That means two things can go wrong, and this report checks both:
  * a regex matches the wrong line      -> section 4 shows exactly which line it used
  * a fact is on the page but no regex found it -> section 5 lists such lines

Usage (from the repo root, virtual environment active):

    python scripts/analyze_oralb_page.py                  # the default demo product
    python scripts/analyze_oralb_page.py <product-url>    # any Oral-B product page
    python scripts/analyze_oralb_page.py --compare        # the compare page (3 models server-rendered)
    python scripts/analyze_oralb_page.py --all            # every model: which fields are filled

Pages come from the cache in data/raw/ when present (no new requests).

What the report shows
---------------------
  1. Page basics        size, scripts, how much of the text is repeated.
  2. JSON-LD            structured data: name, SKU, price, reviews check.
  3. Text structure     the description block, Oral-B's own feature list, "what's included".
  4. Parser coverage    each normalized field -> the exact line that filled it, and empty fields.
  5. Missed facts       lines mentioning battery/charge/timer/... that are NOT in any snippet.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from bs4 import BeautifulSoup
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.scraping import oralb
from src.scraping.fetcher import Fetcher

# Demo product used when no URL is given 
# product : the Oral B io series 9.
# file_name : 707055ea13241ebeed5d3cc2.html
DEFAULT_URL = (
    "https://oralb.com/en-us/products/electric-toothbrushes/"
    "oral-b-io-series-9-genius-rechargeable-electric-toothbrush-black-onyx/"
)

# Purpose: count reviews containing suspicious content (Note: - this is NOT the project's security control.)
SUSPICIOUS = re.compile(
    r"<\s*(a|form|script|img|iframe)\b"
    r"|href\s*="
    r"|action\s*="
    r"|https?://\d+\.\d+\.\d+\.\d+",
    re.I
)

# Words that signal a comparable fact. Used to look for facts the regexes missed.
KEYWORDS = ["battery", "charge", "charging", "hours", "weeks", "days", "timer", "minute",
            "pressure", "bluetooth", "app", "mode", "warranty", "travel case", "refill", "brush head"]


# --------------------------------------------------------------------------- helpers
def heading(title: str) -> None:
    """Print a section title """
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def short(text: str, n: int = 90) -> str:
    text = re.sub(r"\s+", " ", str(text))
    return text if len(text) <= n else text[: n - 1] + "…"


def jsonld_blocks(soup: BeautifulSoup) -> list[dict]:
    """ALL JSON-LD objects on the page (reviews kept, so we can inspect them)."""
    out = []
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except json.JSONDecodeError:
            continue
        out.extend(data if isinstance(data, list) else [data])
    return out


# --------------------------------------------------------------------------- report sections
def report_basics(html: str, soup: BeautifulSoup) -> list[str]:
    """Print page basics and return the cleaned, de-duplicated text lines."""
    heading("1. PAGE BASICS")
    scripts = soup.find_all("script")
    # Count visible lines BEFORE de-duplication, on a fresh copy of the page.
    fresh = BeautifulSoup(html, "lxml")
    for t in fresh(["script", "style", "noscript"]):
        t.decompose()
    raw = [oralb._clean(x) for x in fresh.get_text("\n", strip=True).split("\n") if x.strip()]
    lines = oralb._page_lines(BeautifulSoup(html, "lxml"))
    print(f"HTML size              : {len(html):,} characters")
    print(f"<script> tags          : {len(scripts)}  (JSON-LD: {sum(1 for s in scripts if s.get('type') == 'application/ld+json')})")
    print(f"Visible text lines     : {len(raw)} raw -> {len(lines)} after removing repeats")
    print(f"Repeated lines removed : {len(raw) - len(lines)} "
          f"({(len(raw) - len(lines)) / max(len(raw), 1):.0%})")
    print("(There is no spec table on this site; facts live in marketing prose, so oralb.py uses regexes.)")
    return lines


def report_jsonld(soup: BeautifulSoup) -> None:
    heading("2. JSON-LD (structured data)")
    blocks = jsonld_blocks(soup)
    print(f"Blocks found: {[b.get('@type') for b in blocks]}")
    product = next((b for b in blocks if b.get("@type") == "Product"), None)
    if not product:
        print("No schema.org Product block on this page.")
        return
    offer = product.get("offers") or {}
    print(f"\nname     : {product.get('name')}")
    print(f"sku      : {product.get('sku')}    gtin12: {product.get('gtin12')}")
    print(f"price    : {offer.get('price')} {offer.get('priceCurrency')}   valid until: {offer.get('priceValidUntil')}")
    print(f"url      : {short(product.get('url', ''))}")
    print(f"keys     : {', '.join(product)}")
    reviews = product.get("review") or []
    flagged = [r for r in reviews if SUSPICIOUS.search(f"{r.get('name', '')} {r.get('reviewBody', '')}")]
    print(f"Reviews in JSON-LD: {len(reviews)}  |  suspicious (HTML/links/IP URLs): {len(flagged)}")
    print("Note: the JSON-LD has no description of modes, timer, battery... that is why prose parsing is needed.")


def report_structure(lines: list[str]) -> None:
    heading("3. TEXT STRUCTURE (the blocks oralb.py reads)")
    try:
        start = lines.index("More Information") + 1
    except ValueError:
        start = None
    if start is None:
        print("No 'More Information' heading found: oralb.py falls back to the whole page. Check the layout!")
        start = 0
    end = next((i for i in range(start, len(lines)) if lines[i].startswith("What is covered in my")), len(lines))
    print(f"Description block: lines {start}..{end}  ({end - start} lines)\n")
    for ln in lines[start:end][:14]:
        skipped = len(ln) < 12 or ln.startswith(("•", "*")) or "Height:" in ln
        print(f"   {'(skipped) ' if skipped else '          '}{short(ln)}")
    if end - start > 14:
        print(f"   ... {end - start - 14} more lines")

    feats = [ln for ln in lines if re.match(r"^(TextFeature\d+|Shop By Feature):\s+\S", ln)]
    print(f"\nOral-B's own feature list ({len(feats)} entries, kept as 'Feature list' snippets):")
    for ln in feats:
        print(f"   {short(ln)}")

    print("\n'What's included' (lines after the Height/Weight line):")
    for i, ln in enumerate(lines):
        if "Height:" in ln:
            for nxt in lines[i : i + 7]:
                print(f"   {short(nxt)}")
            break
    else:
        print("   no 'Height:' line found: oralb.py uses the feature-list fallback for included items.")


def report_coverage(html: str, url: str) -> list:
    heading("4. PARSER COVERAGE (what oralb.py extracted, from which line)")
    product, snippets = oralb.parse_product(html, url)
    by_id = {s.id: s for s in snippets}
    print(f"Model key: {product.product_id}    series: {product.series}    snippets created: {len(snippets)}")
    by_section: dict[str, int] = {}
    for s in snippets:
        by_section[s.section] = by_section.get(s.section, 0) + 1
    print("Snippets per section: " + ", ".join(f"{k}={v}" for k, v in by_section.items()))

    print("\nField -> value  <-  the line that filled it:")
    fields = ["price_usd", "mode_count", "modes", "pressure_sensor", "app_connectivity", "timer",
              "battery_life", "charging", "brushing_motion", "included_items", "warranty"]
    empty = []
    for f in fields:
        value = getattr(product, f)
        if value in (None, []):
            empty.append(f)
            continue
        ids = product.evidence.get(f, [])
        line = by_id[ids[0]].text if ids else "?"
        print(f"  {f:<16} = {short(value, 40):<40} <- [{by_id[ids[0]].section if ids else '?'}] {short(line, 60)}")
    print(f"\nEMPTY fields (= 'not found in sources'): {', '.join(empty) or 'none'}")

    if product.mode_count and not product.modes:
        print(f"! mode_count={product.mode_count} but mode NAMES were not stored: the names found did not "
              f"add up to the count (oralb.py refuses to guess). Check the lines under the 'N modes' header.")
    return snippets


def report_missed(lines: list[str], snippets: list) -> None:
    """Find lines that mention a keyword but are in no snippet: possible missed facts."""
    heading("5. POSSIBLE MISSED FACTS (keyword lines that are in NO snippet)")
    captured = {s.text for s in snippets}
    # Feature-list snippets are stored WITHOUT their "TextFeature3:" label, so compare without it too.
    label = re.compile(r"^(TextFeature\d+|Shop By Feature):\s*")
    lines = [ln for ln in lines if label.sub("", ln) not in captured]
    # Ignore navigation, FAQ and legal sections far from the product facts.
    noise = re.compile(r"^(Shop|Learn|Support|Deals|Welcome|Skip|Sort by|Add to|Free Shipping)", re.I)
    shown = 0
    for kw in KEYWORDS:
        hits = [ln for ln in lines if re.search(rf"\b{re.escape(kw)}", ln, re.I)
                and ln not in captured and not noise.match(ln) and 15 <= len(ln) <= 220]
        if not hits:
            continue
        print(f"\n  keyword '{kw}': {len(hits)} uncaptured line(s)")
        for ln in hits[:3]:
            print(f"     - {short(ln, 100)}")
        shown += 1
    if not shown:
        print("  none: every keyword line is already captured.")
    print("\nMost of these are FAQ / warranty / legal text and are fine to ignore. Look for real facts")
    print("(battery run time, charge time, brush-head compatibility) and extend a regex in oralb.parse_product().")


def report_compare(fetcher: Fetcher) -> None:
    heading("COMPARE PAGE (supplementary evidence)")
    html = fetcher.get(oralb.COMPARE_URL)
    cols = oralb.parse_compare(html)
    print(f"Columns found in the downloaded HTML: {list(cols)}  (the other models load via JavaScript)")
    soup = BeautifulSoup(html, "lxml")
    options = {li.get("data-product-id") for li in soup.find_all("li", attrs={"data-product-id": True})}
    print(f"Models listed in the dropdown       : {sorted(o for o in options if o)}")
    for model, body in cols.items():
        print(f"\n[{model}]")
        for ln in body[:30]:
            print(f"   {short(ln)}")


def report_all(fetcher: Fetcher) -> None:
    heading("ALL MODELS: which fields are filled")
    urls = oralb.product_urls(*[fetcher.get(u) for u in oralb.LISTING_URLS])
    fields = ["price_usd", "mode_count", "modes", "pressure_sensor", "app_connectivity", "timer",
              "battery_life", "charging", "included_items", "warranty"]
    rows = []
    for url in urls:
        p, snips = oralb.parse_product(fetcher.get(url), url)
        rows.append((p, len(snips)))
    print(f"{len(rows)} models\n")
    print(f"{'model':<16}" + "".join(f"{f[:9]:>10}" for f in fields) + "  snippets")
    for p, n in rows:
        print(f"{p.product_id:<16}" + "".join(f"{'yes' if getattr(p, f) not in (None, []) else '-':>10}" for f in fields) + f"  {n:>5}")
    print("\nFill rate per field:")
    for f in fields:
        filled = sum(1 for p, _ in rows if getattr(p, f) not in (None, []))
        print(f"  {f:<18} {filled}/{len(rows)}")
    print("\n'-' means not found in sources. Fields that are '-' on many models are either not stated")
    print("on Oral-B pages (fine) or missed by a regex: run this script on one model to see section 5.")


# --------------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser(description="Explore an Oral-B product page.")
    ap.add_argument("url", nargs="?", default=DEFAULT_URL, help="Oral-B product URL")
    ap.add_argument("--compare", action="store_true", help="show the compare page")
    ap.add_argument("--all", action="store_true", help="fill-rate table for every model")
    args = ap.parse_args()

    fetcher = Fetcher()
    try:
        if args.all:
            report_all(fetcher)
        elif args.compare:
            report_compare(fetcher)
        else:
            html = fetcher.get(args.url)
            soup = BeautifulSoup(html, "lxml")
            print(f"URL: {args.url}")
            lines = report_basics(html, soup)
            report_jsonld(soup)
            report_structure(lines)
            snippets = report_coverage(html, args.url)
            report_missed(lines, snippets)
    finally:
        fetcher.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

## Insight from default run -all
======================================
======================================
# 8 models

# model            price_usd mode_coun     modes pressure_ app_conne     timer battery_l  charging included_  warranty  snippets
# OB-io6                 yes       yes       yes       yes       yes       yes         -       yes       yes       yes     37
# OB-io9-genius          yes       yes       yes       yes       yes       yes         -       yes       yes       yes     46
# OB-io7                 yes       yes       yes       yes       yes       yes         -       yes       yes       yes     44
# OB-io5                 yes       yes       yes       yes       yes       yes         -       yes       yes       yes     46
# OB-io3                 yes       yes       yes       yes         -       yes         -       yes       yes       yes     39
# OB-io4                 yes       yes       yes       yes       yes       yes         -       yes       yes       yes     38
# OB-io2                 yes       yes       yes       yes         -       yes       yes       yes       yes       yes     44
# OB-io8-twin            yes         -         -         -       yes         -         -       yes         -       yes     15

# Fill rate per field:
#   price_usd          8/8
#   mode_count         7/8
#   modes              7/8
#   pressure_sensor    7/8
#   app_connectivity   6/8
#   timer              7/8
#   battery_life       1/8
#   charging           8/8
#   included_items     7/8
#   warranty           8/8

# -------------------------------------------------------------------------------------------------------------------------
# Gaps by field (the fill-rate list):

# -> battery_life, 
#   - 1/8: only the iO2 states a figure ("1 charge lasts 4 weeks"). 
#   - Other pages say nothing numeric. The iO9 FAQ only says "long-lasting".
# -> app_connectivity, 
#   - 6/8: the iO3 and iO2 pages don't mention Bluetooth or an app. 
#   - That means "not found", not "no". The lower-end models may simply have no app, but the page text doesn't say so.
# -> mode_count, modes, pressure_sensor, timer, included_items: 7/8. The one miss is the same model in each case.

# The iO8 twin pack is the outlier. It has only 15 snippets, against about 40 for the others. It's missing modes, pressure sensor, timer and included items. The twin-pack page is thin, so the parser has less to work with.
# price_usd, charging and warranty are 8/8. Charging is filled everywhere, but as I noted for the iO9, the value can be the travel case line and not the charger.
