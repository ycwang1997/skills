---
name: web-scrape-recon
description: >-
  Work out how to scrape a website before writing any scraper. Walks a target
  through six escalating methods — static recon, URL params, direct API,
  browser-then-replay (hybrid), full browser, rendered-text fallback — stopping
  at the cheapest one that actually returns the data, then writes a reusable
  site skill plus a working Python client. Use whenever the task is "get data
  off <site>", "does this site have an API", "is there an endpoint behind this
  page", "scrape/crawl <site>", "why is my scraper getting 403", or when
  choosing between requests, Playwright, and a paid rendering service.
---

# Web Scrape Recon

## Purpose

Given a target page and the fields someone wants off it, determine the **cheapest
reliable way to get those fields**, prove it works, and write it down so nobody
has to rediscover it. The output is not a one-off script — it is a site skill (a
markdown file recording the endpoints, params, gotchas and walls) plus a client
module that a future task can use directly.

The ordering matters more than any single technique. A JSON endpoint found in
step 1 is worth more than a perfect set of CSS selectors found in step 5: it is
faster, it survives redesigns, it returns typed fields instead of strings, and it
does not need a browser in the deployment.

## When to Use

- "Get me X from <site>" / "scrape <site>" / "build a crawler for <site>".
- "Does <site> have an API?" — this skill answers that with evidence, not a guess.
- An existing scraper broke, returns 403/429, or returns an empty shell.
- Deciding whether a job needs Playwright, or a paid rendering/unblocking service.
- Before writing selectors. Always run at least step 1 first.

Do **not** use this to defeat a login you do not have, to work around a paywall,
or to bulk-harvest personal data. **Rules of engagement** below is part of the
workflow, not a disclaimer.

## Workflow

Set this once per session. `SKILL_DIR` is the **"Base directory for this skill"**
path printed when this skill loads — never a hardcoded path, so the skill keeps
working wherever it is installed:

```sh
SKILL_DIR="<the base directory shown above>"
run() { uv run --project "$SKILL_DIR" "$SKILL_DIR/scripts/$@"; }
```

`uv run --project` builds the venv from `uv.lock` on first use, so a freshly copied
skill needs no setup step. The one exception is the Chromium download that
`sniff.py` needs, which is cached per machine — if it errors with a missing
executable, run `uv run --project "$SKILL_DIR" playwright install chromium` once.

Work top to bottom. **Stop at the first layer that returns the fields asked for**
— and record which layers you ruled out and why, because that is half the value
of the finished skill.

```
data in HTML?           → 1. url-param  (parse LD+JSON / blob, no browser)
JSON endpoint replays?  → 2. api        (probe.py verdict `api`)
endpoint needs a token? → 3. hybrid     (browser once, then replay)
request session-bound?  → 4. browser    (page context or rendered DOM)
walled or N sites?      → 5. hosted rendering
```

The cost you are minimising is not just runtime. A `browser` solution costs a
headless Chromium in production, breaks on every UI redesign, and returns strings
you have to re-parse. An `api` solution is a function call.

### Layer 0 — Static recon (always run this)

```sh
run recon.py https://target.example/the/page --out ./recon-target
```

Read the report before deciding anything. It tells you:

| Section | What it decides |
|---|---|
| `WALL:` | Akamai/Cloudflare/DataDome/Kasada present → jump to `references/walls.md` before wasting turns |
| `RENDERING:` | "SSR" means the data is already in the HTML — layers 1–2 are live options |
| `EMBEDDED BLOBS` | `__NEXT_DATA__`, LD+JSON, Apollo state — parsed JSON, saved to disk, no API needed |
| `DATA-ISH HOSTS` | `api.foo.com`, `*.execute-api.*` — the strongest browserless lead there is |
| `THIRD-PARTY BACKENDS` | Algolia/Constructor/Shopify/WP — call these directly with the public key in the bundle |
| `CONVENTIONAL PATHS` | `/llms.txt`, `/openapi.json`, `/wp-json/…`, `/products.json` returning 200 |
| `FEEDS` | RSS/Atom: the cheapest possible answer for anything article-shaped |
| `robots.txt` | `allows_target=False` or a `crawl_delay` you must honour |

Declared surfaces are worth knowing by what each buys you: **RSS** answers "latest
headlines from X" outright; **`sitemap.xml`** enumerates every URL without
crawling, and its `<lastmod>` turns "re-scrape everything" into "re-scrape what
changed"; **`/openapi.json`** means the site documents its own API; **`/llms.txt`**
is explicitly meant for you to read.

If a blob or feed already contains every field asked for, you are done — go to
**Deliverables**. Most "we need a crawler" tasks end here and nobody notices.

Rule these out when the feed is truncated (title + link only) or when the fields
you need — price, availability, rating — are never in feeds.

### Layer 1 — URL params + SSR (`url-param`)

Cheapest real method: the site's own filters are expressible as query params and
the results come back inside the HTML. Test by editing the URL by hand and
re-running `recon.py` on the modified URL, then diffing `page.html`.

