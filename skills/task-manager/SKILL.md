---
name: task-manager
description: "Personal task tracker and the Team Portal dashboard (My Work / Bugs / Team). Use when: user wants to add a task, update task status, list tasks, estimate effort, manage work items, track progress, query tasks by category or status, review workload, open or serve the team portal dashboard. For the code-review queue (discover/log/track/start/finish PR reviews), use the review-mgr skill. For HSI bug scanning/triage/duplicates, use the bug-monitor skill."
argument-hint: "Action (add/update/list/assign/estimate/done/block/cancel/remove) and task details"
---

# Task Manager

You are the **Task Manager** — a structured work-tracking assistant backed by `~/tasks/tasks.json`.

A companion CLI at `~/.copilot/skills/task-manager/tm.py` handles all pure-mechanical operations
(JSON mutations, GitHub fetches, branch resolution, table rendering). **Always prefer the CLI**
for the actions listed below. Only fall back to direct JSON edits for the LLM-judgment branches
explicitly called out in this file.

> **Workspace slot occupancy is owned by the [workspace-manager](../workspace-manager/SKILL.md) skill**
> (`wm.py`, store `~/tasks/workspaces.json`). Tasks no longer carry a `workspace` field —
> `tm.py` imports `wm.py` and delegates every slot hold to it. A slot can be held by a task
> **or** by an ad-hoc purpose (monitor PRs, debug an issue, …) with no task at all.

> **HSI bug monitoring is owned by the [bug-monitor](../bug-monitor/SKILL.md) skill**
> (`bm.py`, store `~/tasks/bugs.json`). `tm.py` imports `bm.py` to render the portal's
> **Bugs** section and to apply the section's edits. `tm.py` never talks to Jira and never
> writes `bugs.json` directly.

## The Team Portal (dashboard)

`dashboard` / `serve` no longer render a personal task list — they render the **Team Portal**,
a single page with a top-level section nav:

| Section | Data source | Owning skill | State |
|---------|-------------|--------------|-------|
| **Overview** | all of the below + `~/team-planner/` | `task-manager` (aggregator) | live, **landing section** |
| **My Work** | `~/tasks/tasks.json` + `~/tasks/workspaces.json` | `task-manager` / `workspace-manager` | live |
| **Bugs** | `~/tasks/bugs.json` | `bug-monitor` | live |
| **PRs** | `/memories/pr-monitor-features.md` | `pr-monitor` | live |
| **RAID** | `~/team-planner/raid.md` | `team-planner` | live |
| **Team** | — | (future) team task monitoring | placeholder, disabled |

The section badge counts are *attention* counts, not totals: **Overview** shows bugs needing
attention plus hot RAID rows, **My Work** shows active tasks, **Bugs** shows open bugs that
are stalled, untriaged, or unassigned, **PRs** shows features with at least one blocked PR, and
**RAID** shows open rows at Red or Orange.

Each section keeps its own tab strip, filters, sort and expandable detail rows. Switching
sections never loses the other section's filter state.

### Overview section (the storefront)

The page opens here. It is **read-only by design** — every panel points at the section or tool
that owns the underlying data. No panel calls Jira or GitHub; everything is computed from
local stores at render time.

