---
name: wip-workspace
description: "Workspace-level git operations using the `wip` CLI. Use when: user wants to save/load/restore workspace state, checkout/push/pull/stash/pop across repos, scan workspaces, align branches, return to upstream, list saved states, diff workspace, hard reset, run forall commands, list branch status, or any multi-repo git operation at workspace or WIP level."
argument-hint: "Action (scan/mode/save/load/list/status/info/forall or checkout/push/pull/stash/pop/align/diff/return/hard-reset/set-upstream/depends-on) and optionally: save name, scope (ws or all)"
---

> **Portal handoff:** For Team Portal/dashboard questions, feature requests,
> UI/API changes, bugs or operations, load
> [portal-maintainer](../portal-maintainer/SKILL.md). Domain records and
> workflows remain owned by this skill; coordinate mixed changes with the maintainer.

# WIP Workspace Manager

Manage multi-repo workspaces using the `wip` CLI (`~/bin/wip`). It performs git
operations across all repos in a single workspace (`ws`) or across every
workspace in the WIP directory (`all`).

> Legacy note: this replaces the old sourced `~/wip_funcs.sh` functions
> (`wip_scan`, `ws_checkout`, `wip_hard_reset`, …). `wip_funcs.sh` still exists
> for backward compatibility, but prefer the `wip` CLI everywhere.

## Prerequisites

`wip` is a normal executable on `PATH` (`~/bin/wip`) — nothing to source. Just
`cd` to the correct directory and run `wip <cmd>`.

## Key Concepts

- **WIP root**: The top-level directory containing one or more workspaces. `wip scan` treats `$PWD` as the WIP root.
- **Workspace (ws)**: A subdirectory of the WIP root with a `.repo/` manifest. Each holds multiple git repos.
- **State file**: `wip scan` writes the scanned map to `<wip_root>/.wip/state`. Every later `wip` command finds it by walking **up** from `$PWD` (like git finds `.git`), so you can run commands from anywhere inside the tree.
- **Snapshots**: Named saves live in `~/.wip/saved/<name>.wip` (via `wip save`/`wip load`/`wip list`). They store `ws_name:repo_name` → `path:branch`, no absolute paths.
- **Scopes**: `wip ws <action>` = current workspace only; `wip all <action>` = every workspace under the root. A bare `wip <action>` defaults to `ws`.

## Working modes (`author` vs `mirror`)

Each WIP root has a **mode** that governs `align`, `set-upstream`, and `push`:

- **`author`** — local is the source of truth. `align` rebases dev branches
  onto `origin/main`; then `push` a ready-to-merge branch. Used on the dev box.
- **`mirror`** — remote is the source of truth. `align` resets each repo hard to
  the latest remote (`origin/<dev-branch>` for repos under development, else
  `origin/main`); `push` is **disabled** (override with `--force-mirror`). Used
  on VMs / Palladium / any consumer machine. `align` in mirror mode is
  destructive (reset --hard) so it needs `--yes` non-interactively.

Mode resolution (per root): an explicit mode pinned in `.wip/state` wins;
otherwise the machine default in `~/.wip/config` (`default_mode=author|mirror`)
applies; if that file is absent the default is **`mirror`**. So freshly deployed
machines are `mirror` by default, and only the dev box sets `default_mode=author`.

```bash
wip mode                    # show current mode + where it came from
wip mode author|mirror      # pin this root's mode into .wip/state
wip mode default            # clear the pin -> inherit the machine default
wip scan --mode mirror      # scan and pin a mode in one step
```

## Scanning — Scan Before Operating

`wip scan` records the current branch of every repo into `<wip_root>/.wip/state`.
Do this once per WIP dir (or after workspaces are added); subsequent commands
reuse the state file automatically.

### Scan the WIP directory (all workspaces):
```bash
cd <wip_root>
wip scan
```

### Restore a saved snapshot as the scanned state:
```bash
cd <wip_root>
wip scan <snapshot_name>
```

There is no separate per-workspace scan — one `wip scan` at the root captures
every workspace; target a single one later with the `ws` scope.

## Save / Load / List (State Persistence)

### Save current workspace state
When the user says "save workspace state", "snapshot workspace", etc.:

```bash
cd <wip_root>
wip scan          # ensure state is current
wip save <name>   # -> ~/.wip/saved/<name>.wip
wip status        # confirm what was captured
```
If the user gives no name, generate one from the WIP dir name or ask.

### List saved states
```bash
wip list
```

### Load / restore a saved state
When the user says "load workspace", "restore state", etc.:

```bash
cd <wip_root>
wip load <name>     # restores into <wip_root>/.wip/state
wip all checkout    # check out the recorded branches
```

