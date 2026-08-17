"""
Shared helpers for the recon scripts.

Everything here is deliberately polite: one session identity, a real desktop
User-Agent, and a minimum interval enforced between requests to the same host.
We are trying to look like one curious visitor, not a scraper.
"""

import json
import re
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

DESKTOP_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Header names whose values must never be printed or written to disk.
SECRET_HEADERS = {
    "cookie", "set-cookie", "authorization", "proxy-authorization",
    "x-api-key", "apikey", "api-key", "x-auth-token", "x-csrf-token",
    "x-xsrf-token", "x-algolia-api-key", "x-amz-security-token",
}

# Fingerprints of the anti-bot stacks that decide whether a browserless path is
# even possible. Cookie names and headers are the reliable tells; body regexes
# only apply to challenge pages. Frequencies observed across browse.sh's
# catalog: Akamai and Cloudflare dominate, then DataDome and Kasada.
WALL_SIGNATURES = [
    ("Akamai Bot Manager", {"cookies": ["_abck", "bm_sz", "ak_bmsc"], "headers": []}),
    ("Cloudflare", {"cookies": ["__cf_bm", "cf_clearance"], "headers": ["cf-mitigated", "cf-chl-bypass"]}),
    ("DataDome", {"cookies": ["datadome"], "headers": ["x-datadome", "x-dd-b"]}),
    ("Imperva/Incapsula", {"cookies": ["visid_incap", "incap_ses", "nlbi_"], "headers": ["x-iinfo", "x-cdn"]}),
    ("PerimeterX/HUMAN", {"cookies": ["_px", "_pxhd", "_pxvid"], "headers": ["x-px"]}),
    ("Kasada", {"cookies": ["x-kpsdk-ct"], "headers": ["x-kpsdk-ct", "x-kpsdk-r"]}),
    ("F5/Shape", {"cookies": ["_shape", "TS01"], "headers": ["x-shape"]}),
    ("AWS WAF", {"cookies": ["aws-waf-token"], "headers": ["x-amzn-waf-action"]}),
]

