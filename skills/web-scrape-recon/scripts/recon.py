"""
Layer 0/1 recon: everything you can learn about a site without a browser.

    uv run --project <skill dir> scripts/recon.py https://example.com/some/page

Answers, in one pass:
  * Is there a wall (Akamai / Cloudflare / DataDome / ...) in front of this?
  * Is the content already in the HTML (SSR) — i.e. no JS needed at all?
  * Is there an embedded state blob (__NEXT_DATA__, LD+JSON, Apollo, ...)?
  * Does the page or its JS bundles leak API endpoints / a search backend key?
  * Are there declared machine-readable surfaces (robots, sitemap, llms.txt, RSS)?
  * Do the usual API paths exist (/api, /graphql, /openapi.json, /wp-json, ...)?

Writes a JSON summary plus every blob it found, so the next step can read them.
Nothing here hammers the site: one page fetch, a few bundle fetches, and a
short probe list, all behind a shared pacer.
"""

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parent))
from _common import (  # noqa: E402
    BLOB_PATTERNS,
    NOISE_HOST_RE,
    THIRD_PARTY_BACKENDS,
    Pacer,
    detect_walls,
    new_session,
    pretty_bytes,
    slugify_host,
    url_template,
    write_json,
)

# Paths worth one GET each. Ordered cheapest-signal-first; all are conventional
# public surfaces, not guesses at private endpoints.
PROBE_PATHS = [
    "/llms.txt",
    "/openapi.json",
    "/swagger.json",
    "/api",
    "/api/v1",
    "/api/config",
    "/graphql",
    "/wp-json/wp/v2/posts",
    "/products.json?limit=1",
    "/feed",
    "/rss.xml",
]

# URL-ish strings in HTML/JS that are plausibly data endpoints.
ENDPOINT_RE = re.compile(
    r"""["'`](?P<u>(?:https?://[a-z0-9.\-]+)?/(?:api|graphql|gql|rest|v\d|_next/data|data|feed|search|query)[a-z0-9/_\-.{}$:?=&%]*)["'`]""",
    re.I,
)
# Absolute hosts mentioned anywhere in the page/bundle. A bare `https://api.foo.com`
# with no path is invisible to ENDPOINT_RE but is often the single best lead you
# get without a browser.
HOST_RE = re.compile(r"https?://([a-z0-9](?:[a-z0-9.\-]*[a-z0-9])?\.[a-z]{2,})", re.I)
DATA_HOST_RE = re.compile(r"(?:^|[.\-])(?:api|apis|graphql|gql|data|content|search|feeds?|json|rest|svc|services?)(?:[.\-]|$)|execute-api|appsync", re.I)
ALGOLIA_RE = re.compile(r"""(?:algolia[_-]?(?:app(?:lication)?[_-]?id|api[_-]?key)|x-algolia-\w+)["'\s:=]+["']([a-z0-9]{8,})["']""", re.I)
API_KEYISH_RE = re.compile(r"""["'](?:apiKey|api_key|publicKey|searchKey|accessToken|clientId)["']\s*:\s*["']([^"']{8,80})["']""")


def fetch(session, pacer, url, timeout, allow_errors=True):
    pacer.wait()
    try:
        return session.get(url, timeout=timeout, allow_redirects=True)
    except Exception as e:
        if allow_errors:
            return e
        raise


def ssr_verdict(html: str, soup: BeautifulSoup) -> dict:
    """Does the served HTML already contain the content?"""
    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()
    text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
    links = len(soup.find_all("a", href=True))
    ratio = len(text) / max(len(html), 1)
    if len(text) > 2000 and links > 20:
        verdict = "SSR — content is in the HTML. Try url-param/fetch before any browser."
    elif len(text) > 500:
        verdict = "Partial SSR — some content present; check whether the fields you need are among it."
    else:
        verdict = "JS-rendered shell — the HTML has no content. Expect an API behind it (best case) or a browser (worst case)."
    return {
        "text_chars": len(text),
        "html_chars": len(html),
        "text_ratio": round(ratio, 4),
        "anchor_count": links,
        "verdict": verdict,
        "text_sample": text[:400],
    }


