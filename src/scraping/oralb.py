"""Oral-B parser (https://oralb.com/en-us).

How the Oral-B website differs from Philips
-------------------------------------------
- There is NO structured spec table. Product pages are marketing prose
  ("A built-in 2-minute timer, with quadrant pacing ...").
- So we (a) keep the useful text lines verbatim as snippets and (b) use
  small, conservative regular expressions to read facts out of them
  (mode count, timer, pressure sensor, ...).
- The Oral-B (COMPARE_URL) has a cleaner labelled layout, but only 3
  models (iO9, iO6, iO2) are in the downloaded HTML; the rest load via
  JavaScript. We use it as extra evidence for those three.

Model naming Rationale
-----------------------
Oral-B sells each model in many colours, each with its own URL. We collapse
colours into one product per model ("iO Series 9", "iO Series 9 Genius", ...).
iO8 is only sold as a Twin Pack, so it is kept but flagged in `notes`.
"""
from __future__ import annotations

import json
import re
from datetime import date
from urllib.parse import urljoin
from bs4 import BeautifulSoup

from src.common.schema import Product, Snippet

BASE = "https://oralb.com"
COMPARE_URL = f"{BASE}/en-us/products/compare/electric-toothbrushes"

# Category pages that together list the individual product pages
# (page 1-4 of the main list, plus twin packs/bundles for iO8).
LISTING_URLS = [
    f"{BASE}/en-us/products/electric-toothbrushes/electric-toothbrushes/{q}"
    for q in ("", "?page=1", "?page=2", "?page=3", "?page=4")
] + [f"{BASE}/en-us/products/electric-toothbrushes/twin-packs-and-bundles/"]
BRAND = "Oral-B"

# Brushing-mode names we recognise. Left = clean name we store,
# right = regex that finds it in page text (case-insensitive).
MODE_VOCAB = {
    "Daily Clean": r"daily clean",
    "Whitening": r"whiten(?:ing)?",                       # pages say both "Whiten" and "Whitening"
    "Gum Care": r"gum care",
    "Sensitive": r"(?<!super[ -])sensitive(?! teeth| gums)",  # not "Super Sensitive", not "sensitive teeth"
    "Super Sensitive": r"super[ -]sensitive",
    "Intense": r"intense(?: clean)?",
    "Tongue Clean": r"tongue clean",
    "Smart Adapt": r"smart adapt",
}
# Product URL shapes: single brush ".../oral-b-io-series-6-..." and
# twin pack ".../io-series-8-twin-pack-...". Group 1 captures the series number.
_SINGLE_RE = re.compile(r"/products/electric-toothbrushes/oral-b-io-series-(\d+)(?:-[a-z0-9-]*)?/$")
_TWIN_RE = re.compile(r"/products/io-series-(\d+)-twin-pack-[a-z0-9-]+/$")


def _clean(s: str) -> str:
    """Tidy text: turn non-breaking hyphens into '-' and collapse whitespace."""
    return re.sub(r"\s+", " ", s.replace("\u2011", "-")).strip()


def product_urls(*listing_htmls: str) -> list[str]:
    """Pick ONE product URL per model from the listing pages.
    Design decisions:
    1. Colour variants share a series number, so we keep the first URL per number.
    2. A single-brush page is preferred; a twin pack is used only when no single brush exists for that series (in case of iO8).
    """
    singles: dict[str, str] = {}
    twins: dict[str, str] = {}
    for html in listing_htmls:
        for a in BeautifulSoup(html, "lxml").find_all("a", href=True):
            href = urljoin(BASE, a["href"].split("?")[0])
            if (m := _SINGLE_RE.search(href)):
                singles.setdefault(m.group(1), href)  # setdefault = keep the first one
            elif (m := _TWIN_RE.search(href)):
                twins.setdefault(m.group(1), href)
    out = list(singles.values())
    out += [u for n, u in twins.items() if n not in singles]
    return out


def _jsonld_product(soup: BeautifulSoup) -> dict:
    """Return the schema.org Product JSON-LD (name, sku, price), reviews removed."""
    for t in soup.find_all("script", type="application/ld+json"):
        try:
            d = json.loads(t.string or "")
        except json.JSONDecodeError:
            continue
        if isinstance(d, dict) and d.get("@type") == "Product":
            d.pop("review", None)  # SECURITY: droped as reviews are untrusted user content
            return d
    return {}


def _model_from_name(name: str) -> tuple[str, str]:
    """Work out the model from the product NAME (not the URL).

    "Oral-B iO Series 9 Genius ..., Black Onyx" -> ("io9-genius", "iO Series 9 Genius").
    We use the name because two different URLs can serve the same product page
    (the plain iO9 URL shows the Genius page), and names tell the correct one.
    """
    m = re.search(r"iO\s*Series\s*(\d+)(\s+Genius)?", name, re.I)
    if not m:  # unknown naming: fall back to a slug of the name
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:30]
        return slug, name
    num, genius = m.group(1), bool(m.group(2))
    return f"io{num}{'-genius' if genius else ''}", f"iO Series {num}{' Genius' if genius else ''}"