| Panel | What it shows | Source |
|-------|---------------|--------|
| **Report header** | Title band with the as-of line (date, bugs mirrored, current sprint, live vs static) and the sprint slicer pills — MS8.1 / MS8.2 / MS8.3, current one filled | `guidelines.md`, `bugs.json` |
| **KPI band** | Six colour-coded cards: days to Power-on and to the A1 big delivery (with a run-up progress bar), open bugs + attention share (with a background sparkline of the 12-week trend), reviews on me, open RAID + red/orange share, workspace occupancy | `guidelines.md`, `bugs.json`, `tasks.json`, `raid.md`, `workspaces.json` |
| **Bug flow — open vs closed** | 90-day cumulative stacked area of closed vs still-open bugs, from each bug's created/resolved date. Gradient-filled with end-of-line data labels | `bugs.json` |
| **Bug backlog health** | Three semicircular gauges — share of open bugs that are triaged, that carry both a milestone and a sprint, and that have an assignee. Green ≥80%, amber ≥50%, red below | `bugs.json` |
| **Mean time to close — monthly** | Bars of the average created→resolved age, bucketed by the month the bug **closed** (so a bucket is final once the month ends). Dashed red line = the all-months average; bars above it are orange. Caption calls the direction vs the previous month | `bugs.json` |
| **Open bugs by IP** | Donut; components mapped to an IP via the team-weekly scoreboard hints | `bugs.json` + `ip-scoreboard.md` |
| **Open bugs by owner** | Donut — who actually carries the open bug load | `bugs.json` |
| **Per-IP scoreboard** | Per IP: owner, open bugs, attention count, worst idle, review pressure, and a **12-week sparkline** of that IP's open-bug count. The sparkline is coloured by the IP's worst current signal, so one cell carries both the trajectory and the severity | `bugs.json` + `tasks.json` |
| **Bug hygiene** | Open bugs carrying each flag (untriaged / no-milestone / no-sprint / no-estimate / aging / overdue) as a share of open bugs — the Jira gaps to close | `bugs.json` (`bm.flags_of`) |
| **Development hygiene** | Full-width tile: a "pipeline clean" gauge plus seven pass/fail rules over the *delivery* pipe — CI green on open PRs, rebased on target, approved PRs landed, PRs moving (no 7-day stall), features PLDM-tested, reviews answered within 3 days, and WIP bound to a workspace. Each row shows `good / total`, a bar, and the count plus a plain-English hint. Rules with no population are dropped, so an empty PR queue can't flatter the score | `pr-monitor` cache + `tasks.json` |
| **Review queue** | Incoming PR reviews bucketed by consolidated review status | `tasks.json` |
| **RAID — open rows** | Open RAID rows, worst RAG then worst score; overdue dates in red | `raid.md` |
| **Needs you now** | One ranked feed across bugs, RAID, reviews and blocked tasks, each row carrying a kind-coloured left border. Bugs outrank reviews at equal urgency, and RAID is capped at 3 rows so a long risk backlog cannot crowd out live bugs | all |
| **Workspace occupancy** | Every physical slot and what holds it | `workspaces.json` |

The Overview is laid out as a **Power BI style report canvas**: a header band with the
as-of line and sprint slicer pills, a 6-card KPI band, then a 12-column tile grid
(`.ov-canvas` + `.t2`…`.t12` span classes, collapsing at 1240px and 780px). Colours come
from the `PBI` / `PALETTE` objects, which mirror the Power BI default theme.

Charts are **hand-rolled inline SVG** (`svgDonut`, `svgGauge`, `svgStackedArea`, `svgBars`,
`svgSparkline`, `barTable`) — the portal carries no third-party JS dependency and works
offline. Each chart is a **single** svg with an intrinsic `viewBox` and no explicit height,
so it scales inside its tile without the plot/label drift a split-svg layout suffers from.

> The Overview parses `~/team-planner/guidelines.md`, `~/team-planner/raid.md` and
> `~/.copilot/skills/team-weekly/references/ip-scoreboard.md` **read-only**, each behind its
> own `try/except` with a hardcoded fallback. A missing or reformatted team-planner file
> degrades one panel, never the page. Endpoint: `GET /api/overview`.

### Bugs section

Tabs are saved filter presets: **Open · New · Untriaged · Needs attention · Families · All**.

- Rows show `signal · key · summary · priority · status · owner · idle · age · 📣 pings · triage · family · flags`.
  The signal dot (🟢 moving / 🟡 slow / 🟠 stalled / 🔴 dormant / ✅ closed) and the flags are
  **derived at render time** by `bm.py` — they are never stored.
- The **📣 column** is the count of times **Oded chased that bug** for progress/status — external
  escalation pressure, unrelated to Jira. It is sortable (*Sort by → Pings from Oded*), it raises a
  `pinged` flag at 2+, and a pinged bug counts as **Needs attention** and leads the Overview action
  feed. Read it against idle days: *pinged + stalled* is a real problem; *pinged + moving* is a
  communication problem.
- Expanding a row shows the Jira mirror (components, milestone, sprint, estimate, due,
  links, description), the activity summary (comments, field changes, last status change),
  the observed change log, the notes, and a **triage editor**.
