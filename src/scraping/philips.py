"""Philips Sonicare parser (https://www.usa.philips.com).

How the Philips website is built (what we learned)
--------------------------------------------------
- The listing page links to every product page; product URLs contain "/c-p/".
- Each product page contains:
    a) schema.org "Product" JSON-LD  -> name, SKU, price, short description.
    b) a big embedded JSON blob (inside `self.__next_f.push([...])` script tags) holding the full technical specification as
       chapter -> item -> value, e.g.
           chapter "Ease of use" -> item "Timer" -> value "BrushPacer and SmartTimer".
       It is part of the downloaded HTML, so no browser automation is needed.
- The JSON-LD also contains customer REVIEWS. Some contain injected HTML/links to unknown servers, 
  so we DELETE all review text and never use it 
  (Rationale [SECURITY]: it is untrusted user content and could also be a prompt-injection vector).

What this module returns
------------------------
`parse_product(html, url)` -> (Product, [Snippet, ...])
  * Snippet: verbatim spec lines, one per "item: value", used for citations.
  * Product: normalized fields, each linked to the snippet ids that prove it.
  If a spec item is missing on the page, the field stays None (= not found).
"""
from __future__ import annotations

import json
import re
from datetime import date
from urllib.parse import urljoin
from bs4 import BeautifulSoup

from src.common.schema import Product, Snippet

BASE = "https://www.usa.philips.com"
# The page the case study tells us to start from.
LISTING_URL = f"{BASE}/c-m-pe/electric-toothbrushes/latest"
BRAND = "Philips Sonicare"


def product_urls(listing_html: str) -> list[str]:
    """Return every product page URL found on the listing page (no duplicates)."""
    soup = BeautifulSoup(listing_html, "lxml")
    seen: dict[str, None] = {}  # a dict keeps first-seen order and removes duplicates
    for a in soup.find_all("a", href=True):
        if "/c-p/" in a["href"]:  # "/c-p/" marks a product detail page
            # Drop any "?query" part and make the link absolute.
            seen[urljoin(BASE, a["href"].split("?")[0])] = None
    return list(seen)


def _spec_blob(html: str) -> dict:
    """Pull the embedded specification JSON out of the page.

    Next.js ships page data as JavaScript string literals:
        <script>self.__next_f.push([1,"...escaped json..."])</script>
    We find the chunk that contains the spec, decode the JS string, 
    then read the JSON object that starts at `"specification":{"filters"...`.
    Returns {} if the page has no spec blob (the caller then gets None fields).
    """
    marker = '"specification":{"filters"'
    for m in re.finditer(r"self\.__next_f\.push\(\[1,(\".*?\")\]\)</script>", html, re.S):
        chunk = m.group(1)
        if "csChapter" not in chunk:  # cheap filter: only the spec chunk has this key
            continue
        try:
            decoded = json.loads(chunk)
        except json.JSONDecodeError:
            continue
        i = decoded.find(marker)
        if i < 0:
            continue
        # raw_decode reads exactly one JSON value and ignores the text after it
        obj, _ = json.JSONDecoder().raw_decode(decoded[decoded.find("{", i):])
        return obj
    return {}


def _product_jsonld(soup: BeautifulSoup) -> dict:
    """Return the schema.org Product JSON-LD, with reviews removed."""
    for t in soup.find_all("script", type="application/ld+json"):
        try:
            d = json.loads(t.string or "")
        except json.JSONDecodeError:
            continue
        if isinstance(d, dict) and d.get("@type") == "Product":
            # SECURITY: reviews are untrusted user text
            # Remove them completely so they can never reach the data.
            d.pop("review", None)
            return d
    return {}


def _clean(s: str) -> str:
    """Collapse runs of whitespace/newlines into single spaces."""
    return re.sub(r"\s+", " ", s).strip()


