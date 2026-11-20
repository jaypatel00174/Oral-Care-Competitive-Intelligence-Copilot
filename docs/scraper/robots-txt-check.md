# Scraper docs: robots.txt check

What we checked before scraping, and what we found. The code that enforces this
is `Fetcher._allowed()` in [`src/scraping/fetcher.py`](../../src/scraping/fetcher.py).

Checked on: 2026-11-20, using our own user agent (`USER_AGENT` in `fetcher.py`).

## Result

Both sites publish a robots.txt, and every page we use is allowed.

| Site | robots.txt URL | Status | Size |
|---|---|---|---|
| Philips (US) | `https://www.usa.philips.com/robots.txt` | 200 (exists) | 14,823 bytes |
| Oral-B (US) | `https://oralb.com/robots.txt` | 200 (exists) | 2,062 bytes |

## Pages tested against the rules

| Site | URL | Allowed? |
|---|---|---|
| Philips | `/c-m-pe/electric-toothbrushes/latest` (listing) | Yes |
| Philips | `/c-p/HX9903_15/diamondclean-smart-9300-rechargeable-toothbrush` (product) | Yes |
| Oral-B | `/en-us/products/compare/electric-toothbrushes` (compare page) | Yes |
| Oral-B | `/en-us/products/electric-toothbrushes/electric-toothbrushes/` (category listing) | Yes |
| Oral-B | `/en-us/products/electric-toothbrushes/oral-b-io-series-9-genius-...-black-onyx/` (product) | Yes |
| Oral-B | `/en-us/products/io-series-8-twin-pack-black-onyx-black-onyx/` (twin pack) | Yes |

Only these representative URLs were tested by hand. The other cached pages were
not listed one by one, but `Fetcher.get()` runs the same check before every
real download.

## What each site blocks (and we never touch)

- **Philips:** `/content/`, `/shop/c/*`, `/healthcare/crsc/` and several
  healthcare help pages.
- **Oral-B:** account, cart and checkout pages (`/cart.php`, `/checkout`,
  `/login.php`, `/account.php`, `/orderstatus.php`, `/search.php`), some
  utility paths, and URLs with the `_bc_fsnf=1` parameter.

## Things to be aware of

- Oral-B's robots.txt names many AI crawlers (GPTBot, ClaudeBot, CCBot and
  others). Our user agent is not one of them, so the general rules for all
  crawlers apply, and those allow our pages. That is valid under robots.txt, but
  it shows the site is cautious about automated collection. We keep the volume
  low for that reason.
- **Fail-open behavior:** if robots.txt cannot be downloaded (server error or
  network failure), `_allowed()` treats the site as having no rules. A missing file
  (404) really does mean no rules, but for a server error the stricter reading of
  the standard (RFC 9309) is to assume the site is off-limits. It did not matter
  here because both files loaded, but it is a known gap in the code.

## How we keep the load small

- About 35 pages in total, downloaded once and cached in `data/raw/`.
- At least 2 seconds between requests to the same site.
- Retries back off (2, 4, 8 seconds) on "slow down" responses.
- No logins, CAPTCHAs, paywalls or rate limits are bypassed.
