# Scraper: how we scrape and what we learned

This document explains how the scraper works, what each website looks like
underneath, and which gaps are known. It is based on what was actually run
against the pages on 2026-11-20. Code lives in [`src/scraping/`](../../src/scraping/);
the compliance check is in [`robots-txt-check.md`](robots-txt-check.md).

## 1. How we scrape (end to end)

```
Listing pages ──► product URLs ──► download (cached) ──► parse ──► Product + Snippets ──► files
  (2 sites)       philips.py /       fetcher.py            philips.py /     schema.py        pipeline/
                  oralb.py                                 oralb.py                          build_dataset.py
```

1. **Find product URLs.** `product_urls()` reads each brand's listing pages and
   returns the product page links.
   - Philips: one listing page, links containing `/c-p/` (12 products found).
   - Oral-B: five category pages (`?page=` up to 4) plus the twin-packs page.
     Colour variants collapse to one URL per model (8 models found).
2. **Download politely.** Every page goes through `Fetcher.get()` (the only code
   that touches the internet). In order: cache check, robots.txt check, 2 second
   throttle per site, retry with backoff on 429/503 or network errors, then save.
3. **Parse.** `parse_product(html, url)` returns a `Product` (normalized fields)
   and a list of `Snippet`s (verbatim source text). Every filled field records the
   snippet ids that prove it.
4. **Enrich.** The Oral-B compare page is attached as an extra snippet and fills
   battery life and charging where the product page says nothing.
5. **Write outputs.** `build_dataset.py` writes `outputs/comparison_table.csv`,
   `data/products.json` and `data/snippets.jsonl`. `build_stores.py` then loads
   SQLite and Chroma.

Result of the last run: 12 Philips Sonicare + 8 Oral-B products, 722 snippets.

### Run it

```powershell
python -m src.pipeline.build_dataset            # uses the cache; no requests if fully cached
python -m src.pipeline.build_dataset --refresh  # re-download everything (throttled)
python -m src.pipeline.build_stores             # load SQLite + Chroma
```

## 2. The fetcher and the raw cache

`src/scraping/fetcher.py` enforces the scraping rules in one place:

| Step | What happens |
|---|---|
| Cache check | A page already downloaded is returned from `data/raw/` with no request. |
| robots.txt | Downloaded once per site; a disallowed URL raises `RobotsDisallowed`. |
| Throttle | At least 2 seconds between requests to the same site. |
| Retry | Up to 3 attempts; waits 2, 4, 8 seconds after a 429/503 or network error. |
| Save | The HTML is written to the cache. |

`data/raw/` (git-ignored) contains:

| File | Meaning |
|---|---|
| `<24-char hash>.html` | One downloaded page. The name is the first 24 characters of the SHA-256 of the exact URL. |
| `url_map.json` | `{hash file name: url}` so every cache file can be traced back to its page. |
| `<hash>.txt` | The robots.txt of each site, saved exactly as downloaded. |

The same URL always maps to the same file, but any change to the URL (a trailing
slash, a `?query`) is a different file. Everything downstream can be rebuilt from
`data/raw/` with no new requests.

## 3. Philips Sonicare pages

**Source:** `https://www.usa.philips.com/c-m-pe/electric-toothbrushes/latest` and
the `/c-p/...` product pages it links to.

Pages are large (the DiamondClean 9300 page is about 1.6 million characters) but
they are plain HTML, so no browser automation is needed. Each product page has two
useful data sources:

1. **JSON-LD** (a `<script type="application/ld+json">` tag): name, SKU, price,
   description. It also contains customer reviews (see the security note below).
2. **A spec blob** inside a `self.__next_f.push([1,"..."])` script. It is a
   backslash-escaped JavaScript string holding the full spec sheet as
   chapter, item and value. `_spec_blob()` un-escapes it and reads the
   `specification` object.

Example (real), the Timer item after decoding:

```json
{"csItemName": "Timer",
 "csValue": [{"csValueName": "BrushPacer and SmartTimer"}]}
```

becomes the snippet `Timer: BrushPacer and SmartTimer` (id `PH-HX9903_15#019`),
and sets `timer` on the product with that id as evidence.

The DiamondClean 9300 spec has 12 chapters and 35 items. Because Philips gives a
structured table, parsing is exact: a field is filled only when its named spec
item exists.

### Insights from scanning all 12 products (`analyze_philips_page.py --all`)

- Near universal items (10 to 12 of 12): Color, Handle, Operating time, Battery,
  Battery indicator, Charger, Warranty, Battery type.
- Items on about half the products: Timer (8), Pressure feedback (6), Bluetooth
  and app features (5). This is why some columns are blank for some products.
- The three entry-level products (the kids brush and the two Philips One brushes)
  have no Modes chapter at all.
- Model-specific items appear on 1 product only (SenseIQ, Stickers, AAA Alkaline)
  and make poor comparison columns.
- **The same fact has different names on different products:** Timer is under
  `Ease of use` on 8 products and also under `Cleaning performance` on 2.
  Pressure appears as `Smart sensor technology > Pressure feedback` (6),
  `Cleaning performance > Pressure feedback` (1) and
  `Smart sensor technology > Pressure sensor` (1). Modes use both `White` and
  `White+`. The parser reads one location per fact, so some blank fields may be
  wrong. Which products are affected has not been checked.

### Security note: injected review content

The JSON-LD reviews on the 9300 page include entries with injected HTML, for
example a `<form action='http://96.62.143.209:8099/...'>` and an `<a href=...>`
pointing at the same raw-IP server (2 of the 5 embedded reviews). The parser
deletes the `review` key before anything else sees it, and the URL appears nowhere
in the dataset (checked by `tests/test_dataset.py`). Treat all scraped text as
untrusted data, never as instructions. The guardrails and the evaluation suite
should include a prompt-injection case built from this.