If the user doesn't specify which save to load:
- Run `wip list` first to show available saves.
- If there's only one, load it. If multiple, ask which.
- "Load last" = most recently modified snapshot:
  ```bash
  ls -t ~/.wip/saved/*.wip 2>/dev/null | head -1 | xargs -I{} basename {} .wip
  ```

### Return to clean/upstream state
When the user says "return to upstream", "reset to base branches", etc.:

```bash
cd <wip_root>
wip scan            # or: wip load <name>
wip all return      # check out each repo's upstream branch
```

## Branch Status — Which Repos Are on Dev vs Base

When the user asks "which repos have changes", "show branch status", etc.:

```bash
cd <wip_root>
wip scan
wip status          # WIP root + workspaces (with upstream) + repo->path:branch
```

Parse `wip status` to categorize repos:

**Development branches** — where `branch != upstream` and `branch != main`
**Base/upstream branch** — where `branch == upstream` or `branch == main`

Present dev-branch repos individually and summarize base-branch repos with a
count unless the user wants the full list. For dev branches specifically:

```bash
wip all depends-on   # DEPENDS_ON: line of every dev/* branch (whole WIP)
wip ws  depends-on   # non-upstream branches in the current workspace
```

## Git Operations (Bulk)

Every action takes a scope:
- **`wip all <action>`**: ALL repos across ALL workspaces in the WIP directory.
- **`wip ws <action>`**: repos in the CURRENT workspace only (`cd` into it first).
- **`wip <action>`**: shorthand for `wip ws <action>`.

### Available actions:

| User says | Command | What it does |
|-----------|---------|--------------|
| checkout branches | `wip all checkout` / `wip ws checkout` | Check out each repo to its recorded branch |
| push all | `wip all push` / `wip ws push` | Force-push non-upstream branches (needs `--yes`; `author` mode only) |
| pull all | `wip all pull` / `wip ws pull` | Pull latest for non-upstream branches |
| align / rebase | `wip all align` / `wip ws align` | `author`: rebase on `origin/main`; `mirror`: reset --hard to latest remote |
| set upstream | `wip all set-upstream` / `wip ws set-upstream` | `author`: track `origin/main`; `mirror`: track `origin/<dev-branch>` |
| stash all | `wip all stash` / `wip ws stash` | git stash in repos with non-upstream branches |
| pop stash | `wip all pop` / `wip ws pop` | git stash pop in all repos |
| diff | `wip all diff` / `wip ws diff` | Write diff patches vs upstream to `<ws>/diff/` |
| return to upstream | `wip all return` / `wip ws return` | Check out upstream branch in all repos |
| hard reset | `wip all hard-reset` / `wip ws hard-reset` | Hard reset non-upstream branches to origin (needs `--yes`) |

### Pattern for any git operation:
```bash
cd <wip_root>
wip scan             # or: wip load <name>
wip all <action>     # or: wip ws <action> from inside a workspace
```

### Destructive actions
`push` (force-push) and `hard-reset` require confirmation. On a TTY `wip`
prompts; in scripts/non-interactive use pass `--yes` (or `-y`):
```bash
wip all hard-reset --yes
```

## Utility Commands

### Print current state:
```bash
wip status   # WIP root, workspaces + upstreams, and every repo -> path:branch
```

### Run a command in all repos:
```bash
wip forall 'git status'                 # all workspaces (default)
wip forall all 'git log --oneline -3'   # explicit all scope
wip forall ws 'git status'              # current workspace only
```

### Show dependencies:
```bash
wip all depends-on   # repos with dev/* branches across all workspaces
wip ws  depends-on   # non-upstream branches in the current workspace
```

## Scope Detection

- Inside a workspace subdirectory + "this workspace" → `wip ws <action>`.
- "all workspaces", "everything", or at WIP root level → `wip all <action>`.
- Default to `wip all` when scope is ambiguous and the operation is broad;
  a bare `wip <action>` defaults to `ws`.

## Important Notes

- `wip` is at `~/bin/wip` (on `PATH`); no sourcing needed.
- State lives at `<wip_root>/.wip/state`, discovered by walking up from `$PWD`. Run `wip scan` once at the root; you can then run actions from any subdirectory.
- Snapshots live in `~/.wip/saved/`. `wip load <name>` takes a bare name.
- `wip scan` auto-detects workspaces by looking for `.repo/metadata.json` in subdirectories.
- Action names are hyphenated: `hard-reset`, `set-upstream`, `depends-on`.
- Run `wip help` (or `wip help <action>`) for built-in documentation of every command and flag.

