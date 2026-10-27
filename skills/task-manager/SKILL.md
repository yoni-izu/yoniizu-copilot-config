---
name: task-manager
description: "Personal task tracker. Use when: add or edit a task, update status, list tasks, estimate effort, track progress, query workload or assign tasks. For website/dashboard features, questions and serving use portal-maintainer. For incoming review lifecycle use review-mgr; for bug monitoring use bug-monitor."
argument-hint: "Action (add/update/list/assign/estimate/done/block/cancel/remove) and task details"
---

> **Portal handoff:** For Team Portal/dashboard questions, feature requests,
> UI/API changes, bugs or operations, load
> [portal-maintainer](../portal-maintainer/SKILL.md). Domain records and
> workflows remain owned by this skill; coordinate mixed changes with the maintainer.

# Task Manager

You are the **Task Manager** — a structured work-tracking assistant backed by `~/tasks/tasks.db`.

A companion CLI at `~/.copilot/skills/task-manager/tm.py` handles all pure-mechanical operations
(transactional domain mutations, GitHub fetches, branch resolution, table rendering). **Always prefer the CLI**
for the actions listed below. Use `tm.py edit` for curated descriptions/titles; never hand-edit legacy JSON or SQLite.

> **Workspace slot occupancy is owned by the [workspace-manager](../workspace-manager/SKILL.md) skill**
> (`wm.py`, store `~/tasks/workspaces.db`). Tasks no longer carry a `workspace` field —
> `tm.py` imports `wm.py` and delegates every slot hold to it. A slot can be held by a task
> **or** by an ad-hoc purpose (monitor PRs, debug an issue, …) with no task at all.

> Bug/phenomenon data belongs to bug-monitor/bug-triage in `~/tasks/bugs.db`.
> The portal integrates those APIs independently of the task CLI.

## Team Portal ownership

The [portal-maintainer](../portal-maintainer/SKILL.md) owns the website, its
server/assets, API coordination, operations and maintenance journal.
Portal questions and feature requests route there. Task/review business
rules remain here. `tm.py serve` and `tm.py dashboard` are compatibility
commands delegating to `~/.copilot/team-portal/portal.py`.

## CLI cheatsheet

Run as: `python3 ~/.copilot/skills/task-manager/tm.py <subcommand> ...`

| Subcommand | Purpose | Notes |
|------------|---------|-------|
| `add <title> [-c CAT] [-p PRI] [--complexity X] [--cost N] [--jira K] [--context S] [--workspace W] [--no-workspace] [--backlog] [--cwd PATH]` | Add a non-review task | Auto-detects workspace from cwd unless `--no-workspace`. Reports if workspace is occupied. |
| `list [--status S] [--category C] [--priority P] [--json]` | List tasks | Default hides `done`/`cancelled`. Sorts by priority then category. Shows a "Feature" column (first sentence of `description`, falls back to title with leading `Review:` stripped). Prints summary + workspace map. |
| `dashboard [-o PATH]` | Compatibility static export | Delegates to portal-maintainer; use its canonical `portal.py dashboard`. |
| `serve [--port N] [--stop] [--status]` | Compatibility server command | Delegates to portal-maintainer; use its canonical `portal.py serve`. |
| `workspaces [--project NAME]` | Show workspace slot occupancy (tasks **and** ad-hoc holds) | Default project: `iridium`. Delegates to `wm.py`. |
| `update <id> <status> [-n NOTE]` | Set arbitrary status | `id` matches `T-NNN` or unique title substring. |
| `edit <id> [--title TEXT] [-d DESCRIPTION]` | Curate task metadata | Owner API preserves all other fields; never edit JSON or database directly. |
| `done <id> [-n NOTE]` / `block <id>` / `cancel <id>` | Status shortcuts | `done`/`cancel` auto-clear workspace and set `completed`. |
| `assign <id> <workspace> [--force]` | Assign task to a workspace slot | Delegates to `wm.py`. Requires a free, freshly verified clean slot; `--force` cannot displace a hold. Promotes `todo`→`in-progress`; stamps the acquire date. |
| `release <id> [-n NOTE]` | Free a task's workspace slot **without** changing its status | Releases the slot in `wm.py` but leaves `status` untouched. Use when you stop working in a slot but the task isn't done (e.g. review `stop`, context switch). |
| `remove <id> --yes` | Permanently delete | Prefer `cancel` unless this is a duplicate/mistake. |