def find_blobs(html: str, out_dir: Path) -> list[dict]:
    found = []
    for name, pattern in BLOB_PATTERNS:
        for i, m in enumerate(pattern.finditer(html)):
            raw = m.group(1).strip()
            entry = {"kind": name, "chars": len(raw)}
            try:
                parsed = json.loads(raw)
                entry["parsed"] = True
                if isinstance(parsed, dict):
                    entry["top_level_keys"] = list(parsed.keys())[:25]
                    if name == "ld+json":
                        entry["schema_type"] = parsed.get("@type")
                elif isinstance(parsed, list):
                    entry["top_level_keys"] = f"list[{len(parsed)}]"
                    if parsed and isinstance(parsed[0], dict):
                        entry["schema_type"] = parsed[0].get("@type")
                path = out_dir / "blobs" / f"{name.strip('_')}_{i}.json"
                write_json(path, parsed)
                entry["saved_to"] = str(path)
            except Exception as e:
                entry["parsed"] = False
                entry["parse_error"] = f"{type(e).__name__}: {e}"
                path = out_dir / "blobs" / f"{name.strip('_')}_{i}.txt"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(raw[:200_000])
                entry["saved_to"] = str(path)
            found.append(entry)
    return found


def scan_endpoints(text: str, base_url: str) -> list[str]:
    hits = set()
    for m in ENDPOINT_RE.finditer(text):
        u = m.group("u")
        # Skip obvious asset paths and template noise.
        if re.search(r"\.(?:js|css|png|jpe?g|svg|woff2?|map|ico)(?:$|\?)", u, re.I):
            continue
        hits.add(urljoin(base_url, u) if u.startswith("/") else u)
    return sorted(hits, key=lambda u: (len(u), u))


def scan_hosts(text: str, own_host: str) -> dict:
    """Third-party hosts referenced by the page, split into data-ish and other."""
    counts: dict[str, int] = {}
    for m in HOST_RE.finditer(text):
        h = m.group(1).lower()
        if h == own_host or NOISE_HOST_RE.search(h):
            continue
        counts[h] = counts.get(h, 0) + 1
    data_ish = {h: n for h, n in counts.items() if DATA_HOST_RE.search(h)}
    other = {h: n for h, n in counts.items() if h not in data_ish}
    return {
        "data_ish": dict(sorted(data_ish.items(), key=lambda kv: -kv[1])),
        "other": dict(sorted(other.items(), key=lambda kv: -kv[1])[:30]),
    }


def detect_backends(text: str) -> dict:
    out = {}
    for name, patterns in THIRD_PARTY_BACKENDS.items():
        for p in patterns:
            if re.search(p, text, re.I):
                out.setdefault(name, []).append(p)
    return out


def scan_bundles(session, pacer, soup, base_url, limit, timeout) -> dict:
    """Fetch the first N first-party JS bundles and grep them for endpoints/keys.

    This is where SPAs keep their API base URLs and public search keys — the
    same thing you would read off the Network tab, minus the browser.
    """
    host = urlparse(base_url).netloc
    srcs = []
    for tag in soup.find_all("script", src=True):
        src = urljoin(base_url, tag["src"])
        if urlparse(src).netloc in ("", host) and src.endswith((".js", ".mjs")):
            srcs.append(src)
    result = {"fetched": [], "endpoints": [], "keys": [], "backends": {}, "hosts": {"data_ish": {}, "other": {}}}
    seen_endpoints, seen_keys = set(), set()
    for src in srcs[:limit]:
        r = fetch(session, pacer, src, timeout)
        if isinstance(r, Exception) or r.status_code >= 400:
            result["fetched"].append({"url": src, "error": str(r)})
            continue
        body = r.text
        result["fetched"].append({"url": src, "bytes": len(body)})
        for u in scan_endpoints(body, base_url):
            if u not in seen_endpoints:
                seen_endpoints.add(u)
                result["endpoints"].append(u)
        for m in list(ALGOLIA_RE.finditer(body)) + list(API_KEYISH_RE.finditer(body)):
            v = m.group(1)
            if v not in seen_keys:
                seen_keys.add(v)
                result["keys"].append({"value": v, "context": body[max(0, m.start() - 60):m.end() + 20]})
        for name, pats in detect_backends(body).items():
            result["backends"].setdefault(name, []).extend(pats)
        for bucket, hosts in scan_hosts(body, host).items():
            for h, n in hosts.items():
                result["hosts"][bucket][h] = result["hosts"][bucket].get(h, 0) + n
    return result