def _page_lines(soup: BeautifulSoup) -> list[str]:
    """Turn the page into a clean list of visible text lines.

    Two fixes are applied:
    - Oral-B prints a label and its value on separate lines
      ("Shop By Feature:" / "7 SMART Brushing Modes"); we join them.
    - The same block is repeated several times (desktop/mobile/tabs); we drop repeated lines so snippets are not duplicated.
    """
    for t in soup(["script", "style", "noscript"]):  # remove non-visible content
        t.decompose()
    raw = [_clean(x) for x in soup.get_text("\n", strip=True).split("\n")]
    joined: list[str] = []
    i = 0
    while i < len(raw):
        ln = raw[i]
        if ln.endswith(":") and re.match(r"^(TextFeature\d+|Shop By Feature)\s*:$", ln) and i + 1 < len(raw):
            joined.append(f"{ln} {raw[i + 1]}")  # merge label + next line
            i += 2
            continue
        joined.append(ln)
        i += 1
    seen: set[str] = set()
    out = []
    for ln in joined:
        if ln and ln not in seen:
            seen.add(ln)
            out.append(ln)
    return out


def parse_product(html: str, url: str) -> tuple[Product, list[Snippet]]:
    """Turn one Oral-B product page into a Product plus its source Snippets."""
    # Step 1: Read the Page
    soup = BeautifulSoup(html, "lxml")
    ld = _jsonld_product(soup)
    name = _clean(ld.get("name", "")) or _clean(soup.title.string.split("|")[0])
    lines = _page_lines(soup)

    # Step 2: Identity
    key, series = _model_from_name(name)
    is_twin = "twin pack" in name.lower()
    pid = f"OB-{key}" + ("-twin" if is_twin else "")

    # Step 3: Initialise empty product 
    prod = Product(
        product_id=pid, brand=BRAND, name=name, series=series,
        sku=str(ld["sku"]) if ld.get("sku") else None,
        source_url=url, captured_at=date.today().isoformat(),
    )
    if is_twin:  # warn readers that the price covers two brushes
        prod.notes = "Twin pack (2 handles). Price is for the pack, not a single brush."
    snippets: list[Snippet] = []
    
    # add helper function
    def add(section: str, text: str) -> str:
        """Record a verbatim snippet and return its id (numbered per product)."""
        sid = f"{pid}#{len(snippets):03d}"
        snippets.append(Snippet(id=sid, product_id=pid, brand=BRAND, section=section,
                                text=_clean(text), source_url=url))
        return sid

    # Step 4: Extract revelant entities
    # ---- price (from JSON-LD) ------------------------------------------------
    offer = ld.get("offers") or {}
    if offer.get("price"):
        sid = add("Price", f"Price: ${offer['price']} {offer.get('priceCurrency', 'USD')} "
                           f"(as listed on {url} on {prod.captured_at})")
        prod.set("price_usd", float(offer["price"]), sid)
    if prod.notes:
        add("Note", prod.notes)

    # ---- description block ---------------------------------------------------
    # The descriptive text sits between the "More Information" heading and the warranty FAQ.
    except ValueError:
        start = 0
    end = next((i for i in range(start, len(lines)) if lines[i].startswith("What is covered in my")), len(lines))
    desc: list[tuple[str, str]] = []  # (snippet_id, text) for the description lines
    for ln in lines[start:end]:
        # skip tiny lines, bullet/footnote lines and the dimensions line
        if len(ln) < 12 or ln.startswith(("•", "*")) or "Height:" in ln:
            continue
        desc.append((add("Product description", ln), ln))

    # ---- Feature list -----------------------------------------------------
    # Oral-B's own short feature list ("7 SMART Brushing Modes", "1-Magnetic Charger", ...)
    for ln in lines:
        if re.match(r"^(TextFeature\d+|Shop By Feature):\s+\S", ln):
            add("Feature list", ln.split(":", 1)[1])

    # ---- what's included -----------------------------------------------------
    # The item list follows the "Height: ... Weight: ..." dimensions line, e.g.
    # "(1) Travel case", "(1) Charger". We stop at the first bullet/FAQ line.
    for i, ln in enumerate(lines):
        if "Height:" in ln:
            items: list[tuple[str, str]] = []
            for nxt in lines[i + 1 : i + 9]:
                if nxt.startswith(("•", "What is covered", "How do I")):
                    break
                if "Weight:" in nxt or nxt.startswith("only)"):  # wrapped dimension text
                    continue
                items.append((add("What's included", nxt), nxt))
            # The handle itself is listed at the start of the dimensions line: "(1) Oral-B iO6 ... Height: ..."
            first = re.sub(r"\s*Height:.*$", "", ln)
            if re.match(r"^\(\d+\)", first):
                items.insert(0, (add("What's included", first), first))
            if items:
                prod.set("included_items", [t for _, t in items], [s for s, _ in items])
            break

    # Fallback for pages without a dimensions line: use feature-list entries that
    # start with a quantity ("4 refill heads", "1-Magnetic Charger"), excluding modes.
    if prod.included_items is None:
        feats = [s for s in snippets if s.section == "Feature list" and re.match(r"^\(?\d+\)?[- ]", s.text)]
        feats = [s for s in feats if not re.search(r"modes?\b", s.text, re.I)]
        if feats:
            prod.set("included_items", [s.text for s in feats], [s.id for s in feats])

    # ---- feature extraction --------------------------------------------------
    # `pool` = the prose snippets we search. 
    # `first(regex)` returns the FIRST
    # snippet matching the regex as (snippet_id, text, match) or None. The
    # snippet id becomes the evidence for whatever we extract from it.
    pool = [(s.id, s.text) for s in snippets if s.section in ("Product description", "Feature list")]

    def first(pattern: str):
        rx = re.compile(pattern, re.I)
        for sid, text in pool:
            if (m := rx.search(text)):
                return sid, text, m
        return None

    # Modes. 
    # Step 1: read the COUNT from text like "5 SMART CLEANING MODES".
    # Step 2: read the NAMES from that line and the next one. 
    # We only keep the names when they add up to the stated count.
    if (hit := first(r"(\d+)\s+(?:smart\s+)?(?:cleaning|brushing)\s+(?:settings/brushing\s+)?(?:modes|settings)")):
        sid, _, m = hit
        count = int(m.group(1))
        prod.set("mode_count", count, sid)
        for idx, (s, t) in enumerate(desc):
            if not re.search(rf"\b{count}\b[^.]*\b(?:modes|settings)\b", t, re.I):
                continue
            window = desc[idx : idx + 2]  # header line + the line after it
            text = " ".join(x for _, x in window)
            names = [c for c, rx in MODE_VOCAB.items() if re.search(rx, text, re.I)]
            if len(names) == count:
                prod.set("modes", names, [x for x, _ in window])
                break

    # Pressure sensor: only True when the text says so (never set to False).
    if (hit := first(r"pressure sensor")):
        prod.set("pressure_sensor", True, hit[0])
        prod.set("pressure_feedback", hit[1], hit[0])

    # App / Bluetooth connectivity.
    if (hit := first(r"bluetooth|oral-b app|connected coach|3d teeth tracking")):
        prod.set("app_connectivity", True, hit[0])
        prod.set("app_details", hit[1], hit[0])

    # Timer, e.g. "a built-in 2-minute timer ...".
    if (hit := first(r"\d[- ]min(?:ute)?[^.]{0,40}timer|timer[^.]{0,40}\d[- ]min|2[- ]minute")):
        prod.set("timer", hit[1], hit[0])

    # Battery life, e.g. "1 charge lasts 4 weeks". Most Oral-B pages do not state this.
    if (hit := first(r"charge lasts|battery life|battery[^.]{0,30}(?:weeks?|days?)")):
        prod.set("battery_life", hit[1], hit[0])

    # Charging hardware (magnetic charger, travel case, ...).
    if (hit := first(r"magnetic charger|charging (?:travel )?case|full charge|\bcharger\b")):
        prod.set("charging", hit[1], hit[0])

    # Cleaning technology wording (micro-vibrations, round head, iO drive).
    if (hit := first(r"micro-?vibrations|oscillat|round brush head|iO drive")):
        prod.set("brushing_motion", hit[1], hit[0])

    # Warranty: "warranty ... runs for 2 years" is in the FAQ part of the page.
    for ln in lines:
        if (m := re.search(r"warranty starts on the day of purchase and runs for (\d+ years?)", ln, re.I)):
            prod.set("warranty", f"{m.group(1)} limited warranty", add("Warranty", ln))
            break

    # Marketing claims = first three description lines (quoted as written).
    for sid, text in desc[:3]:
        prod.claims.append(text)
        prod.evidence.setdefault("claims", []).append(sid)
    return prod, snippets


def parse_compare(html: str) -> dict[str, list[str]]:
    """Read the compare page and return {'io9': [text lines...], 'io2': [...]}.

    Rationale: Only 3 columns exist in the downloaded HTML (the others load by JavaScript),
    so this is supplementary evidence. The pipeline attaches these lines as an
    extra "Compare page" snippet and uses them to fill battery life / charging.
    """
    soup = BeautifulSoup(html, "lxml")
    out: dict[str, list[str]] = {}
    for col in soup.select("div.product-comparison-list"):  # one div per compared product
        lines = [_clean(x) for x in col.get_text("\n", strip=True).split("\n") if x.strip()]
        # The model name appears as e.g. "iO9 Electric Toothbrush"; use it as the key.
        head = next((l for l in lines if re.match(r"^iO\d+ Electric Toothbrush$", l)), None)
        if not head:
            continue
        model = "io" + re.search(r"\d+", head).group(0)
        # Remove the dropdown's list of model names and the arrow glyph.
        body = [l for l in lines if not re.match(r"^iO\d+ Electric Toothbrush$|^▾$", l)]
        out.setdefault(model, body)  # the page repeats columns; keep the first
    return out