Confirmed when: changing `?q=`/`?page=`/`?sort=` changes the rendered content, and
`RENDERING` still says SSR. Then no browser and no API reverse-engineering are
needed — you fetch and parse HTML.

**Parse in this order: LD+JSON block → embedded state blob → CSS selectors.** The
first two are contracts the site maintains for search engines and its own
framework; selectors are an accident of the current design.

Rule out when: filters live in a POST body, in a hash fragment (`#/search?…` never
reaches the server), or the page is a JS shell. → Layer 2. A common half-case: the
first page is SSR and page 2 is XHR — then use `url-param` for page 1 and note the
XHR endpoint for the rest.

### Layer 2 — Direct API (`api`)

Where a candidate endpoint comes from, in order of how cheap the lookup is:

1. `DATA-ISH HOSTS` in `recon.py` output (`api.foo.com`, `*.execute-api.*`)
2. a third-party backend + its public key (Algolia, Constructor.io, Shopify
   `/products.json`, WordPress `/wp-json/`)
3. `/_next/data/<buildId>/<route>.json` — Next.js route data, directly callable
   (the `buildId` changes on every deploy; read it from `__NEXT_DATA__`)
4. `CANDIDATE ENDPOINTS`, or `/openapi.json`
5. `sniff.py`, when nothing above worked

Prove it works outside a browser:

```sh
run probe.py --url 'https://api.target.example/v1/items?page=0' -H 'Referer: https://target.example/'
```

`probe.py` replays with the full header set, then with a User-Agent only, then
removes one header at a time to find which are load-bearing, then verifies the
minimal set. It prints a verdict and a paste-ready `requests` snippet. Typical
load-bearing headers are `Referer`, `Origin`, an `Accept`, and sometimes a static
public key — all constants you can hardcode.

**Verify shape, not status.** A 200 that returns the marketing shell, an empty
list, or a challenge page is a failure. `probe.py` compares JSON top-level keys
for exactly this reason; apply the same standard by hand.

Verdict `api` → **stop here.** Write the client, note the required headers and
the pagination params in the site skill.

If you have no candidate endpoint yet, or the endpoint 403s, → Layer 3/4 via `sniff.py`.

### Layer 3/4 — Watch the browser, then decide (`hybrid` vs `browser`)

Open the page in a real browser and record every call it makes:

```sh
run sniff.py 'https://target.example/the/page' --out ./sniff-target --wait 5
run sniff.py URL --scroll 3 --click 'button[data-testid=load-more]'   # trigger lazy loads
run sniff.py URL --headed --pause --save-storage ./auth.json          # log in by hand, keep the session
run sniff.py URL --storage-state ./auth.json                          # reuse it later
```

The summary ranks endpoints by JSON payload size (the data is nearly always the
biggest JSON response) and flags whether each call carried a Cookie or bearer.
Then take the interesting index back to `probe.py`:

```sh
run sniff.py URL --out ./sniff-target --include-secrets   # needed to replay auth'd calls
run probe.py --capture ./sniff-target/capture.json --index 11
```

Three possible verdicts, and each is a different method:

**`api`** — replays with static headers only. The browser was scaffolding; drop it.

**`hybrid`** — needs a session cookie or bearer that a browser must mint first.
This is the right answer for most SPAs behind an auth-gated API (33 of browse.sh's
132 published skills work this way), and the best cost/robustness trade-off:
one browser start per run instead of one per page. Shape of the solution:

1. open the page once in Playwright (or load a saved `--storage-state`),
2. lift the credential — from the request headers `sniff.py --include-secrets`
   recorded, from `localStorage` via `page.evaluate`, or from the context cookie jar,
3. page through the JSON API with plain `requests` and that credential,
4. refresh when it expires (401 → re-open the page, don't retry blindly).

Doesn't apply when the credential is per-request rather than per-session (nonce,
signed cursor, `fb_dtsg`-style token) → `browser`.

**`browser`** — fails even with the browser's full header set: the request is
bound to that session (WAF token, per-request nonce, TLS fingerprint). Stay in the
browser and prefer, strictly in this order:

1. **rendered DOM** — `sniff.py --save-html`, then parse offline. No click automation.
2. **intercept the page's own response** — you are not constructing a request, so
   there is nothing about yours to reject.
3. **page context** — `page.evaluate("fetch(url, {credentials:'include'})…")` sends
   the request *from* the session, so every token comes along free. Not always
   enough; `references/patterns.md` has the measured counterexample.
4. **framework state** — read the Apollo/Redux/Next cache in the page instead of
   re-issuing the call.
5. **UI automation** — clicks and typing. Last, and always with the overlay
   workaround in `patterns.md`.

Note that `--save-html` gives you the rendered DOM, which is often all you need —
parse that instead of automating clicks through the whole UI.

### Layer 5 — Rendered-text fallback

Only when: the page is JS-rendered, has no usable endpoint, **and** a wall makes
your own browser unreliable — or the job is "read many arbitrary pages as text"
rather than "extract structured fields from one site".

This is where a hosted rendering/unblocking service (Firecrawl, Browserbase, or
similar) earns its cost. It is deliberately **not** a dependency of this project;
`references/walls.md` covers when it is genuinely the answer, how to add it, and
the three cheaper things to try first.