def check_robots(session, pacer, base_url, target_url, timeout) -> dict:
    url = urljoin(base_url, "/robots.txt")
    r = fetch(session, pacer, url, timeout)
    if isinstance(r, Exception) or r.status_code >= 400:
        return {"url": url, "available": False, "note": str(r if isinstance(r, Exception) else r.status_code)}
    rp = RobotFileParser()
    rp.parse(r.text.splitlines())
    lines = r.text.splitlines()
    return {
        "url": url,
        "available": True,
        "allows_target_for_wildcard_ua": rp.can_fetch("*", target_url),
        "crawl_delay": rp.crawl_delay("*"),
        "sitemaps": [ln.split(":", 1)[1].strip() for ln in lines if ln.lower().startswith("sitemap:")],
        "disallow_sample": [ln.strip() for ln in lines if ln.lower().startswith("disallow:")][:20],
    }


def probe(session, pacer, base_url, timeout) -> list[dict]:
    out = []
    for path in PROBE_PATHS:
        url = urljoin(base_url, path)
        r = fetch(session, pacer, url, timeout)
        if isinstance(r, Exception):
            out.append({"path": path, "error": str(r)})
            continue
        if r.status_code == 404:
            continue  # the boring answer; skip to keep the report readable
        ct = r.headers.get("content-type", "")
        entry = {"path": path, "status": r.status_code, "content_type": ct, "bytes": len(r.content)}
        if "json" in ct or "xml" in ct or "text/plain" in ct:
            entry["preview"] = r.text[:500]
        out.append(entry)
    return out


