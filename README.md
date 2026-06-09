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
- `ras-manageability` — RAS / manageability Q&A.
- `task-manager` — Structured work tracking backed by `~/tasks/tasks.json`.
- `team-planner`, `team-weekly` — FW team planning and weekly meeting lifecycle.
- `wip-workspace` — Workspace-level git operations via `wip_funcs.sh`.

## Excluded from version control

Runtime state and conversation data are excluded via `.gitignore`:
`logs/`, `session-state/`, `ide/`, `session-store.db*`, `command-history-state.json`,
`vscode.session.metadata.cache.json`.

