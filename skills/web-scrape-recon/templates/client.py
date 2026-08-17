"""
Client template for a verified `api` / `hybrid` endpoint.

Copy into the project, rename, and fill in BASE, HEADERS and the functions. The
header set should be exactly what probe.py proved load-bearing — no more, so that a
future failure tells you something.

Kept deliberately polite:
  - one shared Session (connection reuse, one client identity)
  - MIN_INTERVAL enforced between any two calls to the same host
  - no retry loop on failure — it raises, so a 429 stops the run instead of
    hammering through it
"""

import os
import time

import requests

BASE = "https://api.example.com"
MIN_INTERVAL = 1.5  # seconds between requests; raise to robots.txt crawl_delay if higher

DESKTOP_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_session = requests.Session()
_session.headers.update(
    {
        "User-Agent": DESKTOP_UA,
        "Accept": "application/json",
        # Load-bearing per probe.py — do not drop:
        "Referer": "https://example.com/",
    }
)
_last_call = 0.0


def _get(path: str, params: dict | None = None) -> dict | list:
    global _last_call
    wait = MIN_INTERVAL - (time.monotonic() - _last_call)
    if wait > 0:
        time.sleep(wait)
    resp = _session.get(f"{BASE}{path}", params=params, timeout=15)
    _last_call = time.monotonic()
    resp.raise_for_status()
    return resp.json()


# --- hybrid only: delete this block for a plain `api` client -------------------
def refresh_credentials() -> None:
    """Mint a session credential with a browser, then keep using plain HTTP.

    Called once at start, and again on a 401 — never in a blind retry loop.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=DESKTOP_UA, viewport={"width": 1366, "height": 900})
        page = context.new_page()
        page.goto("https://example.com/", wait_until="networkidle")
        token = page.evaluate("() => localStorage.getItem('access_token')")
        for cookie in context.cookies():
            _session.cookies.set(cookie["name"], cookie["value"], domain=cookie["domain"])
        browser.close()

    if token:
        _session.headers["Authorization"] = f"Bearer {token}"


# If the credential comes from the environment instead of a browser:
if os.environ.get("EXAMPLE_API_KEY"):
    _session.headers["X-Api-Key"] = os.environ["EXAMPLE_API_KEY"]
# ------------------------------------------------------------------------------


def list_items(query: str, page_size: int = 100):
    """One page of results. Params and their limits are documented in SKILL.md."""
    return _get("/v1/items", {"q": query, "page": 0, "pageSize": page_size})


def iter_items(query: str, page_size: int = 100, max_pages: int = 50):
    """Page until the results run out. max_pages is a stop, not a target."""
    for page in range(max_pages):
        batch = _get("/v1/items", {"q": query, "page": page, "pageSize": page_size})
        rows = batch.get("content", batch) if isinstance(batch, dict) else batch
        if not rows:
            return
        yield from rows
        if len(rows) < page_size:
            return  # short page means last page


if __name__ == "__main__":
    # Smoke test: prove it returns real rows before anyone trusts this module.
    items = list_items("example")
    rows = items.get("content", items) if isinstance(items, dict) else items
    print(f"{len(rows)} rows")
    for row in rows[:5]:
        print(" ", row)
