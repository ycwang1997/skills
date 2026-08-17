"""
Layer 2/3 recon: can this captured request be replayed outside the browser, and
with how little?

    uv run --project <skill dir> scripts/probe.py --capture sniff-x/capture.json --index 7
    uv run --project <skill dir> scripts/probe.py --url https://api.example.com/v1/items -H 'Referer: https://example.com/'

Procedure:
  1. Replay with the browser's full header set        → is the endpoint replayable at all?
  2. Replay with nothing but a User-Agent             → is it wide open?
  3. If not, leave-one-out over every header          → which ones are load-bearing?
  4. Re-verify with only the load-bearing headers     → the minimal recipe.

The verdict maps onto the method taxonomy in SKILL.md:
  open / needs a couple of static headers  → `api`   (no browser, ever)
  needs a session cookie or bearer token   → `hybrid` (browser once, then replay)
  fails even with the full header set      → `browser` (the request is session-bound)
"""

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

import requests

sys.path.insert(0, str(Path(__file__).parent))
from _common import DESKTOP_UA, SECRET_HEADERS, Pacer, detect_walls, pretty_bytes  # noqa: E402

# Headers requests/urllib3 must own, or that mean nothing off-browser.
DROP_HEADERS = {"host", "content-length", "connection", "cookie2", "te", "upgrade-insecure-requests"}


def signature(r: requests.Response) -> dict:
    sig = {
        "status": r.status_code,
        "content_type": r.headers.get("content-type", "").split(";")[0],
        "bytes": len(r.content),
        "keys": None,
    }
    try:
        data = r.json()
        if isinstance(data, dict):
            sig["keys"] = sorted(data.keys())
        elif isinstance(data, list):
            sig["keys"] = f"list[{len(data)}]"
    except Exception:
        pass
    return sig


def equivalent(candidate: dict, baseline: dict) -> bool:
    """Same endpoint, same shape of answer. Sizes drift, so allow slack."""
    if candidate["status"] >= 400:
        return False
    if candidate["content_type"] != baseline["content_type"]:
        return False
    if candidate["keys"] != baseline["keys"]:
        return False
    if baseline["bytes"] and abs(candidate["bytes"] - baseline["bytes"]) / baseline["bytes"] > 0.5:
        return False
    return True


def describe(sig: dict) -> str:
    keys = sig["keys"]
    if isinstance(keys, list):
        keys = f"{{{', '.join(keys[:6])}{'...' if len(keys) > 6 else ''}}}"
    return f"{sig['status']} {sig['content_type'] or '?'} {pretty_bytes(sig['bytes'])} {keys or ''}"


def attempt(pacer, method, url, headers, data, timeout):
    pacer.wait()
    s = requests.Session()
    s.headers.clear()
    s.trust_env = False
    return s.request(method, url, headers=headers, data=data, timeout=timeout, allow_redirects=True)


def load_from_capture(capture_path: Path, index: int) -> tuple[str, str, dict, str | None]:
    calls = json.loads(capture_path.read_text())
    match = next((c for c in calls if c["index"] == index), None)
    if match is None:
        sys.exit(f"no call with index {index} in {capture_path} (0..{len(calls) - 1})")

    headers = dict(match["request_headers"])
    secrets_path = capture_path.with_suffix(".secrets.json")
    if secrets_path.exists():
        real = json.loads(secrets_path.read_text()).get(str(index), {})
        for k, v in real.items():
            headers[k] = v
        print(f"(using real header values from {secrets_path.name})")
    else:
        redacted = [k for k, v in headers.items() if isinstance(v, str) and v.startswith("<redacted")]
        if redacted:
            print(f"! {', '.join(redacted)} are redacted in capture.json — dropping them.")
            print("  If the endpoint needs them, re-run sniff.py with --include-secrets.")
            for k in redacted:
                headers.pop(k)
    return match["method"], match["url"], headers, match.get("post_data")


