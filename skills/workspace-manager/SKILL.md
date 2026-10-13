---
name: workspace-manager
description: "Manage physical workspace-slot occupancy (which ~/projects/<project>/<slot> checkout is in use and by what). Use when: taking up / grabbing / releasing a workspace, occupying a slot for an ad-hoc purpose (monitor PRs, debug an issue, run an experiment) WITHOUT logging a task, checking which slots are free/occupied, reassigning a slot. For logging a task itself, use the task-manager skill (which delegates workspace holds here)."
argument-hint: "Action (list/status/occupy/release/whereami) and slot + holder details"
---

# Workspace Manager

You are the **Workspace Manager** — the single source of truth for **workspace
slot occupancy**, backed by `~/tasks/workspaces.json`.

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

## CLI cheatsheet

Run as: `python3 ~/.copilot/skills/workspace-manager/wm.py <subcommand> ...`

| Subcommand | Purpose | Notes |
|------------|---------|-------|
| `list [--project NAME]` | Show every slot and its holder | Default project: `iridium`. Slots come from the project dir plus any occupied slot. |
| `status <slot>` | Show one slot's holder | Normalizes sub-paths (`one/fw` → `one`). |
| `occupy <slot> --task T-NNN [--detail PATH] [--force]` | Give a slot to a task | Refuses a busy slot unless `--force` (which displaces the holder). |
| `occupy <slot> --label "purpose" [--tag TAG] [--force]` | Grab a slot for an **ad-hoc** purpose | `--tag` ∈ `monitor`, `debug`, `development`, `experiment`, `other` (default `other`). |
| `release <slot>` / `free <slot>` | Free a slot | No-op if already free. |
| `whereami [--cwd PATH]` | Report the slot for the current (or given) directory | Resolves `~/projects/<project>/<slot>/...`. |
| `migrate [--keep-fields]` | One-time import of legacy per-task `workspace` holds | Moves active holds into this store and strips `workspace`/`workspace_since` from `tasks.json` (unless `--keep-fields`). |

## Store schema (single source of truth)

`~/tasks/workspaces.json`:

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

1. **One holder per slot.** `occupy_*` refuses a busy slot unless `--force`
   (which displaces the current holder and returns it as `displaced`).
2. **Never lose task context silently.** When a `--force` occupy displaces a
   *task* holder, the caller (`tm.py`) notes the displacement on that task.
   When you displace via the CLI, tell the user who you displaced.
3. **Task holds mirror task lifecycle.** `task-manager` releases a task's slot
   automatically on `done`/`cancelled` and when a review's ball moves to the
   author / re-review. Don't hand-edit `workspaces.json` for task holds — go
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
