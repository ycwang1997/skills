# web-scrape-recon

A skill plus three CLI tools for answering one question before you write a
scraper: **what is the cheapest way to get this data, and does it actually
work?**

The ladder and the decision rules live in `SKILL.md` — that is what the agent
reads. This file covers running the tools by hand.

Install with `npx skills add ycwang1997/skills --skill web-scrape-recon`; see the
[repository README](../../README.md) for scopes and options.

## Prerequisites

- [uv](https://docs.astral.sh/uv/), and Python 3.11+ (uv fetches it if missing).
- One-time per machine, only for `scripts/sniff.py`:

  ```shell
  uv run --project <skill dir> playwright install chromium
  ```

`uv run --project` builds the virtualenv from `uv.lock` on first use (~15s).
The `.venv` is never committed — it hardcodes absolute paths and does not
survive a copy to another location.

## Running the tools by hand

```shell
SKILL_DIR=~/.claude/skills/web-scrape-recon
run() { uv run --project "$SKILL_DIR" "$SKILL_DIR/scripts/$@"; }

run recon.py https://target.example/page --out ./recon-target   # Layer 0/1
run sniff.py https://target.example/page --out ./sniff-target   # Layer 3/4
run probe.py --capture ./sniff-target/capture.json --index 11   # Layer 2/3
```

`--help` on any of them lists the rest of the flags.

- **`recon.py`** — wall fingerprints, SSR-vs-JS-shell verdict, embedded state
  blobs parsed and saved, data-ish hostnames, third-party search backends and
  their public keys, endpoints in the HTML and JS bundles, feeds, `robots.txt`,
  and which conventional paths exist.
- **`sniff.py`** — every XHR/fetch/GraphQL call the page makes, ranked by JSON
  payload size, with bodies on disk. Flags which calls carried a cookie or
  bearer — that flag separates `api` from `hybrid`.
- **`probe.py`** — replays one captured call outside the browser: full headers,
  UA-only, leave-one-out over every header, then verifies the minimal set. Emits
  a verdict (`api` / `hybrid` / `browser`) and a paste-ready snippet.

Every script paces itself at ≥1.5s between requests and visits only the URLs you
name. There is no crawl mode on purpose. `capture.secrets.json`, `auth.json` and
`*.har` hold live credentials and are gitignored.
