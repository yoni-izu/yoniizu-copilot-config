---
name: workspace-manager
description: "Manage physical workspace-slot occupancy (which ~/projects/<project>/<slot> checkout is in use and by what). Use when: taking up / grabbing / releasing a workspace, occupying a slot for an ad-hoc purpose (monitor PRs, debug an issue, run an experiment) WITHOUT logging a task, checking which slots are free/occupied, reassigning a slot. For logging a task itself, use the task-manager skill (which delegates workspace holds here)."
argument-hint: "Action (list/status/occupy/release/whereami) and slot + holder details"
---

> **Portal handoff:** For Team Portal/dashboard questions, feature requests,
> UI/API changes, bugs or operations, load
> [portal-maintainer](../portal-maintainer/SKILL.md). Domain records and
> workflows remain owned by this skill; coordinate mixed changes with the maintainer.

# Workspace Manager

You are the **Workspace Manager** — the single source of truth for **workspace
slot occupancy**, backed by `~/tasks/workspaces.db`.

A *slot* is a physical checkout directory under `~/projects/<project>/<slot>`
(e.g. `one`, `two`, … `six`, plus any other dir like `main`). At most **one**
holder occupies a slot at a time. A holder is either:

- a **task** (`type: task`) — a logged [task-manager](../task-manager/SKILL.md)
  item that owns the slot while it's being worked, **or**
- an **ad-hoc** hold (`type: adhoc`) — any reason you grab a workspace for that
  you don't want to log a dedicated task for: *monitor PRs*, *debug an issue*,
  *run an experiment*, etc.

This split lets you say *"taking up workspace six to monitor PRs"* or *"grab
four to debug the LTSSM hang"* **without** creating a task, while the store still
knows every slot is accounted for.

A companion CLI + library at `~/.copilot/skills/workspace-manager/wm.py` owns all
mutations. `task-manager`'s `tm.py` **imports** `wm.py` and routes every
workspace change through it, so occupancy stays consistent no matter which skill
touched it.

## Local and remote workspace lifecycle

The roster in `~/tasks/workspaces.db` is authoritative for tracked locations. Every
entry has an immutable `id`: `WS-L-<uuid>` for local or `WS-R-<uuid>` for remote.
The L/R field describes location relative to this controller, not an EC2 type.
Existing local slot keys remain compatible; new remote/colliding entries use
their ID as the slot key. APIs accept either the slot key or its ID. Labels,
directory basenames and group names are not identities.

Remote records add `target`, a registered `mctl` machine name, and an absolute or
`~/` remote path. Never test a remote path on the local filesystem. Registration
does not contact or start the target. First refresh resolves its canonical path
and adopts a pre-existing on-machine ID if another controller registered it.
Use the same target name for a machine consistently; shared NFS paths may refer
to the same physical checkout on several hosts.

- **Refresh** at the top of the portal reads saved workspace data only.
  **Check local status** checks tracked local workspaces; **Check VM status**
  checks tracked remote workspaces only. Each card's check is scoped to that card.
  Live checks are read-only with respect to checkouts and persist owner readings.
  Remote operations run through `mctl workspace`, never a separate SSH config.
- **Clean** means every manifest repository is at its declared revision, with
  no modified/untracked files or local-only commits. Missing/unverifiable
  revisions and scan failures fail closed. Per-project pinned revisions count;
  the manifests checkout is checked separately. The configured roster revision
  must agree with the selected manifest's default revision.
- **Occupy** requires a fresh clean check and no mirror relationship. An
  existing holder cannot be displaced with `--force`; free first. A task cannot
  move to another slot until its previous slot has been freed successfully.
- **Sync**, for an independent workspace, refuses an occupied or non-clean
  checkout and reports why. For an active mirror it preserves development branch
  choices, syncs non-development repositories to manifest revisions and updates
  development branches from their matching origin branches. Dirty/local-only
  work is never reset, rebased or silently discarded; updates are fast-forward
  only. Failed/in-progress mirrors must be re-mirrored or freed, not synced.