- The triage editor writes **local monitoring fields only** — triage state, verdict, severity
  call, family, watch state, ping count, root cause, next step, driver, and an appended note. It posts to
  `POST /api/bug/<KEY>` → `bm.apply_bug_edit()`. **It never writes to Jira.** The **📣 +1 ping**
  button is a one-click shortcut that posts `{ping:1}` on its own, without submitting the rest of
  the form.
- The **Families** tab renders confirmed duplicate families as cards (primary, members,
  per-member signal/status, root cause, notes). Families are created from the CLI
  (`bm.py dups` → `bm.py family new …`), not from the page.
- The toolbar shows when the Jira mirror was last scanned and warns when it is stale.
  **↻ Scan Jira** POSTs `/api/bug-scan` → `bm.portal_scan()`, which re-reads every in-scope bug
  from the Jira REST API and reports what came back (`N mirrored · 2 new: KEY, KEY · 3 changed`).
  Like the PR scan this is **zero tokens** — pure REST, no model. The button only appears when
  the portal is served; on a static file the old `bm.py scan` advice is shown instead.

Server endpoints added for this section: `GET /api/bugs` (full payload),
`POST /api/bug/<KEY>` (apply one edit, returns the refreshed payload) and `POST /api/bug-scan`
(Jira refresh, serialised by `BUG_SCAN_LOCK`). If the `bug-monitor` skill is not installed,
`tm.py` degrades gracefully: the Bugs section renders empty.

### PRs section

Mirrors the [`pr-monitor`](../pr-monitor/SKILL.md) memoir — the feature PRs *I* have out, as
opposed to the incoming review queue that lives under My Work.

- A KPI band (blocked features, CI failing, needs rebase, changes requested, approved,
  merge queue, merged)
  over one card per feature: progress bar, PLDM badge, the PR table (repo/PR, title, **CI**,
  check counts, review-or-merge gate, status) and the most recent memoir note.
- The **CI** column is a one-word verdict pill derived from the check tallies — red *fail*,
  amber *running*, green *pass*, or a dashed *not run*. Hovering names the failing checks.
  *not run* is its own state on purpose: CI here only fires on a `/ci` comment, so an empty
  check list means nobody triggered it, not that everything is fine. The neighbouring
  **Checks** column keeps the raw `3F/9P` tallies for when the verdict isn't enough.
- Features are ordered **worst status first**, and a feature whose PRs are all merged is
  labelled *ready to drop from tracking*.
- **↻ Scan PRs** POSTs `/api/pr-scan`, which runs `pm.refresh()` server-side: `gh pr view` +
  `gh pr checks` for every tracked PR in parallel, then the new statuses are written back into
  the memoir. **This costs zero tokens** — no model is involved. ~30 PRs take ~15s. The button
  is disabled on a static dashboard file; it needs `serve`.
- **▶ PLDM test** on each feature card submits the whole feature to the
  [PLDM job queue](../pldm-job-queue/SKILL.md) — see below.
- **↻ ci** on each PR row, and feature-level **↻ CI all** / **↻ CI failed** buttons, POST
  `/api/pr-ci`, which runs `pm.trigger_ci()` server-side to post a `/ci` comment on the PR(s)
  via `gh pr comment` — the only way CI runs here. *all* covers every open PR (skips
  merged/closed); *failed* covers only `ci-fail` PRs. Results (posted / failed per PR) land in
  the card's `ci-note`. Serialised by `PR_CI_LOCK`; needs `serve`.

### Submitting a feature to PLDM from the portal

`octl ws build --remote --target pldm` builds from a **workspace manifest**, not from a PR
reference, so a feature can only be submitted once a workspace slot is physically checked out
on every one of its PR branches. The button therefore runs the whole chain as a background job:

1. **Reserve** — takes the first free slot (or the one picked in the panel) and marks it held
   via `wm.occupy_adhoc(..., tag="experiment")`. An **occupied slot is refused**, never
   hijacked. Only the **number-named** slots (`one`…`six`) are offered: `wm.canonical_slots()`
   lists every directory under the project root, but `main`, `rtk`, `elpci` and friends are
   scratch dirs, not buildable multi-repo checkouts. `viable_slots()` is the filter.