def parse_product(html: str, url: str) -> tuple[Product, list[Snippet]]:
    """Turn one Philips product page into a Product plus its source Snippets."""
    # Step 1: Read the page
    soup = BeautifulSoup(html, "lxml")
    ld = _product_jsonld(soup)

    # Step 2: Identity. SKU looks like "HX9903/15"; our id replaces "/" so it is URL/file safe.
    sku = ld.get("sku") or url.split("/c-p/")[1].split("/")[0].replace("_", "/")
    pid = "PH-" + sku.replace("/", "_")
    name = _clean(ld.get("name") or soup.title.string.split("|")[0])
    
    # Step 3: Initialise empty product 
    prod = Product(
        product_id=pid, brand=BRAND, name=name, sku=sku, source_url=url,
        captured_at=date.today().isoformat(),
    )
    snippets: list[Snippet] = []
    # add helper function
    def add(section: str, text: str) -> str:
        """Record a verbatim snippet and return its id (numbered per product)."""
        sid = f"{pid}#{len(snippets):03d}"
        snippets.append(Snippet(id=sid, product_id=pid, brand=BRAND, section=section,
                                text=_clean(text), source_url=url))
        return sid

    # Step 4: Series is only a convenience label guessed from the name (e.g. "DiamondClean Smart").
    # TODO: Should be a smarter extraction
    m = re.search(r"(DiamondClean(?: Smart)?|Prestige|ProtectiveClean|For Kids|\bONE\b|\d{4})", name, re.I)
    prod.series = m.group(1) if m else None

    # Step 5: Extract revelant entities
    # ---- price (from JSON-LD) ----------------------------------------------
    # Stored as a snippet too, with the capture date, because prices change.
    offer = ld.get("offers") or {}
    if offer.get("price") is not None:
        sid = add("Price", f"Price: ${offer['price']} {offer.get('priceCurrency', 'USD')} "
                           f"(as listed on {url} on {prod.captured_at})")
        prod.set("price_usd", float(offer["price"]), sid)

    # ---- marketing description (a "claim") -----------------------------------
    desc = ld.get("description")
    if desc:
        sid = add("Product description", desc)
        prod.claims.append(_clean(desc))
        prod.evidence.setdefault("claims", []).append(sid)

    # ---- technical specification -------------------------------------------
    # Walk chapter -> item -> value. Every item becomes one snippet like
    # "Timer: BrushPacer and SmartTimer". We also index them as
    # chapters[chapter][item] = (value_text, snippet_id) for easy lookup below.
    spec = _spec_blob(html)
    chapters: dict[str, dict[str, tuple[str, str]]] = {}
    for ch in spec.get("csChapter", []):
        cname = ch.get("csChapterName", "")
        for it in ch.get("csItem", []):
            vals = "; ".join(v.get("csValueName", "") for v in it.get("csValue", []))
            sid = add(f"{cname} > {it.get('csItemName', '')}", f"{it.get('csItemName', '')}: {vals}")
            chapters.setdefault(cname, {})[it.get("csItemName", "")] = (vals, sid)

    # Get helper function
    def get(chapter: str, item: str):
        """Look up (value, snippet_id) for a spec item, or None if the page lacks it."""
        return chapters.get(chapter, {}).get(item)

    # Step 6: Map Philips spec items onto our common fields. 
    # Each `if` only fires when the page really contains that item, otherwise the field stays None.
    if chapters.get("Modes"):  # each item name in the "Modes" chapter is one mode
        names = list(chapters["Modes"])
        sids = [s for _, s in chapters["Modes"].values()]
        prod.set("modes", names, sids)
        prod.set("mode_count", len(names), sids)
    if chapters.get("Intensities"):  # e.g. High / Medium / Low
        names = list(chapters["Intensities"])
        sids = [s for _, s in chapters["Intensities"].values()]
        prod.set("intensity_levels", f"{len(names)} ({', '.join(names)})", sids)
    if (v := get("Smart sensor technology", "Pressure feedback")):
        prod.set("pressure_sensor", True, v[1])   # listing the feedback implies a sensor
        prod.set("pressure_feedback", v[0], v[1])
    if (v := get("Compatibility", "Bluetooth wireless technology")):
        prod.set("app_connectivity", True, v[1])
        prod.set("app_details", v[0], v[1])
    if (v := get("Ease of use", "Timer")):
        prod.set("timer", v[0], v[1])
    if (v := get("Technical specifications", "Operating time (full to empty)")):
        prod.set("battery_life", v[0], v[1])
    if (v := get("Technical specifications", "Battery type")):
        prod.set("battery_type", v[0], v[1])
    elif (v := get("Technical specifications", "Battery")):  # fallback: "Rechargeable"
        prod.set("battery_type", v[0], v[1])
    if (v := get("Items included", "Charger")):
        prod.set("charging", v[0], v[1])
    if (v := get("Cleaning performance", "Speed")):
        prod.set("brushing_motion", f"Sonic: {v[0]}", v[1])
    if (v := get("Service", "Warranty")):
        prod.set("warranty", v[0], v[1])
    if chapters.get("Items included"):  # "Brush heads: 1 A3 All-in-One; ..." etc.
        items = [f"{k}: {val}" for k, (val, _) in chapters["Items included"].items()]
        prod.set("included_items", items, [s for _, s in chapters["Items included"].values()])
    # Marketing claims from the spec sheet (kept verbatim, including footnote stars).
    for key in ("Plaque removal", "Gum Health", "Whitening"):
        if (v := get("Cleaning performance", key)):
            prod.claims.append(f"{key}: {v[0]}")
            prod.evidence.setdefault("claims", []).append(v[1])

    # Step 7: Extra snippets for retrieval
    fs = soup.find(attrs={"data-component-id": "feature_section"})
    if fs:
        for p in fs.find_all(["h2", "h3", "p"]):
            t = _clean(p.get_text(" "))
            if len(t) > 25 and t != "Show more":  # skip tiny labels / buttons
                add("Feature section", t)
    return prod, snippets