- **Free** returns all repositories to their manifest revisions, then runs
  `octl ws sync --no-plugins` and verifies the result. Holder and mirror state
  clear only after success. Failures keep the reservation and are visible.
  `release(..., reset=False)` is internal to the portal worker after it has
  completed that physical operation, not a shortcut for callers.

The portable `workspace_runtime.py` uses `wip scan`/`wip forall ws` for
multi-repository operations, verifies complete manifest coverage, and checks
each repository's result (the wip wrapper's exit status alone is insufficient).
Temporary scans preserve the user's existing WIP branch plan; concurrent
changes are reported rather than overwritten. Undeclared source repositories
and missing manifest repositories block checkout-changing actions.
Only supported, fully resolved manifests are acted on; unsupported manifest
elements fail explicitly. It must not be used for arbitrary shell commands.

### Mirror state and history

On each machine, `~/.mctl/workspaces/<sha256(canonical-path)>.json` stores the
checkout ID and `mirror` relationship; the sibling `.jsonl` is append-only
action history. This is outside source repositories and survives portal
restarts, untracking and failed connections. `mctl mirror` creates both IDs,
records `source_id`, `source_path`, `source_host` and `syncing` before changing
branches, and marks `active` only after verification. Failure is not active.
Successful `mctl release` or portal Free clears the marker; the next mirror
reads that marker instead of inferring use from an old build log.

Controller readings and append-only action logs live in
`~/tasks/workspace-state/<ID>.json` / `.jsonl`. `checked` advances only after a
successful status read; failed attempts retain the previous reading with an
explicit error and attempt timestamp. Mirror state is distinct from task
occupancy: a mirror reserves capacity but does not move the source task's hold.
The card's action log shows controller events and the last-fetched machine
events; full history remains on disk.

Additional CLI commands:

```bash
wm.py identity PATH [--target MACHINE]       # find/register a stable identity
wm.py refresh SLOT_OR_ID                    # checks only this workspace
wm.py sync SLOT_OR_ID                       # guarded lifecycle operation
wm.py log SLOT_OR_ID                        # controller action history
wm.py mirror-state TARGET PATH --source-id ID --source-path PATH \
  --state syncing|active|failed|released     # mctl lifecycle integration
```

The owner database uses transactions alongside the process/thread lifecycle lock.
`workspaces.json` is a backed-up migration input/explicit export, never a second writer.
Tests:
`python3 -m unittest discover -s ~/.copilot/skills/workspace-manager -p 'test_*.py'`.

## CLI cheatsheet

Run as: `python3 ~/.copilot/skills/workspace-manager/wm.py <subcommand> ...`

| Subcommand | Purpose | Notes |
|------------|---------|-------|
| `list [--project NAME]` | Show every slot and its holder | Default project: `iridium`. Slots come from the project dir plus any occupied slot. |
| `status <slot>` | Show one slot's holder | Normalizes sub-paths (`one/fw` → `one`). |
| `occupy <slot> --task T-NNN [--detail PATH] [--force]` | Give a slot to a task | Requires a fresh clean baseline and a free slot; `--force` cannot displace a hold. |
| `occupy <slot> --label "purpose" [--tag TAG] [--force]` | Grab a slot for an **ad-hoc** purpose | `--tag` ∈ `monitor`, `debug`, `development`, `experiment`, `other` (default `other`). |
| `release <slot>` / `free <slot>` | Free a slot | No-op if already free. |
| `whereami [--cwd PATH]` | Report the slot for the current (or given) directory | Resolves `~/projects/<project>/<slot>/...`. |
| `migrate [--keep-fields]` | One-time import of legacy per-task `workspace` holds | Moves active holds through owner APIs and removes legacy fields from task records (unless `--keep-fields`). This is distinct from database import. |

## Owner API shape

The following JSON describes the owner API representation, not an editable database file:

```json
{
  "version": 1,
  "slots": {
    "four": { "holder": { "type": "task",  "task_id": "T-142", "detail": "four", "since": "2026-07-30", "notes": [] } },
    "six":  { "holder": { "type": "adhoc", "label": "monitor PRs", "tag": "monitor", "since": "2026-08-05", "notes": [] } }
  }
}
```

- A slot is **free** when it is absent from `slots` or its `holder` is `null`.
- `since` is the acquire date (`YYYY-MM-DD`). Moving a task between slots
  preserves its original acquire date.
- `detail` (task holders) keeps the sub-path when a task lives in a sub-dir of
  the slot (e.g. `one/fw`); it collapses to the slot root for occupancy.

## Library API (imported by `tm.py`)

`import wm` exposes the choke-points other skills should use:

- `wm.get_holder(slot)` → holder dict or `None`
- `wm.is_free(slot)` → bool
- `wm.all_holders()` → `{slot: holder}` for every occupied slot
- `wm.slot_of_task(task_id)` → slot name or `None`
- `wm.occupy_task(slot, task_id, detail=None, force=False)` → `(ok, msg, displaced)`
- `wm.occupy_adhoc(slot, label, tag="other", force=False)` → `(ok, msg, displaced)`
- `wm.release(slot)` → `(ok, released_holder)`
- `wm.release_task(task_id)` → freed slot or `None`
- `wm.slot_from_cwd(cwd=None)` → slot name or `None`
- `wm.normalize_slot(ws)` → root slot name

## Rules

1. **One holder per slot.** `occupy_*` refuses a busy slot; successfully free
   the previous hold before assigning another task or purpose.
2. **Never lose task context silently.** Occupying a held slot is refused,
   including with `--force`. Free the old holder successfully first.
3. **Task holds mirror task lifecycle.** `task-manager` releases a task's slot
   automatically on `done`/`cancelled` and when a review's ball moves to the
   author / re-review. Don't hand-edit workspace storage or exports for task holds — go
   through `tm.py` (`assign`/`release`/`done`) or `wm.release_task`.
4. **Ad-hoc holds are yours to manage.** They have no task lifecycle; free them
   when you're done (`wm.py release <slot>` or the dashboard **Set free**).
5. **Timestamps** are `YYYY-MM-DD` from the system date.
6. **Sub-paths collapse to the slot root** for occupancy (`one/fw` occupies
   `one`); the full path is kept in `detail` for display.
7. **Ad-hoc label authorship.** When the *user* manually occupies a slot, use the
   label exactly as they give it — don't rewrite it. When *you (an agent)* occupy
   a slot on their behalf, **devise a concise, topical label from the work you're
   about to do** rather than asking for one. Match the dashboard header style —
   `"<subject> <action> (<ref>)"` — and include any ticket/PR id you already know:
   - reviewing a PR → `"AER PR review (T-123)"` / `"PCIe middleware PR review"`
   - debugging a ticket → `"AER completion-timeout debug (IRIDIUM-1234)"`
   - an experiment → `"bisect LTSSM regression (experiment)"`
   Pick the `--tag` that fits (`monitor`/`debug`/`development`/`experiment`/`other`).

## Dashboard

The task-manager dashboard (`tm.py dashboard` / `serve`) reads this store for its
**Workspace State** tab. In the served (editable) page each slot can be:
- **occupied with a task** (pick from active tasks), or
- **held ad-hoc** (type a purpose + pick a tag), or
- **set free**.

These post to `/api/workspace/<slot>` and apply through `wm.py`, so the CLI, the
dashboard, and every other skill always agree on who holds what.

## Relationship to other skills

- **task-manager** — owns tasks; delegates all slot holds here. Its `assign`,
  `release`, `workspaces` subcommands and auto-assign-on-add all route through
  `wm.py`.
- **review-mgr / pr-monitor** — check and claim slots via `tm.py
  workspaces`/`assign`/`release` (which now delegate here). Before occupying a
  slot for a review/feature, confirm it isn't already held (task *or* ad-hoc).
