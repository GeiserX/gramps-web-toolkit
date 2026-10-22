# AGENTS.md: gramps-web-toolkit

Instructions for coding agents working in this repository. `CLAUDE.md` imports this file.

## What this is

Command-line tools for running AI research agents against a Gramps Web family tree. Each folder is one
tool with its own tests; `docs/` has one page per tool, plus `pipeline.md` (how the pieces fit) and
`hard-won.md` (the failures behind them). The toolkit is for any country; Spain-specific code says so in its name
(`spanish-poller`) or in its docs (the Spanish sources in `gwt-fetch`).

| Folder | Tool | Runtime |
|---|---|---|
| `apply/` | `gwt-apply`, change-list applier | Python 3.11+, `requests` |
| `check/` | `gwt-check`, read-only whole-tree checks | Python 3.11+, standard library |
| `fetch/` | `gwt-fetch`, archive downloads | Python 3.11+, `requests` |
| `ledger/` | `gwt-ledger`, shared search ledger | Python standard library |
| `fscache/` | `gwt-fscache`, HTTP cache | Python standard library |
| `gate/` | `gwt-gate`, shared request pace | Python standard library |
| `rawstore/` | `gwt-rawstore`, images in S3, manifest in git | Python standard library |
| `spanish-poller/` | `gwt-spanish-poller`, Spain-only sede and DEHú watcher | Node 18+, Playwright |
| `ocr-bench/` | OCR, HTR and LLM reading benchmark | Python, one venv per engine |

## Repository rules

- Licence: GPL-3.0-or-later. Commits follow Conventional Commits.
- Every change reaches `main` through a pull request with green CI. Never force push.
- No AI attribution anywhere: no `Co-Authored-By` trailers, no "Generated with" lines.
- **No private data, ever.** Examples and test data are invented: no real people's names from anyone's
  tree, no real case or expediente numbers, no real tree ids, hostnames, IPs, accounts or emails.
  Public records (a Gaceta page, a published parish entry) are fine with their source.
- Never commit a secret, a certificate or a config file with credentials.

## Test

```bash
cd apply && python3 -m pytest -q
cd check && python3 -m pytest -q
cd fetch && python3 -m pytest -q
cd ledger && python3 -m unittest test_gwt_ledger
cd fscache && python3 -m unittest test_gwt_fscache
cd gate && python3 -m unittest test_gwt_gate
cd rawstore && python3 -m unittest test_gwt_rawstore
cd spanish-poller && node --test
```

<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:6cd5cc61 -->
## Beads Issue Tracker

This project uses **bd (beads)** for issue tracking. Run `bd prime` to see full workflow context and commands.

### Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work
bd close <id>         # Complete work
```

### Rules

- Use `bd` for ALL task tracking — do NOT use TodoWrite, TaskCreate, or markdown TODO lists
- Run `bd prime` for detailed command reference and session close protocol
- Use `bd remember` for persistent knowledge — do NOT use MEMORY.md files

**Architecture in one line:** issues live in a local Dolt DB; sync uses `refs/dolt/data` on your git remote; `.beads/issues.jsonl` is a passive export. See https://github.com/gastownhall/beads/blob/main/docs/SYNC_CONCEPTS.md for details and anti-patterns.

## Agent Context Profiles

The managed Beads block is task-tracking guidance, not permission to override repository, user, or orchestrator instructions.

- **Conservative (default)**: Use `bd` for task tracking. Do not run git commits, git pushes, or Dolt remote sync unless explicitly asked. At handoff, report changed files, validation, and suggested next commands.
- **Minimal**: Keep tool instruction files as pointers to `bd prime`; use the same conservative git policy unless active instructions say otherwise.
- **Team-maintainer**: Only when the repository explicitly opts in, agents may close beads, run quality gates, commit, and push as part of session close. A current "do not commit" or "do not push" instruction still wins.

## Session Completion

This protocol applies when ending a Beads implementation workflow. It is subordinate to explicit user, repository, and orchestrator instructions.

1. **File issues for remaining work** - Create beads for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **Handle git/sync by active profile**:
   ```bash
   # Conservative/minimal/default: report status and proposed commands; wait for approval.
   git status

   # Team-maintainer opt-in only, unless current instructions forbid it:
   git pull --rebase
   git push
   git status
   ```
5. **Hand off** - Summarize changes, validation, issue status, and any blocked sync/commit/push step

**Critical rules:**
- Explicit user or orchestrator instructions override this Beads block.
- Do not commit or push without clear authority from the active profile or the current user request.
- If a required sync or push is blocked, stop and report the exact command and error.
<!-- END BEADS INTEGRATION -->

<!-- BEGIN BEADS CODEX SETUP: generated by bd setup codex -->
## Beads Issue Tracker

Use Beads (`bd`) for durable task tracking in repositories that include it. Use the `beads` skill at `.agents/skills/beads/SKILL.md` (project install) or `~/.agents/skills/beads/SKILL.md` (global install) for Beads workflow guidance, then use the `bd` CLI for issue operations.

### Quick Reference

```bash
bd ready                # Find available work
bd show <id>            # View issue details
bd update <id> --claim  # Claim work
bd close <id>           # Complete work
bd prime                # Refresh Beads context
```

### Rules

- Use `bd` for all task tracking; do not create markdown TODO lists.
- Run `bd prime` when Beads context is missing or stale. Codex 0.129.0+ can load Beads context automatically through native hooks; use `/hooks` to inspect or toggle them.
- Keep persistent project memory in Beads via `bd remember`; do not create ad hoc memory files.

**Architecture in one line:** issues live in a local Dolt DB; sync uses `refs/dolt/data` on your git remote; `.beads/issues.jsonl` is a passive export. See https://github.com/gastownhall/beads/blob/main/docs/SYNC_CONCEPTS.md for details and anti-patterns.
<!-- END BEADS CODEX SETUP -->