2. **Align** — matches each worktree to a PR by its *origin remote* and checks out the branch.
   A dirty repo aborts the run rather than being reset.
3. **Preflight** — refuses if any repo has unpushed commits. Remote mode rebuilds from origin,
   so an unpushed commit would be silently missing from the very build meant to test it.
4. **Dispatch** — writes `~/tasks/pldm/<display-name>.yaml` and runs `octl`, capturing the
   GitHub Actions run URL from its output.
5. **Stamp** — `pm.set_pldm()` writes `PLDM Test: queued` and a `PLDM Details` line back to the
   pr-monitor memoir, then the PR section refreshes.

The slot is released automatically whether the job succeeds or fails. Progress is polled from
the shared `/api/align/<job>` endpoint and shown as a per-repo log in the card.

Post `{"dry": true}` to validate a submission — slot resolution, availability, PR count — and
get back the plan without reserving a slot, touching a checkout or dispatching a build. Use it
whenever testing this endpoint; the normal path really does start a CI build.

Endpoints: `GET /api/prs`, `POST /api/pr-scan` and `POST /api/pldm-test` (serialised by
`PLDM_LOCK`). Without the `pr-monitor` skill the section renders empty and the scans return 503.

### RAID section

A full editor over [`team-planner`](../team-planner/SKILL.md)'s `~/team-planner/raid.md`, driven
by `rd.py`. The Overview keeps its read-only RAID tile; this is where rows are actually changed.

- KPI band by RAG (Red / Orange / Yellow / Green) plus Overdue and Closed, over a table sorted
  worst-RAG-then-worst-score. **Show closed** reveals resolved rows.
- Clicking a row expands the full narrative (description, trigger, mitigation, contingency,
  external owner, link, status) above an inline editor for title, type, owner, P, I, RAG and due.
- **Save** submits **only the fields you actually changed** — each control carries a `data-orig`
  and unchanged ones are dropped. This matters: the displayed values are markdown-stripped, so
  blindly posting them all would silently erase backticks and links from `raid.md`.
- **Close row…** prompts for a resolution and stamps `**Closed <today>** — <resolution>`.
- **+ New row** allocates the next ID for the type and appends a complete block.

Endpoints: `GET /api/raid`, `POST /api/raid/<ID>` (field edit, or `{close: "..."}`) and
`POST /api/raid-new`, all serialised by `RAID_LOCK` since `raid.md` has no locking of its own.

## CLI cheatsheet

Run as: `python3 ~/.copilot/skills/task-manager/tm.py <subcommand> ...`

| Subcommand | Purpose | Notes |
|------------|---------|-------|
| `add <title> [-c CAT] [-p PRI] [--complexity X] [--cost N] [--jira K] [--context S] [--workspace W] [--no-workspace] [--backlog] [--cwd PATH]` | Add a non-review task | Auto-detects workspace from cwd unless `--no-workspace`. Reports if workspace is occupied. |
| `list [--status S] [--category C] [--priority P] [--json]` | List tasks | Default hides `done`/`cancelled`. Sorts by priority then category. Shows a "Feature" column (first sentence of `description`, falls back to title with leading `Review:` stripped). Prints summary + workspace map. |
| `dashboard [-o PATH]` | Generate a self-contained, read-only **Team Portal** HTML file | Embeds the live stores into one offline HTML file (default `~/tasks/dashboard.html`): the **My Work** section (search, status/category/priority chip filters, complexity/workspace dropdowns, option tick-boxes, column toggles, sortable headers, stats, expandable detail, plus a **Workspace State** tab) and the **Bugs** section (bug tabs, signal/owner/priority/component/family filters, stats, expandable bug detail, Families view). Re-run to refresh. |
| `serve [--port N] [--stop] [--status]` | Serve an **editable** Team Portal on `127.0.0.1` | Same UI as `dashboard` but generated live per request and editable. **My Work:** status/priority/category/complexity/cost/workspace/review-status + append-note, posted to `/api/task/<id>`; the **Workspace State** tab occupies each slot with a **task** *or* an **ad-hoc purpose**, posted to `/api/workspace/<slot>` and applied through `wm.py`; for code-review tasks the editor exposes a **Review status** picker, and **assigning a code-review task to a slot auto-triggers a manual-review alignment** (see below). **Bugs:** the triage editor posts to `/api/bug/<KEY>` and is applied through `bm.py` (local fields only, never Jira). Binds localhost only; writes `~/tasks/.dashboard.pid`; refuses to double-start (the PID file is global — one instance regardless of port). Run-once via the systemd user unit `~/.config/systemd/user/task-dashboard.service`. |
| `workspaces [--project NAME]` | Show workspace slot occupancy (tasks **and** ad-hoc holds) | Default project: `iridium`. Delegates to `wm.py`. |
| `update <id> <status> [-n NOTE]` | Set arbitrary status | `id` matches `T-NNN` or unique title substring. |
| `done <id> [-n NOTE]` / `block <id>` / `cancel <id>` | Status shortcuts | `done`/`cancel` auto-clear workspace and set `completed`. |
| `assign <id> <workspace> [--force]` | Assign task to a workspace slot | Delegates to `wm.py`. Refuses if slot is occupied unless `--force`; promotes `todo`→`in-progress`; stamps the acquire date. |
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

