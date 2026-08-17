# Repository Guidelines

Maintainer conventions for this repository. External positioning, installation
and the skill catalog live in `README.md`.

## Repository structure

- Keep active skills in `skills/<skill-name>/SKILL.md`.
- Keep retired skills in `deprecated/<skill-name>/SKILL.md`, outside discovery,
  with the reason recorded in the README table.
- Keep optional supporting material inside its skill directory under
  `references/`, `scripts/`, `templates/`, or `assets/`.
- Do not add category directories until there are enough skills that a flat
  list is hard to scan.
- Do not add root-level marketplace or plugin metadata unless a corresponding
  distribution flow actually exists. Distribution here is `npx skills`.

## Skill authoring

- Use lowercase kebab-case for skill directories; name the entry file exactly
  `SKILL.md`.
- Keep frontmatter to `name` and `description` only. `name` must match the
  directory name.
- Write the `description` so another agent can decide from it alone: what the
  skill does, plus the phrasings that should trigger it.
- Put content in `SKILL.md` when it always applies, and in `references/` when it
  is only needed in a specific situation. A rule that must always hold does not
  belong in a file the agent may never open.
- Bundle a script when the operation is deterministic and error-prone to
  re-derive. Scripts are executed, not read into context, so a long script costs
  nothing until it runs — that is the point.
- Keep a skill's frontmatter description and its README catalog row aligned.

## Bundled scripts

- Single self-contained scripts: declare dependencies with PEP 723 inline
  metadata (`# /// script`) so `uv run script.py` needs no project.
- Multi-module script packages that share code: use a `pyproject.toml` plus a
  committed `uv.lock`, and run them with `uv run --project <skill dir>`.
- Never resolve a skill's own directory from a hardcoded path. Use the base
  directory the agent reports when the skill loads.
- Never commit `.venv/` — it hardcodes absolute paths and breaks on copy.
- Record any per-machine setup step (browser downloads, API keys) in the README
  prerequisites table.

## Documentation

- Update the README catalog when a skill is added, retired, renamed, or changes
  what triggers it.
- Keep installation instructions in `README.md` only, so there is one owner.

## Boundaries

- For review, explanation, or diagnosis requests, inspect and report without
  changing files.
- Ask before pushing, tagging, or anything else that leaves this machine.
- Verify a bundled script by running it through a representative path before
  claiming it works. Report the actual output.

## Git and commits

- This repository is public. Confirm the commit author identity is the personal
  GitHub one before the first commit — a work email in a public history cannot
  be removed without a rewrite.
- Summarize what changed and why, and report verification outcomes including
  anything skipped or failing.
