"""Secure, robots.txt-aware HTTP downloader with an on-disk cache.

File Purpose and Rationale
--------------------------
The assignment says: use only public pages, sites’ terms and  robots.txt, never bypass
logins/CAPTCHAs, and keep requests minimal.

What it does for every URL, in order
------------------------------------
1. Cache check   - if we already downloaded this URL, return the saved copy
                   (so re-running the pipeline sends zero requests).
2. robots.txt    - download and honour the site's robots.txt; refuse if the
                   path is disallowed for our user agent.
3. Throttle      - wait at least MIN_DELAY_SECONDS between hits to the same host.
4. Retry/backoff - on HTTP 429/503 or network errors, wait longer and retry.
5. Save          - write the HTML to data/raw/ so the next run uses the cache.

Example
-------
    fetcher = Fetcher()
    html = fetcher.get("https://oralb.com/en-us/products/compare/electric-toothbrushes")
    fetcher.close()
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from urllib import robotparser
from urllib.parse import urlparse

import httpx

# Identify ourselves honestly instead of pretending to be a browser.
USER_AGENT = "OCCI-CaseStudyBot/0.1 (public-page research; contact: jay.00174@live.com)"
# Minimum pause (seconds) between two requests to the same website.
MIN_DELAY_SECONDS = 2.0
# How many times to try a URL before giving up.
MAX_RETRIES = 3

# Downloaded pages are stored here (this folder is git-ignored).
# parents[2] = repo root (this file is src/scraping/fetcher.py).
RAW_DIR = Path(__file__).resolve().parents[2] / "data" / "raw"
# Maps each cached file name to the URL it came from (stored inside the cache folder).
URL_MAP_NAME = "url_map.json"


class RobotsDisallowed(Exception):
    """Raised when a site's robots.txt does not allow us to fetch a URL."""


class Fetcher:
    """Downloads pages responsibly. Create one, call get() many times, then close()."""

    def __init__(self, cache_dir: Path = RAW_DIR, delay: float = MIN_DELAY_SECONDS):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.delay = delay
        # host -> time of our last request to it (used for throttling)
        self._last_hit: dict[str, float] = {}
        # "https://host" -> parsed robots.txt (downloaded once per host)
        self._robots: dict[str, robotparser.RobotFileParser] = {}
        self._client = httpx.Client(
            headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"},
            follow_redirects=True,
            timeout=30,
        )
        self._url_map = self._load_url_map()

    def _load_url_map(self) -> dict[str, str]:
        """Read url_map.json ({file name: url}); an empty or damaged file starts a fresh map."""
        path = self.cache_dir / URL_MAP_NAME
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass
        return {}

    def _remember_url(self, url: str, path: Path) -> None:
        """Record file name -> url in url_map.json (writes only when something changed)."""
        if self._url_map.get(path.name) != url:
            self._url_map[path.name] = url
            (self.cache_dir / URL_MAP_NAME).write_text(
                json.dumps(dict(sorted(self._url_map.items())), indent=2), encoding="utf-8")

    def _cache_path(self, url: str) -> Path:
        """Each URL maps to a stable file name (hash of the URL)."""
        return self.cache_dir / (hashlib.sha256(url.encode()).hexdigest()[:24] + ".html")

    def _allowed(self, url: str) -> bool:
        """True if robots.txt lets our user agent fetch this URL."""
        # rebuilds just the site root.
        # Example: For https://oralb.com/en-us/products/x/, p.scheme is https and p.netloc is oralb.com
        p = urlparse(url)
        base = f"{p.scheme}://{p.netloc}"
        rp = self._robots.get(base)
        if rp is None:  # first time we see this host: download its robots.txt
            rp = robotparser.RobotFileParser()
            try:
                resp = self._client.get(base + "/robots.txt")
                # A missing robots.txt (non-200) means "no restrictions".
                rp.parse(resp.text.splitlines() if resp.status_code == 200 else [])
                if resp.status_code == 200:  # keep a copy of the rules we followed
                    robots_path = self._cache_path(base + "/robots.txt").with_suffix(".txt")
                    robots_path.write_text(resp.text, encoding="utf-8", newline="\n")
                    self._remember_url(base + "/robots.txt", robots_path)
            except httpx.HTTPError:
                rp.parse([])
            self._robots[base] = rp
        return rp.can_fetch(USER_AGENT, url)

    def get(self, url: str, use_cache: bool = True) -> str:
        """Return the page HTML for `url` (from cache if available).

        Pass use_cache=False to force a fresh download.
        Raises RobotsDisallowed if robots.txt forbids the URL.
        """
        path = self._cache_path(url)
        # Step 1: Cache check: if the page was downloaded before, it returns the saved cop
        if use_cache and path.exists():
            self._remember_url(url, path)  # also maps pages cached before the map existed
            return path.read_text(encoding="utf-8")
        #Step 2: robots.txt check
        if not self._allowed(url):
            raise RobotsDisallowed(url)

        host = urlparse(url).netloc
        for attempt in range(MAX_RETRIES):
            # Step 3: Throttle: sleep until `delay` seconds have passed since our last hit.
            wait = self.delay - (time.monotonic() - self._last_hit.get(host, 0))
            if wait > 0:
                time.sleep(wait)
            self._last_hit[host] = time.monotonic()
            # Step 4: Download with retries
            try:
                resp = self._client.get(url)
                if resp.status_code in (429, 503):  # "slow down" -> back off, retry
                    time.sleep(self.delay * 2 ** (attempt + 1))
                    continue
                resp.raise_for_status()
                path.write_text(resp.text, encoding="utf-8")
                self._remember_url(url, path)
                return resp.text
            except httpx.HTTPError:
                if attempt == MAX_RETRIES - 1:
                    raise  # out of retries: let the caller decide what to do
                time.sleep(self.delay * 2 ** (attempt + 1))  # exponential backoff
        raise RuntimeError(f"Failed to fetch {url}")

    def close(self) -> None:
        """Release the underlying HTTP connection."""
        self._client.close()
