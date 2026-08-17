# Skills

Reusable agent skills. Installable into Claude Code, Codex, Cursor and other
agents that read `SKILL.md` collections.

## Quick Start

```shell
npx skills add ycwang1997/skills
```

Then open your agent and let it pick a skill by task description, or invoke one
explicitly by name.

## Install

`npx skills` ([vercel-labs/skills](https://github.com/vercel-labs/skills))
resolves this repository and links the skills into whichever agents you choose.

```shell
# interactive: pick skills and agents
npx skills add ycwang1997/skills

# list what is in here without installing
npx skills add ycwang1997/skills --list

# install one skill, globally, for Claude Code, no prompts
npx skills add ycwang1997/skills --skill web-scrape-recon -g -a claude-code -y
```

| Scope | Flag | Lands in |
|---|---|---|
| Project | (default) | `./.claude/skills/` |
| Global | `-g` | `~/.claude/skills/` |

Update later with `npx skills update`, remove with `npx skills remove`.

Installing by hand works too — copy `skills/<skill-name>/` into
`.claude/skills/<skill-name>/`. Never copy a `.venv`; see the prerequisites
below.

## Catalog

| Skill | Use it for | Needs |
|---|---|---|
| [`web-scrape-recon`](skills/web-scrape-recon/) | Deciding the cheapest reliable way to get data off a website — feeds, URL params, direct API, browser-then-replay, browser, hosted rendering — before writing any scraper. Produces a site skill plus a working client. | `uv`, Python 3.11+, one-time Chromium download |

## Prerequisites

Most skills are plain markdown and need nothing. Skills that bundle `scripts/`
declare what they need in the table above.

`web-scrape-recon` runs its tools through [uv](https://docs.astral.sh/uv/):

```shell
# install uv if you do not have it
curl -LsSf https://astral.sh/uv/install.sh | sh

# one-time, per machine, only needed for scripts/sniff.py
uv run --project ~/.claude/skills/web-scrape-recon playwright install chromium
```

The virtualenv is built from `uv.lock` on first run (~15s) and is never
committed — it hardcodes absolute paths and does not survive a copy.

## Repository Layout

```
skills/<skill-name>/SKILL.md        active skills, discovered by npx skills
deprecated/<skill-name>/SKILL.md    retired skills, outside discovery
```

Optional supporting material lives inside a skill directory under
`references/`, `scripts/`, `templates/`, or `assets/`.

Maintainer conventions are in [AGENTS.md](AGENTS.md).

## License

MIT — see [LICENSE](LICENSE).
