# yoniizu-copilot-config

Personal, user-level GitHub Copilot configuration: instructions, prompts, agents, and skills.

This repo is the working tree of `~/.copilot/` on my dev machine. A similar layout can be used
at a repo level under `.github/`.

## Layout

| Path | Purpose |
|------|---------|
| `copilot-instructions.md` | Always-loaded project context. |
| `*.instructions.md` / `*.prompt.md` / `*.agent.md` / `*.skill.md` | Top-level templates and standalone definitions. |
| `skills/<name>/SKILL.md` | Per-skill instructions, invoked when their description matches the task. |
| `team-portal/` | Portal implementation, assets, tests, documentation and deployment files. Private runtime state lives outside the repo. |

## Skills

- `build-fw` — Build FW Zephyr applications.
- `checkout-pr-branches` — Multi-repo PR checkout.
- `d2d-ip`, `pcie-ep-ip`, `su-ip` — IP design/usage Q&A.
- `jira-hsi-planning` — HSI Jira planning assistant.
- `manager-brief` — Weekly 1:1 brief for upward reporting.
- `notebook` — Personal dev notebook (ideas, conventions).
- `one-on-one` — 1:1 meeting lifecycle.
- `pcie-ep-ip`, `ras-manageability` — Internal spec/RTL Q&A.
- `pr-monitor` — Pending feature PR tracker.
- `portal-maintainer` — Team Portal runtime, UI/API coordination, deployment and append-only maintenance journal.
- `ras-manageability` — RAS / manageability Q&A.
- `task-manager` — Structured work tracking backed by `~/tasks/tasks.db`.
- `team-planner`, `team-weekly` — FW team planning and weekly meeting lifecycle.
- `wip-workspace` — Workspace-level git operations via `wip_funcs.sh`.

## Private local data

Personal records, private operational notes, credentials/configuration and
runtime state belong outside this repository, under `~/.copilot-private/` or
their owning stores such as `~/tasks/` and `~/.pilot/`. Technical IP/project
knowledge remains part of the shared repository. Skill data files under
`~/.copilot-private/skills/` are read and written by the corresponding public
skill scripts.

## Excluded from version control

The Team Portal source lives in [team-portal/](team-portal/); only the maintainer's
instructions live in `skills/portal-maintainer/SKILL.md`. `task-manager/tm.py`
retains task/review operations and compatibility `serve`/`dashboard` commands.
Domain skills own separate SQLite stores under `~/tasks/`. The portal calls
their APIs rather than owning their data. Legacy JSON/Markdown stores are
migration inputs and explicit exports, not writable sources after cutover.
See [portal ownership](team-portal/references/architecture.md).

Runtime state and conversation data are excluded via `.gitignore`:
`logs/`, `session-state/`, `ide/`, `session-store.db*`, `command-history-state.json`,
`vscode.session.metadata.cache.json`.