# Where site data actually lives, in the order it is worth looking. The counts
# in the comments are how often each showed up across the 132 fully published
# browse.sh SKILL.md files — a decent prior for what to check first.
BLOB_PATTERNS = [
    ("__NEXT_DATA__", re.compile(r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', re.S)),  # 70
    ("ld+json", re.compile(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', re.S)),              # 60
    ("__NUXT__", re.compile(r'window\.__NUXT__\s*=\s*(.*?);?\s*</script>', re.S)),
    ("__INITIAL_STATE__", re.compile(r'window\.__(?:INITIAL_STATE|PRELOADED_STATE|INITIAL_DATA)__\s*=\s*(.*?);?\s*</script>', re.S)),
    ("__APOLLO_STATE__", re.compile(r'window\.__APOLLO_STATE__\s*=\s*(.*?);?\s*</script>', re.S)),
    ("deferred-state", re.compile(r'<script[^>]+id=["\']data-deferred-state[^"\']*["\'][^>]*>(.*?)</script>', re.S)),
    ("remix-context", re.compile(r'window\.__remixContext\s*=\s*(.*?);?\s*</script>', re.S)),
    ("sveltekit-data", re.compile(r'type=["\']application/json["\'][^>]*data-sveltekit[^>]*>(.*?)</script>', re.S)),
]

# Third-party search/data backends worth spotting: if the site outsources search
# you can usually call that backend directly with the public key in the bundle.
THIRD_PARTY_BACKENDS = {
    "Algolia": [r"algolia(?:net|\.net)", r"algoliasearch", r"x-algolia-application-id", r"-dsn\.algolia\.net"],
    "Constructor.io": [r"ac\.cnstrc\.com", r"constructorio"],
    "Typesense": [r"typesense"],
    "Meilisearch": [r"meilisearch"],
    "Elastic/Swiftype": [r"swiftype", r"app-search\.[a-z]+\.elastic"],
    "Shopify Storefront": [r"/products\.json", r"shopify", r"myshopify\.com"],
    "WordPress REST": [r"/wp-json/"],
    "Sanity": [r"\.api\.sanity\.io"],
    "Contentful": [r"cdn\.contentful\.com"],
    "Supabase": [r"\.supabase\.co"],
    "Firebase/Firestore": [r"firestore\.googleapis\.com", r"firebaseio\.com"],
    "Prismic": [r"\.prismic\.io"],
    "Strapi": [r"/api/.*populate="],
    "Hasura/GraphQL": [r"/graphql", r"/gql", r"graphql\.[a-z]+"],
}


# Hosts that are never the data source — consent banners, analytics, ad tech.
# Used to keep reports signal-dense: these are still recorded, just ranked last.
NOISE_HOST_RE = re.compile(
    r"google|gstatic|doubleclick|googlesyndication|facebook|fbcdn|twitter|x\.com|linkedin|instagram|"
    r"youtube|ytimg|cookielaw|onetrust|hotjar|segment|mixpanel|amplitude|sentry|newrelic|datadoghq|"
    r"optimizely|cloudflareinsights|adservice|adsystem|adnxs|criteo|taboola|outbrain|scorecardresearch|"
    r"w3\.org|schema\.org|iana\.org|jquery|jsdelivr|unpkg|cdnjs|fonts\.|licdn|gravatar|"
    r"exponea|braze|klaviyo|intercom|zendesk|livelikeapp|recaptcha|gtm\.",
    re.I,
)


def is_noise_host(url: str) -> bool:
    return bool(NOISE_HOST_RE.search(urlparse(url).netloc))


def new_session(referer: str | None = None, accept: str = "*/*") -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": DESKTOP_UA, "Accept": accept,
                      "Accept-Language": "en-US,en;q=0.9"})
    if referer:
        s.headers["Referer"] = referer
    return s


class Pacer:
    """Enforces a floor on the gap between requests. Shared by all scripts."""

    def __init__(self, min_interval: float = 1.5):
        self.min_interval = min_interval
        self._last = 0.0

    def wait(self) -> None:
        gap = self.min_interval - (time.monotonic() - self._last)
        if gap > 0:
            time.sleep(gap)
        self._last = time.monotonic()


def redact_headers(headers: dict) -> dict:
    """Keep the header *names* (they are the interesting part) but drop values."""
    out = {}
    for k, v in headers.items():
        if k.lower() in SECRET_HEADERS:
            out[k] = f"<redacted len={len(v)}>"
        else:
            out[k] = v
    return out


def detect_walls(response: requests.Response) -> list[str]:
    """Which anti-bot stacks are fronting this response, by cookie/header tells."""
    cookie_names = {c.lower() for c in response.cookies.keys()}
    # requests folds repeated Set-Cookie headers into one comma-joined value.
    raw_set_cookie = response.headers.get("set-cookie", "").lower()
    header_names = {k.lower() for k in response.headers}
    server = response.headers.get("server", "").lower()

    found = []
    for name, sig in WALL_SIGNATURES:
        hit = any(any(c.lower().startswith(pat.lower()) for c in cookie_names) for pat in sig["cookies"])
        hit = hit or any(pat.lower() in raw_set_cookie for pat in sig["cookies"])
        hit = hit or any(any(h.startswith(pat) for h in header_names) for pat in sig["headers"])
        if hit:
            found.append(name)
    if "cloudflare" in server and "Cloudflare" not in found:
        found.append("Cloudflare (edge only, no challenge seen)")
    return found


def url_template(url: str) -> str:
    """Collapse ids out of a path so repeated calls group together.

    /tennis/tournaments/1234/2026/matches -> /tennis/tournaments/{n}/{n}/matches
    """
    p = urlparse(url)
    parts = []
    for seg in p.path.split("/"):
        if not seg:
            parts.append(seg)
        elif seg.isdigit():
            parts.append("{n}")
        elif re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", seg, re.I):
            parts.append("{uuid}")
        elif len(seg) > 20 and re.search(r"\d", seg) and re.search(r"[a-z]", seg, re.I):
            parts.append("{id}")
        else:
            parts.append(seg)
    return f"{p.scheme}://{p.netloc}{'/'.join(parts)}"


def looks_like_json_endpoint(url: str, content_type: str = "") -> bool:
    if "json" in content_type.lower() or "graphql" in content_type.lower():
        return True
    lowered = url.lower()
    return any(k in lowered for k in ("/api/", "/graphql", "/gql", ".json", "/rest/", "/v1/", "/v2/", "/_next/data/"))


def pretty_bytes(n: int) -> str:
    if n < 1024:
        return f"{n}B"
    if n < 1024**2:
        return f"{n / 1024:.1f}KB"
    return f"{n / 1024**2:.1f}MB"


def write_json(path: Path, data) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    return path


def slugify_host(url: str) -> str:
    return re.sub(r"[^a-z0-9.-]", "_", urlparse(url).netloc.lower()) or "site"