**Doesn't apply when you skipped a rung.** Paying to render a page whose JSON
endpoint you never looked for is the most common expensive mistake in scraping.

## Deliverables

Produce both, in the user's project (not in the skill directory):

1. **`SKILL.md` for the target site** — copy `templates/site-skill.template.md`.
   It follows the browse.sh frontmatter schema (`recommended_method`,
   `alternative_methods` with rationale per method, `verified`, `updated`), so it
   is portable and machine-readable. Record what did **not** work and why; that
   is what stops the next person repeating your dead ends.
2. **`client.py`** — copy `templates/client.py`. Keeps the pacing, one session
   identity, and the minimal header set that `probe.py` proved.

Verify before claiming done: run the client, print real rows, and state the
verdict plainly — including which layers failed.

## Rules of engagement

Part of the workflow, not a disclaimer. These are also what keeps a scraper
working: the fastest way to lose access to a site is to look like an attack.

**Always**

- **Read `robots.txt` first.** `recon.py` reports whether the target path is
  allowed for `*` and what `crawl_delay` is set. If the path is disallowed, say so
  to the user and stop before writing a scraper for it — that is their decision to
  make knowingly, not something to discover later.
- **Pace every request.** ≥1.5s between calls to the same host is the default in
  every script here; a longer `crawl_delay` wins. Sequential, single session, no
  parallel fan-out. One curious visitor, not a fleet.
- **Fetch only the pages you need.** These scripts visit URLs you name. They are
  not crawlers, and there is no `--depth` flag on purpose.
- **Cache aggressively while developing.** `recon.py` writes `page.html`,
  `sniff.py` writes every response body. Re-run your parser against those files,
  not against the site. Most accidental hammering happens during parser debugging.
- **Stop on 429 or 403.** Do not back off and retry in a loop.
- **Prefer the surface the site published for machines** — feeds, sitemaps,
  OpenAPI, `/llms.txt`, an official API with a free key. More stable, *and* it is
  the access the site consented to.

**Never**

- **Never bypass authentication or payment.** No credential stuffing, no shared or
  scraped logins, no paywall circumvention. `--storage-state` is for accounts you
  personally own, and the file is a credential.
- **Never defeat an active anti-bot challenge.** CAPTCHA solving, TLS/JA3 spoofing
  and identity rotation to evade a block are out of scope. A site actively
  refusing automated access has told you its answer; the legitimate paths are a
  different data source or permission.
- **Never collect personal data you weren't asked for.** Scope extraction to the
  fields the task actually needs, don't store extras "just in case", and don't
  build people-search datasets.
- **Never commit or publish captured credentials.** `capture.secrets.json`,
  `auth.json` and `*.har` (HARs contain full cookie headers) are gitignored here;
  keep that when you copy the client into a project. Search-only keys baked into a
  JS bundle (Algolia, DocSearch) are a public surface and fine to record in a site
  skill — session cookies and bearers are not. Write `<from a live session>` in
  the documentation instead.

**Worth checking before a big run**

- Terms of service, for anything commercial or at volume. Technical possibility
  and permission are different questions, and only one of them is yours to answer.
- Whether an official API exists with a free key — `recon.py` probes
  `/openapi.json`, `/swagger.json` and `/llms.txt`; also just search "<site> API
  docs" before reverse-engineering anything.
- Whether the data is published in bulk. Government and research sites frequently
  offer a CSV/Parquet dump that makes the whole scraper unnecessary.
- What breaks if you are wrong. A scraper that reads is recoverable. Anything that
  **writes** — submitting forms, adding to carts, sending messages — needs
  explicit confirmation from the user first, every time.

**Tell the user what you found.** State plainly: which method you used, what you
ruled out and why, the pacing you applied, whether `robots.txt` allows the path,
and any wall you saw. If a request looked like it needed a login you don't have,
or the target is personal data, raise it instead of routing around it.

## Gotchas

- **Do not skip Layer 0.** Selectors written before recon are usually wasted work.
- **Biggest JSON response wins.** In `sniff.py` output, sort by body size, not by
  how the URL reads. `[3rd-party noise]` rows are consent/analytics — ignore them.
- **A 200 with the wrong shape is a failure.** `probe.py` compares JSON top-level
  keys, not just status codes; apply the same standard by hand.
- **A wall on the HTML page does not mean a wall on the API.** Very often the
  marketing page is behind Cloudflare and `api.site.com` is wide open. Probe the
  endpoint separately.
- **A 403 is not always a wall.** Missing `Referer`/`Origin`/`Accept` produces the
  same status. `probe.py`'s leave-one-out pass distinguishes the two.
- **Session-bound beats clever.** If `probe.py` says `browser`, believe it and stop
  trying header combinations; you are fighting a nonce or a TLS fingerprint.
- **Rate limits are part of the recipe.** Record the pacing you used in the site
  skill.

## References

- `references/patterns.md` — Playwright and replay patterns per situation
- `references/walls.md` — anti-bot stacks, their tells, and where a paid service fits