def emit_snippet(method, url, headers, data, verdict_method):
    p = urlparse(url)
    lines = [
        "# ---- minimal reproduction ----",
        "import requests",
        "",
        "session = requests.Session()",
        "session.headers.update({",
    ]
    for k, v in headers.items():
        shown = "<from a live browser session — see references/patterns.md>" if k.lower() in SECRET_HEADERS else v
        lines.append(f"    {k!r}: {shown!r},")
    lines += ["})", ""]
    if method == "GET":
        lines.append(f"r = session.get({f'{p.scheme}://{p.netloc}{p.path}'!r}, params={dict(x.split('=', 1) for x in p.query.split('&') if '=' in x)!r}, timeout=15)")
    else:
        lines.append(f"r = session.request({method!r}, {url!r}, data={data!r}, timeout=15)")
    lines += ["r.raise_for_status()", "print(r.json())", ""]
    if verdict_method == "hybrid":
        lines.append("# NOTE: the auth value above expires. Refresh it with sniff.py --include-secrets,")
        lines.append("#       or keep a Playwright context alive and call from page context.")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="Replay a captured request off-browser and minimise its headers.")
    src = ap.add_argument_group("target (either --capture/--index or --url)")
    src.add_argument("--capture", type=Path)
    src.add_argument("--index", type=int)
    src.add_argument("--url")
    src.add_argument("--method", default="GET")
    src.add_argument("--data", help="request body for POST/GraphQL")
    src.add_argument("-H", "--header", action="append", default=[], help="'Name: value' (repeatable)")
    ap.add_argument("--pace", type=float, default=1.5, help="min seconds between requests")
    ap.add_argument("--timeout", type=float, default=20)
    ap.add_argument("--out", type=Path, help="write the verdict JSON here")
    args = ap.parse_args()

    if args.capture is not None and args.index is not None:
        method, url, headers, data = load_from_capture(args.capture, args.index)
    elif args.url:
        method, url, data = args.method.upper(), args.url, args.data
        headers = {}
    else:
        ap.error("give either --capture with --index, or --url")

    for h in args.header:
        k, _, v = h.partition(":")
        headers[k.strip()] = v.strip()

    headers = {k: v for k, v in headers.items() if k.lower() not in DROP_HEADERS and not k.startswith(":")}
    headers.setdefault("User-Agent", DESKTOP_UA)
    if data and not any(k.lower() == "content-type" for k in headers):
        headers["Content-Type"] = "application/json"

    print(f"{method} {url}")
    print(f"{len(headers)} header(s) to test → {len(headers) + 3} requests at {args.pace}s apart\n")

    # 1. baseline: everything the browser sent.
    pacer = Pacer(args.pace)
    r = attempt(pacer, method, url, headers, data, args.timeout)
    base = signature(r)
    walls = detect_walls(r)
    print(f"[full headers]  {describe(base)}")
    if walls:
        print(f"                wall: {', '.join(walls)}")

    result = {"method": method, "url": url, "full_headers": describe(base), "walls": walls}

    if base["status"] >= 400:
        print("\nVERDICT: not replayable off-browser.")
        print("  The request is session-bound (WAF token, per-session nonce, or TLS/JA3 fingerprinting).")
        print("  → method: `browser`. Drive the page and read the data from the DOM or from page context")
        print("    with `page.evaluate(...)` / `fetch(..., {credentials: 'include'})`. See references/patterns.md.")
        result["verdict"] = "browser"
        if args.out:
            args.out.write_text(json.dumps(result, indent=2))
        return

    # 2. nothing but a UA.
    bare = {"User-Agent": DESKTOP_UA}
    rb = attempt(pacer, method, url, bare, data, args.timeout)
    bare_sig = signature(rb)
    print(f"[UA only]       {describe(bare_sig)}")
    if equivalent(bare_sig, base):
        print("\nVERDICT: wide open. No headers required beyond a User-Agent.")
        print("  → method: `api`. Write a plain requests client; no browser in the pipeline.")
        result.update(verdict="api", required_headers=[])
        print("\n" + emit_snippet(method, url, bare, data, "api"))
        if args.out:
            args.out.write_text(json.dumps(result, indent=2))
        return

    # 3. leave-one-out.
    print("\nleave-one-out (which headers break it when removed?):")
    required = []
    for name in list(headers):
        if name.lower() == "user-agent":
            continue
        trial = {k: v for k, v in headers.items() if k != name}
        rt = attempt(pacer, method, url, trial, data, args.timeout)
        sig = signature(rt)
        ok = equivalent(sig, base)
        print(f"  without {name:<28} {'ok' if ok else 'BREAKS':<7} {describe(sig)}")
        if not ok:
            required.append(name)

    minimal = {k: v for k, v in headers.items() if k in required or k.lower() == "user-agent"}

    # 4. verify the minimal set on its own.
    rm = attempt(pacer, method, url, minimal, data, args.timeout)
    min_sig = signature(rm)
    print(f"\n[minimal set]   {describe(min_sig)}  ({', '.join(minimal) or 'UA only'})")
    if not equivalent(min_sig, base):
        print("  ! the minimal set alone does not reproduce it — headers interact, keep the full set.")
        minimal = headers

    secretish = sorted(k for k in minimal if k.lower() in SECRET_HEADERS)
    verdict = "hybrid" if secretish else "api"
    result.update(verdict=verdict, required_headers=sorted(minimal), session_bound_headers=secretish)

    print()
    if verdict == "api":
        print(f"VERDICT: replayable with {len(minimal)} static header(s): {', '.join(minimal)}")
        print("  → method: `api`. These values are constants; no browser needed.")
    else:
        print(f"VERDICT: needs session credentials: {', '.join(secretish)}")
        print("  → method: `hybrid`. Open the page in a browser once to mint them, then replay JSON calls.")
        print("    Get them with: sniff.py <page> --include-secrets   (or reuse a saved --storage-state)")
    print("\n" + emit_snippet(method, url, minimal, data, verdict))

    if args.out:
        args.out.write_text(json.dumps(result, indent=2))
        print(f"verdict → {args.out}")


if __name__ == "__main__":
    main()
