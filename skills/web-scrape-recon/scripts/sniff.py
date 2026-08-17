"""
Layer 4 recon: drive the page in a real browser and record what it calls.

    uv run --project <skill dir> scripts/sniff.py https://example.com/search?q=x
    uv run --project <skill dir> scripts/sniff.py URL --scroll 3 --click "button.more"
    uv run --project <skill dir> scripts/sniff.py URL --headed --save-storage auth.json

This is the step that turns "there must be an API" into an exact request you can
replay. Every XHR/fetch/GraphQL call is recorded with its method, query params,
POST body, request-header names, status, content type and full response body, and
the summary flags which calls carried a Cookie/Authorization header — that flag
is what decides between a clean `api` path and a `hybrid` one.

Politeness: one browser context, pages visited sequentially, a pause after each
navigation, a normal desktop UA. It visits the pages you name; it does not crawl.
"""

import argparse
import itertools
import json
import sys
from collections import defaultdict
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).parent))
from _common import (  # noqa: E402
    DESKTOP_UA,
    SECRET_HEADERS,
    is_noise_host,
    pretty_bytes,
    redact_headers,
    slugify_host,
    url_template,
    write_json,
)

# Resource types that can carry data. Documents are included because SSR pages
# and /_next/data/*.json navigations both show up as documents.
DATA_TYPES = {"xhr", "fetch", "document", "websocket", "eventsource"}


