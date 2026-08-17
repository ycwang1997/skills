# Walls: what you hit, how to tell, what actually helps

`recon.py` prints a `WALL:` line from cookie and header fingerprints. This file
explains what each one means for your options, and where a paid service is the
honest answer rather than a shortcut.

## Reading the tells

| Stack | Tells (cookies / headers) | What it means in practice |
|---|---|---|
| **Cloudflare** | `__cf_bm`, `cf_clearance`, `cf-mitigated`, `server: cloudflare` | `__cf_bm` alone is just bot-management telemetry and usually harmless. `cf_clearance` or a 403 with a challenge page means an active challenge (Turnstile). |
| **Akamai Bot Manager** | `_abck`, `bm_sz`, `ak_bmsc` | The strictest common stack. `_abck` carries a sensor score; plain HTTP clients rarely pass. Expect to need a real browser plus a residential IP. |
| **DataDome** | `datadome` cookie, `x-datadome` | Aggressive on datacenter IPs. Often fine from a residential IP in a real browser. |
| **PerimeterX / HUMAN** | `_px*`, `_pxhd`, `_pxvid` | Interstitial "Press & Hold". Not worth automating. |
| **Imperva / Incapsula** | `visid_incap*`, `incap_ses*`, `x-iinfo` | Frequently gates only the search/POST path while listing pages stay open — probe the surfaces separately. |
| **Kasada** | `x-kpsdk-ct`, `x-kpsdk-r` | Requires executing their JS to mint a token; browser-only. |
| **F5 / Shape** | `TS01…` cookies | Usually tolerant of a real browser. |
| **AWS WAF** | `aws-waf-token`, `x-amzn-waf-action` | Classic `hybrid` case: drive the page so the token lands in the cookie jar, then call from page context — you cannot attach it from `curl`. |

Two things worth knowing before you react to any of this:

- **A wall on the HTML page does not mean a wall on the API.** Very often the
  marketing page is behind Cloudflare and `api.site.com` is wide open. Always
  probe the endpoint separately — `recon.py`'s `DATA-ISH HOSTS` exists for this.
- **A 403 is not always a wall.** Missing `Referer`/`Origin`/`Accept` produces the
  same status. `probe.py`'s leave-one-out pass distinguishes the two: if some
  header combination works, it was never a wall.

## Order of attack when you are actually blocked

1. **Probe a different surface.** The API, the mobile subdomain (`m.`), the AMP
   version, `/_next/data/…`, the RSS feed, the sitemap. Different origins often sit
   behind different rules.
2. **Look like a browser more completely.** Realistic `Accept-Language`, a `Referer`
   consistent with navigation, HTTP/2, and `sec-ch-ua`/`sec-fetch-*` headers copied
   from the capture. `probe.py` tests these for you — a header that "breaks it when
   removed" is exactly this effect.
3. **Use a real browser.** Playwright with a visible-quality context (real UA,
   viewport, locale) clears a surprising number of soft checks — but not Akamai or
   Kasada, and headless is detectable.
4. **Change the IP.** Datacenter IPs are the single biggest tell for DataDome and
   Akamai. This is what 88 of browse.sh's 132 published skills mean by
   `proxies: true`.
5. **Slow down.** A 429 is not a wall; it is a request to wait. Reduce concurrency
   to one and raise the interval. Read `robots.txt` `crawl_delay` again.
6. **Hosted rendering / unblocking.** Below.
7. **Ask whether the data is available another way.** An official API with a free
   key, a bulk download, a partner feed, or just asking the site owner. This beats
   every option above on both cost and durability, and is skipped far too often.

Not on this list: solving CAPTCHAs, spoofing TLS/JA3 fingerprints, or rotating
identities to evade a block. If a site is actively refusing automated access, the
answer is a different data source or permission — not better evasion. See
**Rules of engagement** in `SKILL.md`.

## Where a hosted service fits

Reach for one when a wall has survived steps 1–5, **or** when the shape of the job
is "read many arbitrary URLs" instead of "extract fields from one site".

### Firecrawl — URL to markdown/JSON

Best fit: many different domains, unstructured pages, LLM-facing text. Handles JS
rendering and light blocking; per-page cost. Deliberately not a dependency of this
project — add it only at the point you need it:

```sh
uv add firecrawl-py
export FIRECRAWL_API_KEY=fc-...
```

```python
from firecrawl import Firecrawl

app = Firecrawl(api_key=os.environ["FIRECRAWL_API_KEY"])
doc = app.scrape("https://target.example/page", formats=["markdown"])
print(doc.markdown)
```

Check the installed SDK's API before writing much against it — the constructor was
`FirecrawlApp` in v1 and the method names changed in v2. `python -c "import
firecrawl; help(firecrawl)"`.

Two things it does not fix: a login you do not have, and a site whose data you
actually need as typed fields — markdown of a table is worse than the JSON that
rendered it.

### Browserbase — hosted stealth browser + residential proxies

Best fit: one site, hard wall, and you need real interaction (login, multi-step
flow, geo-locked content). This is what browse.sh's own CLI drives — their skills
lean on `browse cloud fetch` for unblocked HTTP and `--verified --proxies` for a
residential identity. It connects over CDP, so existing Playwright code mostly
carries over unchanged.

### Your own Playwright + stealth patches

Cheapest, and adequate against soft checks. Against a maintained bot manager it is
an arms race you will lose on a schedule you don't control. Fine for a one-off,
wrong for anything that has to keep working.

## Write it down

Whatever you conclude, put it in the site skill's `## Site-Specific Gotchas` with
the concrete symptom, the same way browse.sh does:

> `/api/search` returns 403 with `{"error":"forbidden"}` unless the `Referer` is the
> exact search page URL. `_abck` is set on first load but is not required for the
> API. Verified 2026-08-10.

A recorded dead end saves the next run more time than a working recipe does.