> **Code-review subcommands** (`scan-reviews`, `add-review`, `list-reviews`, `review-update`, `review-check`, `review-msg`) also live in `tm.py`, but the review queue lifecycle is owned by the **review-mgr** skill. See `~/.copilot/skills/review-mgr/SKILL.md`.

Valid values:
- categories: `development`, `code-review`, `planning`, `testing`, `track`, `misc`
- priorities: `critical`, `high`, `medium`, `low`
- statuses: `backlog`, `todo`, `in-progress`, `blocked`, `done`, `cancelled`
- complexities: `trivial`, `small`, `medium`, `large`, `epic`
- review statuses (per-PR): `pending`, `in-progress`, `commented`, `changes-requested`, `waiting-for-re-review`, `approved`, `merged`
- consolidated review status (task-level, derived): `pending`, `in-progress`, `waiting-for-re-review`, `waiting-for-author`, `approved`, `merged` (see "Consolidated review status" below)

## Owner API shape

The following JSON is the CLI/API representation, not an editable file.

```json
{
  "next_id": 4,
  "tasks": [
    {
      "id": "T-001",
      "title": "Short imperative title",
      "description": "Detailed description",
      "category": "development",
      "priority": "medium",
      "status": "todo",
      "complexity": "medium",
      "cost_hours": 4,
      "context": "Where this came up",
      "jira": "IPINFRASW-1234",
      "created": "2026-05-26",
      "updated": "2026-05-26",
      "completed": null,
      "notes": ["2026-05-26: initial submission"]
    }
  ]
}
```

> Workspace occupancy is **not** stored on the task. Which slot (if any) a task
> holds lives in the [workspace-manager](../workspace-manager/SKILL.md) store
> (`~/tasks/workspaces.db`) and is resolved via `wm.slot_of_task(id)`.

### Review-task extension

Tasks with `category: "code-review"` also carry a `reviews` array (preferred) or legacy single `review` object:

```json
"reviews": [
  {
    "pr_url": "https://github.com/Elmnt-Internal/ip.infra.sw.fw/pull/670",
    "owner": "Elmnt-Internal",
    "repo": "ip.infra.sw.fw",
    "pr_number": 670,
    "author": "username",
    "branch": "dev/username/feature",
    "files_changed": 12,
    "additions": 340,
    "deletions": 45,
    "review_status": "pending",
    "submitted_at": null,
    "merged": false,
    "closed": false
  }
]
```

Only `category: "code-review"` tasks may have `review` / `reviews`; other categories must not.

**Closed PRs are dropped, not stubbed.** When a scan (portal **Scan reviews** or CLI
`review-check`) finds a PR closed **without** merge, that PR entry is removed from the task's
`reviews` list entirely — there is nothing left to review. A task left with **no** remaining
PRs is transitioned to `cancelled` (its workspace freed), since a merged PR keeps the record
but a closed one leaves no work behind.

#### Consolidated review status (one status per task)

Each PR carries its own **per-PR** `review_status` (granular, PR-scoped):
`pending`, `in-progress`, `commented`, `changes-requested`, `waiting-for-re-review`,
`approved`, `merged`. A task can hold several PRs in different states, but the task
must expose exactly **one** overall status. The CLI derives it with
`consolidate_review_status()` (mirrored in the dashboard as `consolidateReviewStatus()`)
using this precedence:

| Overall status | Condition | Ball is with |
|----------------|-----------|--------------|
| `merged` | **every** PR merged | — (task done; kept as a record, not purged) |
| `approved` | every PR approved-or-merged, but **not all** merged | maintainer (awaiting merge) |
| `waiting-for-author` | **any** PR still carries our feedback (`commented`/`changes-requested`) and is not approved | author |
| `waiting-for-re-review` | author bounced it back (`waiting-for-re-review`), no open feedback | reviewer |
| `in-progress` | a review is actively underway | reviewer |
| `pending` | nothing started | reviewer |

`waiting-for-author` is a **consolidated-only** status — it never lives on an individual
PR (those stay `commented` / `changes-requested`). The overall status maps to the generic
task `status` field: `merged`→`done`, `approved`/`in-progress`→`in-progress`,
`waiting-for-author`→`blocked`, `waiting-for-re-review`/`pending`→`todo`. `list-reviews`,
the dashboard Status column/filter/sort, `review-update`, and `blocked-scan --update` all
use this single derived value.