def main():
    ap = argparse.ArgumentParser(description="Capture a page's network calls with Playwright.")
    ap.add_argument("urls", nargs="+")
    ap.add_argument("--out", default=None, help="output dir (default: ./sniff-<host>)")
    ap.add_argument("--headed", action="store_true", help="show the browser (needed for manual login)")
    ap.add_argument("--wait", type=float, default=4.0, help="seconds to idle after each navigation")
    ap.add_argument("--scroll", type=int, default=0, help="scroll to bottom N times (triggers lazy loads)")
    ap.add_argument("--click", action="append", default=[], help="selector to click after load (repeatable)")
    ap.add_argument("--storage-state", help="load cookies/localStorage from this file")
    ap.add_argument("--save-storage", help="save cookies/localStorage here after the run")
    ap.add_argument("--pause", action="store_true", help="pause for manual interaction before capture ends (implies --headed)")
    ap.add_argument("--all", action="store_true", help="record every resource type, not just data-ish ones")
    ap.add_argument("--include-secrets", action="store_true",
                    help="also write capture.secrets.json with real Cookie/Authorization values so probe.py can replay "
                         "auth'd calls. Short-lived tokens, but still credentials — the file is gitignored.")
    ap.add_argument("--max-body", type=int, default=2_000_000, help="max response body bytes to save per call")
    ap.add_argument("--har", action="store_true", help="also write a full HAR")
    ap.add_argument("--save-html", action="store_true", help="save each page's rendered DOM (layer-6 fallback input)")
    ap.add_argument("--timeout", type=float, default=45.0, help="navigation timeout in seconds")
    args = ap.parse_args()

    out_dir = Path(args.out or f"sniff-{slugify_host(args.urls[0])}")
    (out_dir / "bodies").mkdir(parents=True, exist_ok=True)

    calls: list[dict] = []
    secrets: dict[str, dict] = {}  # index -> full unredacted request headers
    seen: dict[tuple, dict] = {}  # (method, url, post_data) -> the entry we already made
    # response.body() pumps the event loop, so on_response re-enters. Indices come
    # from a counter claimed up front, never from len(calls).
    counter = itertools.count()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not (args.headed or args.pause))
        ctx_kwargs = {
            "user_agent": DESKTOP_UA,
            "viewport": {"width": 1366, "height": 900},
            "locale": "en-US",
        }
        if args.storage_state:
            ctx_kwargs["storage_state"] = args.storage_state
        if args.har:
            ctx_kwargs["record_har_path"] = str(out_dir / "capture.har")
        context = browser.new_context(**ctx_kwargs)
        page = context.new_page()

        def on_response(response):
            req = response.request
            if not args.all and req.resource_type not in DATA_TYPES:
                return
            try:
                post = req.post_data
            except Exception:
                post = None
            key = (req.method, req.url, post)
            if key in seen:
                seen[key]["seen_count"] += 1
                return

            headers = req.headers
            entry = {
                "index": next(counter),
                "method": req.method,
                "url": req.url,
                "url_template": url_template(req.url),
                "resource_type": req.resource_type,
                "status": response.status,
                "request_headers": redact_headers(headers),
                "auth_headers_present": sorted(h for h in headers if h.lower() in SECRET_HEADERS),
                "response_content_type": response.headers.get("content-type", ""),
                "noise_host": is_noise_host(req.url),
                "seen_count": 1,
            }
            # Claim the slot before reading the body, so a re-entrant call for the
            # same request dedupes instead of racing us.
            seen[key] = entry
            calls.append(entry)
            if post:
                entry["post_data"] = post[:20_000]
                try:
                    parsed = json.loads(post)
                    if isinstance(parsed, dict) and "query" in parsed:
                        entry["graphql_operation"] = parsed.get("operationName") or "<anonymous>"
                except Exception:
                    pass
            ct = entry["response_content_type"].lower()
            if "json" in ct or "graphql" in ct or "xml" in ct or "text/plain" in ct:
                try:
                    body = response.body()
                    entry["body_bytes"] = len(body)
                    if len(body) <= args.max_body:
                        ext = "json" if "json" in ct or "graphql" in ct else "txt"
                        bp = out_dir / "bodies" / f"{entry['index']:04d}.{ext}"
                        bp.write_bytes(body)
                        entry["body_file"] = str(bp)
                        entry["body_preview"] = body[:800].decode("utf-8", "replace")
                except Exception as e:
                    entry["body_error"] = f"{type(e).__name__}: {e}"
            if args.include_secrets:
                secrets[str(entry["index"])] = dict(headers)

        page.on("response", on_response)

        for url in args.urls:
            print(f"→ {url}")
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=args.timeout * 1000)
            except Exception as e:
                print(f"  navigation issue: {e}")
            try:
                page.wait_for_load_state("networkidle", timeout=args.timeout * 1000)
            except Exception:
                pass  # networkidle never settles on sites with polling/analytics

            for sel in args.click:
                try:
                    page.click(sel, timeout=5000)
                    print(f"  clicked {sel}")
                    page.wait_for_timeout(1500)
                except Exception as e:
                    print(f"  click {sel} failed: {type(e).__name__}")
            for i in range(args.scroll):
                page.mouse.wheel(0, 20_000)
                page.wait_for_timeout(1500)
                print(f"  scrolled {i + 1}/{args.scroll}")

            page.wait_for_timeout(int(args.wait * 1000))

            if args.save_html:
                hp = out_dir / f"rendered-{slugify_host(url)}-{len(list(out_dir.glob('rendered-*.html')))}.html"
                hp.write_text(page.content())
                print(f"  rendered DOM → {hp}")

        if args.pause:
            print("\n-- browser is open. Log in / interact, then press Enter here to finish capture --")
            input()

        if args.save_storage:
            context.storage_state(path=args.save_storage)
            print(f"storage state → {args.save_storage}  (reuse with --storage-state; treat as a credential)")

        context.close()
        browser.close()

    write_json(out_dir / "capture.json", calls)
    if secrets:
        sp = write_json(out_dir / "capture.secrets.json", secrets)
        print(f"! real auth header values written to {sp} — credentials, do not commit or paste into a report")

    # ---- ranked summary ----------------------------------------------------
    groups = defaultdict(list)
    for c in calls:
        groups[(c["method"], c["url_template"], c.get("graphql_operation"))].append(c)

    def score(entries):
        # Rank by how much JSON came back: the endpoint holding the data is
        # almost always the biggest JSON response on the page. Consent banners and
        # analytics ship big JSON too, so they sort below everything else.
        json_bytes = sum(e.get("body_bytes", 0) for e in entries if "json" in e["response_content_type"].lower())
        return (0 if entries[0]["noise_host"] else 1, json_bytes, len(entries))

    ranked = sorted(groups.items(), key=lambda kv: score(kv[1]), reverse=True)

    print(f"\n{'=' * 78}\n{len(calls)} distinct calls captured → {out_dir / 'capture.json'}\n{'=' * 78}")
    print(f"{'idx':>4} {'method':<7} {'status':>6} {'body':>8} {'auth':<5} endpoint")
    shown = 0
    for (method, tmpl, op), entries in ranked:
        e = entries[0]
        body = pretty_bytes(e.get("body_bytes", 0)) if e.get("body_bytes") else "-"
        auth = "yes" if e["auth_headers_present"] else "no"
        label = f"{tmpl}  [{op}]" if op else tmpl
        n = sum(x["seen_count"] for x in entries)
        suffix = f"  (x{n})" if n > 1 else ""
        if e["noise_host"]:
            suffix += "  [3rd-party noise]"
        print(f"{e['index']:>4} {method:<7} {e['status']:>6} {body:>8} {auth:<5} {label}{suffix}")
        shown += 1
        if shown >= 40:
            print(f"  ... {len(ranked) - shown} more groups in capture.json")
            break

    with_auth = [c for c in calls if c["auth_headers_present"] and "json" in c["response_content_type"].lower()]
    without = [c for c in calls if not c["auth_headers_present"] and "json" in c["response_content_type"].lower()]
    print("\nREAD THIS:")
    print(f"  {len(without)} JSON call(s) went out with NO cookie/auth header → candidates for a clean `api` path.")
    if with_auth:
        carried = sorted({h for c in with_auth for h in c["auth_headers_present"]})
        print(f"  {len(with_auth)} JSON call(s) carried {', '.join(carried)} → likely `hybrid` (browser first, then replay).")
    else:
        print("  no JSON call needed a cookie or token — if one of the above has your data, you never need a browser again.")
    print(f"\nnext: uv run scripts/probe.py --capture {out_dir / 'capture.json'} --index <idx>")
    print("      that replays one call outside the browser and tells you which headers are actually required.")


if __name__ == "__main__":
    main()
