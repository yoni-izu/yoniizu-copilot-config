---
name: wip-workspace
description: "Workspace-level git operations using wip_funcs.sh. Use when: user wants to save/load/restore workspace state, checkout/push/pull/stash/pop across repos, scan workspaces, align branches, return to upstream, list saved states, diff workspace, hard reset, run forall commands, list branch status, or any multi-repo git operation at workspace or WIP level."
argument-hint: "Action (save/load/scan/checkout/push/pull/stash/pop/align/diff/return/hard_reset/list/forall/status) and optionally: save name, scope (ws or wip)"
---

# WIP Workspace Manager

Manage multi-repo workspaces using `~/wip_funcs.sh`. This script provides functions to perform git operations across all repos in a workspace (ws) or across the entire WIP directory.

## Prerequisites

`~/wip_funcs.sh` is already sourced via `~/.bashrc` — do NOT re-source it. Just `cd` to the correct directory and run the commands directly.

## Key Concepts

- **WIP root**: The top-level working directory containing one or more workspaces. Set automatically to `$PWD` on first function call.
- **Workspace (ws)**: A subdirectory of the WIP root containing a `.repo/` folder with a manifest. Each workspace has multiple git repos.
- **wip_info**: Associative array mapping `ws_name:repo_name` → `path:branch` for all repos across all workspaces.
- **ws_upstream_info**: Associative array mapping `ws_name` → upstream branch (e.g. `fb-iridium-2_9_1_1p0_983` or `main`).
- **Save files**: Stored in `~/wip/` as `<name>.wip` files. They persist the wip_info and ws_upstream_info arrays.

## Scanning — Always Scan Before Operating

Before any save/checkout/push/pull/etc. operation, the workspace state must be scanned into memory. Always scan first if operating from scratch:

### Scan entire WIP directory (all workspaces):
```bash
cd <wip_root>
wip_scan
```

### Scan a single workspace:
```bash
cd <wip_root>/<ws_name>
ws_scan
```

### Load a saved state and scan:
```bash
cd <wip_root>
wip_scan <save_name>
```

## Save / Load / List (State Persistence)

### Save current workspace state
When the user says "save workspace state", "save current state", "snapshot workspace", etc.:

1. `cd` to the WIP root.
2. Scan the workspace(s): `wip_scan`
3. Save with a name: `wip_save <name>`
   - If the user doesn't provide a name, generate one from the WIP directory name or ask.
   - Files are saved to `~/wip/<name>.wip`
4. Confirm what was saved: `wip_print_info`

```bash
cd <wip_root>
wip_scan
wip_save <name>
```

### List saved states
When the user says "list saves", "show saved workspaces", "what states do I have", etc.:

```bash
wip_list
```

### Load / restore a saved state
When the user says "load workspace", "restore state", "load last workspace", etc.:

1. `cd` to the WIP root.
2. Load the state: `wip_load <name>` (name without `.wip` extension)
3. Set the wip_root: the current directory becomes the WIP root.
4. Checkout all branches: `wip_checkout`

```bash
cd <wip_root>
wip_load <name>
wip_checkout
```

If the user doesn't specify which save to load:
- Run `wip_list` first to show available saves.
- If there's only one, load it. If multiple, ask the user which one.
- "Load last" means load the most recently modified `.wip` file:
  ```bash
  ls -t ~/wip/*.wip 2>/dev/null | head -1 | xargs -I{} basename {} .wip
  ```

### Return to clean/upstream state
When the user says "go back to clean workspace", "return to upstream", "reset to base branches", etc.:

1. `cd` to the WIP root.
2. Scan (or load saved state if available): `wip_scan`
3. Return all repos to their upstream branches: `wip_return`

```bash
cd <wip_root>
wip_scan
wip_return
```

## Branch Status — Which Repos Are on Dev vs Base

When the user says "which repos have changes", "show branch status", "what's on dev branches", "list repo branches", etc.:

After scanning (`wip_scan` or `wip_load`), iterate `wip_info` and compare each repo's branch against its workspace upstream from `ws_upstream_info`. Present results in two groups:

```bash
cd <wip_root>
wip_scan
wip_print_info
wip_print_upstream_info
```

Then parse the output to categorize repos:

**Repos on development branches** — where `branch != upstream_branch` and `branch != main`
**Repos on base/upstream branch** — where `branch == upstream_branch` or `branch == main`

Present as a formatted summary, e.g.:

```
Development branches:
  ws_name:repo_name  →  dev/my-feature
  ws_name:repo_name  →  dev/fix-bug

Base branch (fb-iridium-2_9_1_1p0_983):
  ws_name:repo_name
  ws_name:repo_name
  ... (N repos)
```

For conciseness, list dev-branch repos individually but summarize base-branch repos with a count unless the user asks for the full list.

You can also use `wip_depends_on` or `ws_depends_on` to show repos with `dev/*` branches specifically.

## Git Operations (Bulk)

All operations work at two scopes:
- **`wip_<action>`**: Operates on ALL repos across ALL workspaces in the WIP directory.
- **`ws_<action>`**: Operates on repos in the CURRENT workspace only (must `cd` into the workspace first).

### Available actions:

| User says | Command | What it does |
|-----------|---------|--------------|
| checkout branches | `wip_checkout` / `ws_checkout` | Checkout each repo to its recorded branch |
| push all | `wip_push` / `ws_push` | Force-push all non-upstream branches to origin |
| pull all | `wip_pull` / `ws_pull` | Pull latest for all non-upstream branches |
| align / rebase | `wip_align` / `ws_align` | Rebase all branches on their upstream (fetch + rebase) |
| set upstream | `wip_set_upstream` / `ws_set_upstream` | Set tracking branch for all repos |
| stash all | `wip_stash` / `ws_stash` | Git stash in all repos with non-upstream branches |
| pop stash | `wip_pop` / `ws_pop` | Git stash pop in all repos |
| diff | `wip_diff` / `ws_diff` | Generate diff patches against upstream for all repos |
| return to upstream | `wip_return` / `ws_return` | Checkout upstream branch in all repos |
| hard reset | `wip_hard_reset` / `ws_hard_reset` | Hard reset all non-upstream branches to origin |

### Pattern for any git operation:
```bash
cd <wip_root>
wip_scan          # or wip_load <name> if restoring a saved state
wip_<action>      # or ws_<action> if inside a workspace directory
```

## Utility Functions

### Print current state:
```bash
wip_print_info          # Show all repo → path:branch mappings
wip_print_upstream_info # Show all workspace → upstream branch mappings
```

### Run a command in all repos:
```bash
wip_forall "git status"
wip_forall "git log --oneline -3"
```

### Show dependencies:
```bash
wip_depends_on   # List repos with dev/* branches across all workspaces
ws_depends_on    # List repos with non-upstream branches in current workspace
```

### Get names:
```bash
wip_get_name  # Print the WIP directory name
ws_get_name   # Print the current workspace name (based on PWD)
```

## Scope Detection

- If the user is inside a workspace subdirectory and says "this workspace" or similar, use `ws_*` commands.
- If the user says "all workspaces", "everything", or is at the WIP root level, use `wip_*` commands.
- Default to `wip_*` (all workspaces) when scope is ambiguous.

## Important Notes

- `~/wip_funcs.sh` is sourced via `~/.bashrc` — no need to re-source.
- `wip_root` is set to `$PWD` on the first function call. Always `cd` to the correct directory before scanning.
- Save files in `~/wip/` are portable — they store `ws_name:repo_name` → `path:branch`, no absolute paths.
- `wip_scan` auto-detects workspaces by looking for `.repo/metadata.json` in subdirectories.
- For `wip_load`, both bare names (`my_save`) and full paths (`~/wip/my_save.wip`) work.
- The script uses bash aliases for `ws_<action>` and `wip_<action>`, so they work directly in the terminal.