**A code-review task is only truly `done` once its consolidated status is `merged`**
(every PR merged). At `approved` the reviewer is finished but the task stays
`in-progress` until the merges land — the task-manager keeps tracking it until then.
Fully-merged tasks are **not purged**: they persist as a `done`/`merged` record and
remain visible in the dashboard's code-review section (with a **Merged** stat card).
Running `done`/`update <id> done` on a not-yet-merged review task does **not** force it
done — it approves the open PRs and leaves the task `in-progress` until they merge.

### `track` tasks

Use `category: "track"` for work you are **monitoring rather than executing** — a topic
or activity owned by a team member, a bug being worked by someone else, an external
dependency, or any thread you want to keep an eye on without owning the hands-on work.
They behave like ordinary tasks (status, priority, notes, `jira`) and appear in the
unified Tasks view (filter Category to `track`). Put the person/thing being tracked and the reason in `context`, and use
append-only `notes` to log progress updates you observe. A `track` task is typically not
assigned to a workspace slot (you are not the one coding it), so prefer `--no-workspace`
when adding one.

## When to use the LLM instead of the CLI

Use judgment for these fields, but persist changes through the CLI:

1. **Polishing a task `description`** — when the user wants a curated summary, not just the PR title. Use `-d "..."` to pass it to `add` / `add-review`, or use `tm.py edit <id> -d "..."` afterwards.
2. **Resolving ambiguous category/priority** at `add` time — if truly unclear from context, ask the user before calling the CLI.

> **Code-review workflows** (discover/scan, `review-start` / `review-stop` / `review-done`, the blocked-review author-activity scan, and the memoir update) are owned by the **review-mgr** skill. See `~/.copilot/skills/review-mgr/SKILL.md`.

## Rules (still mandatory, enforced by CLI where possible)

1. **Always read the store before writing.** The CLI does this automatically.
2. **Never lose data.** Prefer status changes over deletion.
3. **Auto-estimate** complexity and cost on every new task. The CLI defaults are reasonable; override with `--complexity` / `--cost` when you have a better estimate, and explain your reasoning in one line.
4. **Keep IDs stable.** Never renumber. Gaps are fine.
5. **Timestamps:** `YYYY-MM-DD`, always from system date. The CLI handles this.
6. **Notes append-only.** Never edit or remove existing notes entries.
7. A new database starts with no tasks; an existing legacy store is imported once with a backup.
8. `jira` is optional — populate when the user mentions one or when `add-review` extracts it from a PR title.
9. **Workspace uniqueness:** At most one holder (task or ad-hoc) per workspace slot. The [workspace-manager](../workspace-manager/SKILL.md) enforces this on `assign`/occupy (free the old hold first; `--force` does not override) and `add` defaults to `backlog` if the target slot is occupied.
10. **Workspace auto-release on done/cancelled:** `tm.py` calls `wm.release_task` so the slot frees when a task is finished.
11. **Workspace occupancy lives in the workspace-manager store**, not on the task. `tm.py` never hand-edits occupancy — it delegates to `wm.py` (`occupy_task`/`release_task`) through one path, so the **Workspaces** dashboard section and other skills (review-mgr, pr-monitor) all read one truth. A slot can be held by a task **or** an ad-hoc purpose (see the workspace-manager skill).
12. **Workspace auto-assignment on add:** When called from inside `~/projects/<project>/<ws>/...`, the CLI auto-assigns that slot if free (via `wm.py`); otherwise it parks the task in `backlog`. Pass `--cwd` to override the detected path or `--no-workspace` to disable.
13. **Code-review tasks** carry review-only fields (`reviews[]`) and their own lifecycle, branch-capture, and multi-PR invariants (all enforced by the `tm.py` review subcommands). A review task is kept until **every** PR merges (only then is it `done`/`merged`); merged tasks persist as a record rather than being purged. These are documented and driven by the **review-mgr** skill — see `~/.copilot/skills/review-mgr/SKILL.md`.
14. **One PR per repo per task.** A multi-PR review task must never contain two PRs targeting the same repo. `review-start` can only check out one branch per repo in the workspace; a second same-repo PR is unreachable and causes reviewer confusion. When `add-review` receives multiple URLs, verify no two target the same repo — if they do, split into separate tasks.