def main():
    ap = argparse.ArgumentParser(description="Browserless recon on a target page.")
    ap.add_argument("url")
    ap.add_argument("--out", default=None, help="output dir (default: ./recon-<host>)")
    ap.add_argument("--bundles", type=int, default=3, help="how many first-party JS bundles to grep (0 to skip)")
    ap.add_argument("--no-probe", action="store_true", help="skip the conventional-path probe list")
    ap.add_argument("--pace", type=float, default=1.5, help="min seconds between requests")
    ap.add_argument("--timeout", type=float, default=20)
    args = ap.parse_args()

    out_dir = Path(args.out or f"recon-{slugify_host(args.url)}")
    out_dir.mkdir(parents=True, exist_ok=True)
    base = f"{urlparse(args.url).scheme}://{urlparse(args.url).netloc}"

    pacer = Pacer(args.pace)
    session = new_session(accept="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8")

    print(f"→ GET {args.url}")
    r = fetch(session, pacer, args.url, args.timeout)
    if isinstance(r, Exception):
        print(f"  request failed: {r}")
        sys.exit(1)

    html = r.text
    soup = BeautifulSoup(html, "lxml")
    (out_dir / "page.html").write_text(html)

    report = {
        "target": args.url,
        "final_url": r.url,
        "status": r.status_code,
        "server": r.headers.get("server"),
        "content_type": r.headers.get("content-type"),
        "bytes": len(r.content),
        "redirect_chain": [h.headers.get("location") for h in r.history],
        "walls": detect_walls(r),
        "title": (soup.title.string or "").strip() if soup.title else None,
        "generator": (soup.find("meta", attrs={"name": "generator"}) or {}).get("content"),
    }
    report["rendering"] = ssr_verdict(html, BeautifulSoup(html, "lxml"))
    report["blobs"] = find_blobs(html, out_dir)
    report["feeds"] = [
        urljoin(args.url, ln["href"])
        for ln in soup.find_all("link", rel=lambda v: v and "alternate" in v, href=True)
        if "xml" in (ln.get("type") or "") or "json" in (ln.get("type") or "")
    ]
    report["endpoints_in_html"] = scan_endpoints(html, args.url)
    report["backends_in_html"] = detect_backends(html)
    report["hosts_in_html"] = scan_hosts(html, urlparse(args.url).netloc.lower())
    report["robots"] = check_robots(session, pacer, base, args.url, args.timeout)
    if args.bundles:
        print(f"→ grepping up to {args.bundles} JS bundles")
        report["bundles"] = scan_bundles(session, pacer, soup, args.url, args.bundles, args.timeout)
    if not args.no_probe:
        print(f"→ probing {len(PROBE_PATHS)} conventional paths")
        report["probes"] = probe(session, pacer, base, args.timeout)

    path = write_json(out_dir / "recon.json", report)

    # ---- human summary -----------------------------------------------------
    print(f"\n{'=' * 72}\n{args.url}\n{'=' * 72}")
    print(f"status {report['status']}  {pretty_bytes(report['bytes'])}  server={report['server']}")
    if report["walls"]:
        print(f"WALL: {', '.join(report['walls'])}  → probe the API host separately; see references/walls.md")
    else:
        print("WALL: none detected on this response")
    rob = report["robots"]
    if rob.get("available"):
        print(f"robots.txt: target allowed={rob['allows_target_for_wildcard_ua']} crawl_delay={rob['crawl_delay']} sitemaps={len(rob['sitemaps'])}")
    print(f"\nRENDERING: {report['rendering']['verdict']}")
    print(f"  visible text {report['rendering']['text_chars']} chars, {report['rendering']['anchor_count']} links, ratio {report['rendering']['text_ratio']}")

    if report["blobs"]:
        print("\nEMBEDDED BLOBS (read these before writing any selector):")
        for b in report["blobs"]:
            keys = b.get("top_level_keys") or b.get("schema_type") or "?"
            print(f"  {b['kind']:<18} {pretty_bytes(b['chars']):>8}  keys={keys}")
            print(f"  {'':<18} saved: {b['saved_to']}")
    else:
        print("\nEMBEDDED BLOBS: none")

    data_hosts = dict(report["hosts_in_html"]["data_ish"])
    for h, n in (report.get("bundles", {}).get("hosts", {}).get("data_ish") or {}).items():
        data_hosts[h] = data_hosts.get(h, 0) + n
    if data_hosts:
        print("\nDATA-ISH HOSTS (strongest browserless lead — try these first):")
        for h, n in sorted(data_hosts.items(), key=lambda kv: -kv[1]):
            print(f"  {h}  (x{n})")

    backends = dict(report["backends_in_html"])
    for name in (report.get("bundles", {}).get("backends") or {}):
        backends.setdefault(name, [])
    if backends:
        print(f"\nTHIRD-PARTY BACKENDS: {', '.join(backends)}")
        print("  → these are usually callable directly with the public key in the bundle")

    keys = report.get("bundles", {}).get("keys") or []
    if keys:
        print("\nPUBLIC KEYS IN BUNDLE (search-only keys are a public surface, not a leak):")
        for k in keys[:10]:
            print(f"  {k['value']}")

    endpoints = list(report["endpoints_in_html"])
    endpoints += [u for u in report.get("bundles", {}).get("endpoints", []) if u not in endpoints]
    if endpoints:
        grouped = sorted({url_template(u) for u in endpoints})
        print(f"\nCANDIDATE ENDPOINTS ({len(endpoints)} raw, {len(grouped)} templates):")
        for u in grouped[:40]:
            print(f"  {u}")
        if len(grouped) > 40:
            print(f"  ... {len(grouped) - 40} more in recon.json")

    if report["feeds"]:
        print(f"\nFEEDS: {', '.join(report['feeds'])}")

    interesting = [p for p in report.get("probes", []) if p.get("status") and p["status"] < 400]
    if interesting:
        print("\nCONVENTIONAL PATHS THAT EXIST:")
        for p in interesting:
            print(f"  {p['status']} {p['path']:<24} {p['content_type']} {pretty_bytes(p['bytes'])}")

    print(f"\nfull report: {path}")
    print("next: pick a layer per SKILL.md. If nothing above gives you the data, run scripts/sniff.py.")


if __name__ == "__main__":
    main()