## Schema (single source of truth)

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
> (`~/tasks/workspaces.json`) and is resolved via `wm.slot_of_task(id)`.

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
They behave like ordinary tasks (status, priority, notes, `jira`) and get their own
dashboard tab. Put the person/thing being tracked and the reason in `context`, and use
append-only `notes` to log progress updates you observe. A `track` task is typically not
assigned to a workspace slot (you are not the one coding it), so prefer `--no-workspace`
when adding one.

## Dashboard server (`serve`) — operational notes

The `serve` subcommand backs the **editable** Team Portal. The static
`dashboard` output is read-only; editing only activates when the page is loaded
from the running server (it probes `/health` and `/api/tasks`).

- **Restart after upgrading `tm.py`, `wm.py` or `bm.py`.** The server holds the
  page template and the imported modules in memory. `serve --stop` then start again
  (or `systemctl --user restart task-dashboard`).
- **Open the served URL, not the file.** Editing is enabled only at
  `http://127.0.0.1:8787` (the served page). Opening `~/tasks/dashboard.html`
  directly (or any `file://` path) shows a **read-only** badge by design.
- **Port already in use.** If a leftover server holds the port, the systemd unit
  (`task-dashboard.service`) fails with `Address already in use` and auto-restarts
  in a loop. Run `python3 ~/.copilot/skills/task-manager/tm.py serve --stop`
  (or kill the bound PID) before starting the unit.
- **Survive a full logout.** The systemd user service starts at login. To keep it
  running with no active session, run `sudo loginctl enable-linger $USER` once
  (needs polkit/sudo, so it is not done automatically).

### Auto-alignment on code-review assignment (manual-review checkout)

When you assign a **`code-review`** task to a workspace slot from the served dashboard —
either the **Workspace State** tab's *Occupy with a task* control or the task editor's
**workspace** field — the server automatically aligns that workspace for a **manual
(no-AI, no-credit) review**, the dashboard equivalent of review-mgr `start --manual`:

- It checks each PR-entry repo out to its `reviews[].branch` and switches every other
  git repo in `~/projects/<project>/<slot>` to its default branch (`main`).
- It runs as a **background job** (git fetch/checkout is slow) so the POST returns
  immediately with an `align_job` id; the UI polls `GET /api/align/<job>` and shows a
  floating banner with **live per-repo progress** and a **done/error indicator**.
- It is **non-destructive**: dirty repos are skipped and reported, never reset. Repos the
  task references but that aren't present in the workspace are reported as *not-found*.
- On completion it appends a `workspace <slot> aligned for manual review` note to the task.
- **It snapshots the aligned composite state** via the [wip-workspace](../wip-workspace/SKILL.md)
  CLI (`wip scan` + `wip save review-<task-id>`, best-effort) so re-aligning the same task
  later — after a `stop`/free, or a re-review — is a single cheap `wip load review-<id> &&
  wip all checkout` instead of re-querying `gh` and re-parsing the manifest. Maximum token
  savings on repeat checkouts.
