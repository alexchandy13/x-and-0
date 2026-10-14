"""Shared HTTP fetching, caching and parsing helpers for the ratings scrapers.

All four sources are WordPress sites that render ratings into static HTML, so
scraping is a matter of being polite and parsing tables. Two of them
(2kratings.com, sofifa.com) sit behind a Cloudflare managed challenge; see
basketball.py for how that one is handled.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
CACHE_DIR = REPO_ROOT / ".cache"

# A browser-like UA is required: these hosts return 403 to default client UAs.
# Override with X_AND_0_UA if you want to identify your crawler differently.
USER_AGENT = os.environ.get(
    "X_AND_0_UA",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
)

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Upgrade-Insecure-Requests": "1",
}


class Fetcher:
    """Polite HTTP client with on-disk caching and retries.

    The cache is keyed on the URL and never expires, so a re-run after a roster
    update only costs requests for pages you explicitly drop from `.cache/`.
    Use `refresh=True` to bypass it entirely.
    """

    def __init__(self, namespace: str, delay: float = 1.0, refresh: bool = False,
                 timeout: int = 30, retries: int = 3):
        self.cache_dir = CACHE_DIR / namespace
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.delay = delay
        self.refresh = refresh
        self.timeout = timeout
        self.retries = retries
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self._last_request = 0.0

    def _cache_path(self, url: str) -> Path:
        digest = hashlib.sha1(url.encode()).hexdigest()[:16]
        slug = re.sub(r"[^a-z0-9]+", "-", url.lower().split("//")[-1])[-60:].strip("-")
        return self.cache_dir / f"{slug}-{digest}.html"

    def get(self, url: str, timeout: int | None = None) -> str | None:
        """Return page HTML, or None if it could not be fetched."""
        path = self._cache_path(url)
        if not self.refresh and path.exists():
            return path.read_text(encoding="utf-8", errors="replace")

        for attempt in range(1, self.retries + 1):
            elapsed = time.monotonic() - self._last_request
            if elapsed < self.delay:
                time.sleep(self.delay - elapsed)
            self._last_request = time.monotonic()
            try:
                resp = self.session.get(url, timeout=timeout or self.timeout,
                                        allow_redirects=True)
            except requests.RequestException as exc:
                log(f"  ! {url} attempt {attempt}: {type(exc).__name__}")
                time.sleep(2 ** attempt)
                continue

            if resp.status_code == 200:
                path.write_text(resp.text, encoding="utf-8")
                return resp.text
            if resp.status_code == 404:
                log(f"  ! {url}: 404, skipping")
                return None
            log(f"  ! {url} attempt {attempt}: HTTP {resp.status_code}")
            time.sleep(2 ** attempt)

        return None


def soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def text(node) -> str:
    """Collapsed whitespace text of a node."""
    if node is None:
        return ""
    return " ".join(node.get_text(" ", strip=True).split())


def to_int(value) -> int | None:
    """Parse an integer out of a rating string, tolerating commas and junk."""
    if value is None:
        return None
    match = re.search(r"-?\d[\d,]*", str(value))
    if not match:
        return None
    return int(match.group(0).replace(",", ""))


def strip_abbrev(label: str) -> tuple[str, str | None]:
    """'Arm Strength (ARM STR)' -> ('Arm Strength', 'ARM STR')."""
    match = re.match(r"^(.*?)\s*\(([^)]+)\)\s*$", label)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return label.strip(), None


def key_for(label: str) -> str:
    """Normalise an attribute label into a stable snake_case key."""
    label = strip_abbrev(label)[0]
    label = label.replace("&", " and ")
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", label.lower())).strip("_")


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def write_dataset(name: str, payload: dict) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / f"{name}.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    log(f"wrote {path.relative_to(REPO_ROOT)} "
        f"({len(payload.get('players', []))} players, "
        f"{len(payload.get('teams', []))} teams)")
    return path


def parse_card_attributes(page) -> tuple[dict, dict]:
    """Parse maddenratings/theshowratings player pages.

    Both render each attribute category as a `div.card` whose `card-header`
    holds '<Category> Ratings <value>', followed by one `div.d-flex` per
    attribute.

    The label and value are read structurally rather than by splitting the
    row's text, because maddenratings injects a weekly-movement badge between
    them (`Awareness +8 91`), which a naive text split folds into the label.

    Returns (attributes, categories).
    """
    attributes: dict[str, dict] = {}
    categories: dict[str, int] = {}

    for card in page.find_all("div", class_="card"):
        header = card.find("div", class_=re.compile(r"card-header"))
        if not header:
            continue
        match = re.match(r"^(.*?)\s*Ratings\s+(\d+)$", text(header))
        if not match:
            continue  # bio/chart cards and the 'Total Attributes' footer
        category = match.group(1).strip()
        categories[key_for(category)] = int(match.group(2))

        rows = card.find_all("div", class_="d-flex")
        for row in rows[1:]:  # row 0 repeats the category header
            # The rating lives in its own span; `data-order` carries it as a
            # float on maddenratings, plain text on theshowratings.
            box = row.find("span", class_=re.compile(r"attribute-box"))
            if box is None:
                continue
            value = to_int(box.get("data-order") or text(box))
            if value is None:
                continue

            label_node = row.find("span", class_="attribute-label")
            if label_node is None:
                spans = row.find_all("span", recursive=False)
                label_node = spans[0] if spans else None
            label = text(label_node)
            if not label:
                continue

            name, abbrev = strip_abbrev(label)
            attributes[key_for(name)] = {
                "label": name,
                "abbrev": abbrev,
                "category": category,
                "value": value,
            }

    return attributes, categories