## 4. Oral-B pages

**Sources:** the compare page
`https://oralb.com/en-us/products/compare/electric-toothbrushes`, five category
pages, the twin-packs page, and the product pages they link to.

Oral-B has **no spec table**. Product pages are server-rendered marketing prose
(about 600,000 characters, with 51% of the visible text lines being repeats because
blocks are duplicated for tabs and mobile). Only the JSON-LD (name, SKU, GTIN,
price) is structured, and it has no reviews and says nothing about modes, timer or
battery. So `oralb.py`:

1. Keeps useful text lines verbatim as snippets (description block, Oral-B's own
   "Feature list", "What's included", warranty).
2. Reads facts with conservative regular expressions (mode count and names, pressure
   sensor, app, timer, battery, charging, warranty). A value is set only when a regex
   matches real text; the matching snippet id is the evidence. No match means the
   field stays empty.
3. Keeps mode names only when the names found equal the stated count. If they
   disagree, only the count is kept.

### Model handling

- Each model is sold in several colours with separate URLs. The series number is
  captured from the URL and one URL per model is kept (single brush preferred over
  a twin pack).
- The model identity is taken from the product **name**, because the plain iO9 URL
  serves the "iO Series 9 Genius" page. They are counted as one model.
- **iO8 is only sold as a Twin Pack**, so it is kept with a note that the price is
  for two brushes. It has the thinnest data (15 snippets against about 40 for the
  others).

### The compare page

Only three columns (iO9, iO6, iO2) are in the downloaded HTML; the others load with
JavaScript. The dropdown lists models iO2 through iO10. It is used as supplementary
evidence for battery life and charging.

Two real discrepancies seen in it:

- **Price:** the compare page shows the iO9 at $399.99 marked down to $260, while
  the product page says $329.99. The pipeline uses the product-page price.
- **Modes:** the compare page lists "Smart Adapt" next to seven modes, while the
  product page says "7 modes".

### Fill rates (8 Oral-B models, product pages only)

| Field | Filled |
|---|---|
| price, charging, warranty | 8/8 |
| mode count, modes, pressure sensor, timer, included items | 7/8 |
| app connectivity | 6/8 |
| battery life | 1/8 (only iO2: "1 charge lasts 4 weeks") |

An empty cell means "not found in sources", not "no". The final dataset adds the
compare page, which gives battery life "Long-Lasting" for iO9, iO6 and iO2. That is
not a measurable value.

## 5. Developer tools

| Script | Purpose |
|---|---|
| `scripts/analyze_philips_page.py [url] [--all] [--dump-spec FILE]` | Shows JSON-LD, the full spec table, which spec items the parser uses or ignores, and (with `--all`) how often each spec item appears. |
| `scripts/analyze_oralb_page.py [url] [--compare] [--all]` | Shows the text structure, which line filled each field, lines that mention a fact but are in no snippet, and a fill-rate table. |
| `scripts/query_stores.py` | Queries SQLite (read-only) and Chroma, and traces citations. |

Run these before changing a parser, so you see what the page really contains.

## 6. Known gaps and risks

Items marked "confirmed" were reproduced; the others are suspected and not yet
tested.

**Philips**
- One location per fact, so timer, pressure and mode names can be missed (see
  section 3). Confirmed from the frequency table; affected products not listed.
- Claim footnotes are not captured. Claims such as `20x more effective*` are stored
  without the footnote that gives the comparison basis, which could mislead if quoted
  alone. The page has a `disclaimer` component that is not read. Confirmed.
- Most of the visible page is not read. Only the `feature_section` block is. Components
  such as `parts_accessories_cards` (compatible brush heads), `card_*` and
  `business_section_*` are not captured. Other JSON-LD fields (image, availability,
  rating) are not stored. Confirmed.
- `pressure_sensor` is set to `True` because a "Pressure feedback" item exists. The
  page does not literally say "has a pressure sensor".

**Oral-B**
- The lead description paragraph ("...micro-vibrations...") is missing from the
  snippets, so `brushing_motion` is empty and `claims` skip it. Suspected cause: line
  de-duplication keeps the copy outside the "More Information" block. Not verified.
- "First match wins" can pick a weaker line. For the iO9, `charging` takes the travel
  case line and not the Magnetic Charger, and `app_connectivity` takes the
  "3D Teeth Tracking" line and not "Bluetooth Connectivity". Confirmed.
- FAQ answers (for example "How long does an iO Series 9 battery last?") are not
  captured as snippets. The answer gives no number.
- `intensity_levels` is never filled for Oral-B because the parser does not try. It
  has not been checked whether Oral-B pages state intensity levels at all. If they do
  not, the field should stay empty and not be inferred from mode names.
- Only "iO series" URLs are recognized, so any other Oral-B electric brush would be
  skipped. The compare dropdown lists an iO10 but no iO10 page was found; the search
  for it was not done.

**Fetcher**
- It fails open: if robots.txt cannot be downloaded (server error or network failure)
  the site is treated as having no rules. The stricter reading of the standard (RFC
  9309) is to assume the site is off-limits.
- The user agent contact (`contact: candidate`) is a placeholder and tells a site
  owner nothing useful.
- Two cached pages in `data/raw/` are not recorded in `url_map.json` (leftovers from
  early exploration). They are not used by the pipeline.

**General**
- Prices are a snapshot; check `captured_at`.
- If a site changes its layout, a field goes blank (a regex or spec name stops
  matching) rather than wrong, but nothing alerts you. Run the analysis scripts after
  a refresh and compare fill rates.