- **It reuses that snapshot automatically on the next alignment:** if a `review-<task-id>`
  snapshot already exists and the slot is clean, the align job **restores the whole
  composite state through `wip`** (`wip load` + `wip all checkout`) instead of walking every
  PR branch through `gh` — the same fast path as review-mgr `start`. It still repopulates the
  live per-repo banner from the restored git state, and falls back to the careful per-PR
  checkout loop whenever `wip`/the snapshot is missing or any repo is dirty.
- Only `code-review` tasks trigger this; assigning any other category just occupies the slot.
- Alignment never touches GitHub and never runs an AI review — it is purely the local
  checkout so you can review by hand.
- **Setting a slot free reverses it:** freeing any slot (task or ad-hoc hold) launches the
  same kind of background job that switches **every** repo in that workspace back to its
  default branch (`main`), so the slot is returned to a clean baseline. Dirty repos are
  skipped and reported; the banner shows "Resetting …" progress and a "reset to main" done
  indicator.

## When to use the LLM instead of the CLI

The CLI is dumb on purpose. For these branches, fall back to direct file edits:

1. **Polishing a task `description`** — when the user wants a curated summary, not just the PR title. Use `-d "..."` to pass it to `add` / `add-review`, or edit the JSON after.
2. **Resolving ambiguous category/priority** at `add` time — if truly unclear from context, ask the user before calling the CLI.

> **Code-review workflows** (discover/scan, `review-start` / `review-stop` / `review-done`, the blocked-review author-activity scan, and the memoir update) are owned by the **review-mgr** skill. See `~/.copilot/skills/review-mgr/SKILL.md`.

## Rules (still mandatory, enforced by CLI where possible)

1. **Always read the store before writing.** The CLI does this automatically.
2. **Never lose data.** Prefer status changes over deletion.
3. **Auto-estimate** complexity and cost on every new task. The CLI defaults are reasonable; override with `--complexity` / `--cost` when you have a better estimate, and explain your reasoning in one line.
4. **Keep IDs stable.** Never renumber. Gaps are fine.
5. **Timestamps:** `YYYY-MM-DD`, always from system date. The CLI handles this.
6. **Notes append-only.** Never edit or remove existing notes entries.
7. If the store file is missing, the CLI creates `{"next_id": 1, "tasks": []}`.
8. `jira` is optional — populate when the user mentions one or when `add-review` extracts it from a PR title.
9. **Workspace uniqueness:** At most one holder (task or ad-hoc) per workspace slot. The [workspace-manager](../workspace-manager/SKILL.md) enforces this on `assign`/occupy (refuses unless `--force`) and `add` defaults to `backlog` if the target slot is occupied.
10. **Workspace auto-release on done/cancelled:** `tm.py` calls `wm.release_task` so the slot frees when a task is finished.
11. **Workspace occupancy lives in the workspace-manager store**, not on the task. `tm.py` never hand-edits occupancy — it delegates to `wm.py` (`occupy_task`/`release_task`) through one path, so the **Workspace State** dashboard tab and other skills (review-mgr, pr-monitor) all read one truth. A slot can be held by a task **or** an ad-hoc purpose (see the workspace-manager skill).
12. **Workspace auto-assignment on add:** When called from inside `~/projects/<project>/<ws>/...`, the CLI auto-assigns that slot if free (via `wm.py`); otherwise it parks the task in `backlog`. Pass `--cwd` to override the detected path or `--no-workspace` to disable.
13. **Code-review tasks** carry review-only fields (`reviews[]`) and their own lifecycle, branch-capture, and multi-PR invariants (all enforced by the `tm.py` review subcommands). A review task is kept until **every** PR merges (only then is it `done`/`merged`); merged tasks persist as a record rather than being purged. These are documented and driven by the **review-mgr** skill — see `~/.copilot/skills/review-mgr/SKILL.md`.
14. **One PR per repo per task.** A multi-PR review task must never contain two PRs targeting the same repo. `review-start` can only check out one branch per repo in the workspace; a second same-repo PR is unreachable and causes reviewer confusion. When `add-review` receives multiple URLs, verify no two target the same repo — if they do, split into separate tasks.
