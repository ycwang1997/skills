# Playwright and replay patterns

Copy-paste recipes for the situations `sniff.py` and `probe.py` put you in. All
sync-API Playwright, matching the scripts.

## Read the page's own state instead of re-issuing the call

Cheapest extraction there is: the data is already parsed in memory.

```python
# Apollo / GraphQL clients
data = page.evaluate("() => window.__APOLLO_CLIENT__?.cache.extract()")

# Next.js props (also available in the HTML as __NEXT_DATA__, no JS needed)
data = page.evaluate("() => window.__NEXT_DATA__.props.pageProps")

# Nuxt / Vue, Redux
data = page.evaluate("() => window.__NUXT__?.data")
data = page.evaluate("() => window.__store__?.getState?.()")
```

## Enumerate what a page fetched, without a full capture

```python
urls = page.evaluate("""() => performance.getEntriesByType('resource')
    .filter(e => /api|graphql|\\.json/.test(e.name)).map(e => e.name)""")
```

Useful as a quick sanity check on a single page. `sniff.py` is still the tool when
you need bodies, methods and headers.

## Intercept the page's own response — try this before re-issuing anything

When the endpoint is walled, the request the *page itself* makes still succeeds.
Let it make the call and take the parsed JSON out of the response:

```python
grabbed = {}
def on_response(r):
    if "/api/" in r.url and "json" in r.headers.get("content-type", ""):
        try:
            grabbed[r.url] = r.json()
        except Exception:
            pass

page.on("response", on_response)
page.goto(url, wait_until="networkidle")
page.wait_for_timeout(5000)   # let late XHRs land
```

This is what `sniff.py` does, and it is the most reliable browser technique there
is: you are not constructing a request, so there is nothing about yours to reject.
The cost is that you only get the calls the page chooses to make — to reach other
params you drive the UI and intercept again.

**Measured counterexample to the section below** (atptour.com, 2026-08-10): the
page's own XHR to `app.atptour.com/api/v2/gateway/livematches/website` returns
25.7 KB of clean JSON, but re-issuing that exact URL from page context in the same
passed session fails with `TypeError: Failed to fetch` (CORS), and the sibling
endpoint `www.atptour.com/en/-/www/…` returns a 403 Cloudflare interstitial. Both
were re-tested from a fresh context that had just passed a challenge, so it is not
a stale-cookie artifact. Interception worked; re-issuing did not.

## Fire the request from page context

Every cookie, WAF token and bearer the session holds comes along automatically, so
this often works where `requests` and `curl` cannot — **but not always**, see the
counterexample above. A WAF rule can key on the request shape (headers the app
adds, `sec-fetch-site`, the path) rather than on credentials, and a naked `fetch`
you wrote does not look like the app's own call. Cross-origin API hosts add a
second failure mode: CORS allows the app's requests and refuses yours.

Try it second, after interception, and fall back to interception if it 403s.

```python
result = page.evaluate("""async () => {
    const r = await fetch('/api/v2/search?q=shoes&page=2', {
        credentials: 'include',
        headers: {'Accept': 'application/json'},
    });
    return {status: r.status, body: await r.json()};
}""")
```

For GraphQL, same trick with the POST body `sniff.py` captured in `post_data`:

```python
result = page.evaluate("""async ([op, query, vars]) => {
    const r = await fetch('/graphql', {
        method: 'POST', credentials: 'include',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({operationName: op, query, variables: vars}),
    });
    return r.json();
}""", [op_name, query_text, {"page": 2}])
```

Persisted queries: if the captured body has a `sha256Hash` and no `query` text, the
server only accepts hashes it knows. Reuse the captured hash verbatim; you cannot
invent one.

## Lift a bearer token for the `hybrid` path

```python
# From the SPA's token store (inspect localStorage keys first)
token = page.evaluate("() => localStorage.getItem('access_token')")

# Or from its own outbound requests, which is more reliable than guessing keys
captured = {}
page.on("request", lambda r: captured.setdefault("auth", r.headers.get("authorization")))
page.goto(url, wait_until="networkidle")

# Then leave the browser behind
session = requests.Session()
session.headers.update({"Authorization": captured["auth"], "User-Agent": DESKTOP_UA})
```

Treat 401 mid-run as "re-open the page", not "retry". Blind retries against an
expired token are what trips rate limits.

## Reuse a login instead of logging in every run

```sh
# once, by hand
run sniff.py https://site.example/login --headed --pause --save-storage ./auth.json
# afterwards
run sniff.py https://site.example/dashboard --storage-state ./auth.json
```

```python
context = browser.new_context(storage_state="auth.json")   # cookies + localStorage
```

`auth.json` is a live credential. It is gitignored here; keep it that way, and
only ever for an account you own.

## Wait for data, not for time

```python
# The response you actually care about
with page.expect_response(lambda r: "/api/search" in r.url and r.status == 200) as info:
    page.click("button[type=submit]")
data = info.value.json()

# Or a DOM condition
page.wait_for_function("() => document.querySelectorAll('[data-testid=row]').length > 10")
```

`networkidle` never settles on sites with polling or ad refresh — `sniff.py` treats
its timeout as normal for that reason. Don't build a scraper on `wait_for_timeout`
alone.

## Pagination

- **URL param** — best case: `?page=N`. Loop until an empty result set, never until
  a fixed N.
- **Cursor in the response** — pass the previous response's cursor. Note in the site
  skill whether the cursor is session-bound (many are; then it is a `browser` job).
- **Infinite scroll** — `sniff.py --scroll N`, or in code:

```python
seen = 0
while True:
    page.mouse.wheel(0, 20_000)
    page.wait_for_timeout(1500)
    count = page.evaluate("() => document.querySelectorAll('[data-testid=row]').length")
    if count == seen:
        break          # stopped growing: either the end, or a "load more" button
    seen = count
```

- **"Load more" button** — click while it exists, with a hard cap so a broken
  selector can't loop forever.

## Overlays that swallow clicks

A modal's overlay absorbs pointer events even for buttons outside it. Symptom:
`page.click()` reports success and nothing changes. browse.sh calls the workaround
"the single most important pattern" in one of their skills:

```python
page.eval_on_selector("button.add-to-cart", "el => el.click()")   # synthetic event, reaches the handler
```

Better still: check whether the overlay actually blocks *reading*. It usually
doesn't — `page.content()` and `querySelector` work fine behind a modal, so if you
only need data, ignore the dialog entirely.

## Blocking noise to speed up a capture

```python
context.route("**/*", lambda route: route.abort()
              if route.request.resource_type in ("image", "media", "font") else route.continue_())
```

Do not block scripts or XHR — you would be blocking the thing you came to find. Be
aware this changes the request pattern the site sees; keep it for development, drop
it if a site starts behaving differently.

## Being one visitor, not a fleet

The scripts here already do this; keep it when you write the client:

- one `Session` / one browser context, reused (connection reuse is also a fingerprint)
- ≥1.5s between requests, and honour `robots.txt` `crawl_delay` when it is higher
- a real desktop User-Agent, a `Referer` that matches how a browser would have arrived
- sequential pages, no parallel fan-out
- on 429: stop, don't back off and retry in a loop
