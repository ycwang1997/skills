---
name: <site>-<what-it-does>            # e.g. wtatennis-get-rankings
title: <Site> <What It Does>
description: >-
  One paragraph: what input it takes, what it returns, which surface it uses, and
  any hard limit. Written so a future agent can decide from this alone whether
  this skill answers the question. State read-only if it is read-only.
website: <host, no scheme>
category: <sports|ecommerce|government|finance|travel|news|real-estate|...>
tags:
  - <site-slug>
  - <domain>
  - read-only
source: 'recon: web-scrape-recon <YYYY-MM-DD>'
updated: '<YYYY-MM-DD>'
recommended_method: <feed|url-param|api|hybrid|browser|rendered>
alternative_methods:
  - method: <api>
    rationale: >-
      Why this is the recommended path — the endpoint, what it returns, what it
      requires. Be specific enough to act on without re-running recon.
  - method: <browser>
    rationale: >-
      Why this is the fallback, or why it was ruled out. Include the actual
      symptom: "returns 403 without the Referer header", "cursor is session-bound",
      "state filter is silently ignored at SPA load".
verified: <true|false>                 # did you run it end to end and see real rows?
proxies: <true|false>                  # does it need a non-datacenter IP?
---

# <Site> <What It Does>

## Purpose

What this returns, field by field, and what it will not do. Name the boundary
explicitly (read-only, never submits, never follows affiliate links).

## When to Use

- The task shapes that should land here.
- Include the phrasings a user would actually type.
- And the near-misses that should go elsewhere: "for X, use <other skill> instead".

## Workflow

### Path A — <recommended method> (recommended)

1. **Build the request.** Exact host, path, and every param that matters:

   ```
   GET https://api.<site>/v1/<resource>
       ?page=0
       &pageSize=100
       &<filter>=<value>
   ```

   | Param | Values | Notes |
   |---|---|---|
   | `pageSize` | 1–100 | 100 is the max; default is 25 |
   | `<filter>` | … | … |

2. **Required headers** — exactly what `probe.py` proved load-bearing, no more:

   ```
   Referer: https://<site>/
   Accept: application/json
   User-Agent: <a real desktop UA>
   ```

3. **Parse the response.** Map response paths to output fields:

   | Output field | JSON path |
   |---|---|
   | … | `content[i].…` |

4. **Paginate.** How you know when to stop (`totalRecords`, empty page, cursor null).

5. **Pacing.** Requests are spaced ≥Ns. `robots.txt` `crawl_delay` = N.

### Path B — <fallback method>

Use only when Path A fails, and state how you'd notice. Same level of detail.

## Expected Output

A concrete example — one real record, trimmed, with realistic values:

```json
{
  "…": "…"
}
```

## Site-Specific Gotchas

- **<Symptom in bold>.** What happens, why, and what to do instead. Include status
  codes and error bodies verbatim; they are how the next run recognises it.
- **What was ruled out.** The endpoints that 403'd, the params silently ignored, the
  blob that turned out to be stale. A recorded dead end saves more time than a
  working recipe.
- **Freshness.** Anything that will drift: a Next.js `buildId` in the URL, a public
  key that may rotate, a `verified` date that should be re-checked.
