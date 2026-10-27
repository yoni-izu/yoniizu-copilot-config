# Team Portal behavior contracts

Portal-specific documentation migrated from task-manager. Paths to runtime files
are relative to `~/.copilot/team-portal/`. Domain APIs, not legacy files,
are authoritative; see [architecture](./architecture.md).

## The Team Portal (dashboard)

`dashboard` / `serve` no longer render a personal task list — they render the **Team Portal**,
a single page with a top-level section nav:

| Section | Data source | Owning skill | State |
|---------|-------------|--------------|-------|
| **Overview** | all of the below + `~/team-planner/` | `portal-maintainer` (aggregator) | live, **landing section** |
| **Tasks** | `~/tasks/tasks.db` | `task-manager` | live |
| **Workspaces** | `~/tasks/workspaces.db` | `workspace-manager` | live |
| **Bugs** | `~/tasks/bugs.db` (bugs, families, phenomena) + `~/.copilot-private/skills/bug-triage/investigations/*.md` | `bug-monitor` + `bug-triage` | live |
| **PRs** | `~/tasks/prs.db` | `pr-monitor` | live |
| **PLDM** | the PLDM job queue | `pldm-job-queue` | live |
| **VM jobs** | `~/tasks/delegation.db` (mctl log ingestion) | `vm-delegate` | live |
| **RAID** | `~/tasks/raid.db` | `team-planner` | live |
| **Team** | `~/tasks/team.db` via `team-manager` | `team-manager` | live: Overview / Plan / Members |

### Team section

Team includes the lead and all engineers. Member pages show focus assignments,
saved activity, 1:1 sessions and incoming reviews. Sessions and assignments use
the owner payload's top-level collections, filtered by member key.
`#member/<key>` and `#team/plan` open the corresponding views.

Presentation mode is the default on every page load. Private mode is explicit
and is not remembered across reloads. Leaving Team or switching to presentation
immediately discards the private payload from the UI and restores the last
server-filtered shared payload. Late private responses cannot overwrite it.
Member goals and meeting entries follow their owner-defined visibility.

The mode control sits at the top-right of Team, with green Presentation and red
Private fill, without a sensitive-data warning banner. Focus is an editable
status-free field on the member header card, not a tab. The Tasks tab contains
managerial assignments and linked session action items.
Edit focus and Edit profile share a horizontal action row. Profile editing uses
the same Save/Cancel popup form as focus, with main IP, country, goals and goals
visibility prefilled; no browser prompt sequence. Saves remain revision-checked.

Member tasks display their stable IDs, category, priority, status and due date.
Create/edit forms support descriptions, Jira, PR URLs and typed cross-domain
references. Add note appends a visibility-controlled progress entry. Session
action rows expose the same metadata under Task details & links; saving them is
atomic with the log record.

The member Log tab replaces the 1:1 label and combines 1:1, Conversation, Meeting
and Note entries, with a multi-select type filter (none means all). Existing
routes and session IDs are preserved. New entries default to type 1:1; blank
titles become `1:1 with <name> - <YYYY-MM-DD>` using the resolved date. Custom
and historical titles are preserved. Each entry belongs to one member.

Log creation/editing uses a near-full-screen modal in the workspace-dialog
style. Topic is optional; blank date means today. Typed item rows occupy the
largest area; freeform notes are a compact 3-row box below them. A toolbar adds
rows of any type served in `log_item_types` (Action item, Decision, Discussion
point, Status update, Roadblock, Feedback given, Feedback received,
Growth/career, Personal); each row has a colored type badge, its own fields,
a visibility select (defaulted per type; Personal is always private) and a
remove button. Party selects show the member's name; on the lead's own page
the counterpart is the manager (`manager_name`, e.g. "Manager") and
blank 1:1 titles use the manager's name. Notes, topic and individual items
have visibility controls. State and session-level visibility are not form fields. Presentation
shows a session only if it has presentation-visible notes/entries; topic
visibility independently controls whether its title is disclosed. Private notes
remain filtered. Legacy workflow metadata is retained internally; ordinary edits
need no state transition selection.
Saving creates/updates each item's linked record atomically with the session:
action → member task (once; later edits happen in Tasks and the row is
read-only with a task link), status update → task progress note and optional
status change, roadblock → roadblock record, feedback → feedback record.
Removing a roadblock/feedback/status row deletes its record; tasks are never
deleted. Saved roadblock rows offer "Promote to RAID", which creates a RAID
issue through `/api/raid-new` and links it with `roadblock-raid-link`; linked
rows show a RAID chip instead. The Log tab renders items with type badges and
key fields and has an "Item type" multi-select filter (a session matches if it
contains any selected type). Legacy untyped entries remain editable under
"Other entries".

Activity has an explicit private-mode Scan Jira & GitHub button. Scans never run
on render, refresh or navigation. Each source uses its last successful scan
watermark, or seven days back for its first scan. Errors are visible and failed
sources must not advance their watermark.

Tasks, Activity and Log each have independent search and multi-select filters,
matching/loaded counts and Clear filters. Search uses case-insensitive terms
(all terms must match), only within the server-authorized loaded payload.
Activity and Log date ranges include both From and Through dates; undated rows
are excluded when a range is set. A reversed range reports an error and matches
no rows. These controls never trigger external scans or fetch more history.

All three views have left-side row checkboxes and Select visible/Deselect
controls. Filtering prunes hidden rows from selection; changing members,
refreshing or returning to Presentation clears selections. Activity supports
copying selected source links (deduplicated); its Jira/GitHub observations remain
read-only, without local annotations.

Private mode keeps Edit details on Tasks and adds Edit inline on Log, plus Edit
selected field. Task rows no longer expose inline task editing. Edit details
retains the full task/link and log-entry forms. Log note edits preserve every other entry
and its existing visibility. Bulk task fields are category, priority, status and
due date (blank clears it); bulk log fields are type, date and topic visibility.
Bulk editing previews record titles and count in a confirmation form before
Save. The owner applies one member's batch atomically, checks every revision,
retains log amendment history and rejects the whole batch on conflict.
Bulk operations cannot replace titles or note bodies. Presentation is read-only.
Leaving Private also clears inline drafts and filter values.
Member Tasks columns include Created, Age (days since Created), and Due. If a
task has no creation timestamp, Created/Age show `—`.

Profile edits, assignment creation/status updates and session creation/editing
use revision-checked owner mutations. Save failures are displayed explicitly.
Browser editing checks must use temporary records, never personal databases.
Calendar windows use `started_at` / `ended_at`; absent calendar records and
unknown metrics are shown explicitly, not inferred or converted to zero.
The current Plan view is a recorded delivery summary, not a complete Jira
planning dashboard or target-hit forecast.

### Workspace controls (local and remote)

Workspaces use the [workspace-manager lifecycle](../../skills/workspace-manager/SKILL.md#local-and-remote-workspace-lifecycle):
stable `WS-L-…` / `WS-R-…` IDs, machine badges, machine selection in Track
workspace, per-card status refresh, freshness/errors, mirror source IDs and
append-only action history. Tracking remote workspaces uses registered `mctl`
targets; it never starts a VM. The top **Refresh** reloads saved workspace
information from the owner database only; it never checks workspace directories,
runs Git/status commands or connects to machines. The toolbar's **Check local
status** checks all tracked local workspaces. **Check VM status** checks all
tracked remote workspaces through their registered machine targets, without
checking locals or starting machines. These labels stay fixed while running
(the clicked button is disabled). Per-card checks remain scoped to that card.
Live checks save owner readings and reload the display after completion; errors
remain explicit rather than making an old reading look freshly verified.

Refresh does not Sync. Sync and occupancy enforce a fresh clean-checkout guard;
independent occupied slots cannot Sync. Mirrors retain their development
branches on Sync and cannot be independently occupied. Free returns to
**manifest revisions**, then Syncs and verifies, retaining the hold/relation on
failure. It does not indiscriminately reset repositories to `main`.

Workspace actions run as background `/api/align/<id>` jobs. Endpoints:
`POST /api/ws-refresh` (local only), `POST /api/ws-check-vm` (remote only),
`POST /api/ws-refresh/<slot>` (one local or remote),
`POST /api/ws-sync/<slot>`, `POST /api/workspace/<slot>` (occupy/free),
`GET /api/workspaces` (cached), `GET /api/workspace-log/<slot>` (cached history).
Roster payloads include `id`, `target`, `location`, `status` and registered
`targets`. Re-read data after job completion; accepting a job is not success.

The section badge counts are *attention* counts, not totals: **Overview** shows bugs needing
attention plus hot RAID rows, **Tasks** shows active tasks, **Workspaces** shows occupied
slots, **Bugs** shows open bugs that
are stalled, untriaged, or unassigned, **PRs** shows features with at least one blocked PR,
**VM jobs** shows jobs running right now, and
**RAID** shows open rows at Red or Orange.

Each section keeps its own filters, sort and expandable detail rows, plus tabs where applicable.
All table-based views follow one portal standard: header click sorts by that
column (toggle asc/desc) and header-edge drag resizes column width. Width/sort
preferences are remembered per table in browser local storage. Selection/action
columns are not sortable.
**Tasks** is one unified table with no category tabs: use the multi-select **Category** filter
to narrow it, and **Columns** to choose visible fields without category-specific layout resets.
Review status and author filters remain available in this view; **Scan reviews** is available
when served. Selecting a single category adds its specialised summary cards. Done/cancelled
tasks remain hidden by default. The existing `#work` and `#task/<id>` links still work.
**Every filter dropdown is multi-select** (Tasks, PRs, Bugs, Phenomena, VM target): tick any
number of values; a row passes when it matches **any** ticked value of that dropdown (OR within a
field, AND across fields), and an empty dropdown means *All*. This is a **portal design
standard** — see *Filters are always multi-select* under the styling rules. In VM jobs,
**Refresh** is scoped only when exactly one target is ticked; with several it probes the whole
fleet. Switching
sections never loses the other section's filter state.

### Overview section (the storefront)

The page opens here. It is **read-only by design** — every panel points at the section or tool
that owns the underlying data. No panel calls Jira or GitHub; everything is computed from
local stores at render time.

| Panel | What it shows | Source |
|-------|---------------|--------|
| **Report header** | Title band with the as-of line (date, bugs mirrored, current sprint, live vs static) and the sprint slicer pills — MS8.1 / MS8.2 / MS8.3, current one filled | `guidelines.md`, `bugs.db` |
| **KPI band** | Six colour-coded cards: days to Power-on and to the A1 big delivery (with a run-up progress bar), open bugs + attention share (with a background sparkline of the 12-week trend), reviews on me, open RAID + red/orange share, workspace occupancy | `guidelines.md`, `bugs.db`, `tasks.db`, `raid.db`, `workspaces.db` |
| **What changed since your last visit** | Full-width feed merging every dated trail the stores already keep — bug field-change log and newly mirrored bugs, pr-monitor feature notes, task notes — newer than a day watermark, newest first, 10 shown with *+N more*. **✓ mark all seen** moves the watermark to today and remembers the ids already acknowledged today (dates are day-granular); **↺ last 7 days** resets. Each row jumps to its item. Seen-state is browser-local | `bugs.db`, pr-monitor database, `tasks.db` |
| **Bug flow — open vs closed** | 90-day cumulative stacked area of closed vs still-open bugs, from each bug's created/resolved date. Gradient-filled with end-of-line data labels | `bugs.db` |
| **Bug backlog health** | Three semicircular gauges — share of open bugs that are triaged, that carry both a milestone and a sprint, and that have an assignee. Green ≥80%, amber ≥50%, red below | `bugs.db` |
| **Mean time to close — monthly** | Bars of the average created→resolved age, bucketed by the month the bug **closed** (so a bucket is final once the month ends). Dashed red line = the all-months average; bars above it are orange. Caption calls the direction vs the previous month | `bugs.db` |
| **Open bugs by IP** | Donut; components mapped to an IP via the team-weekly scoreboard hints | `bugs.db` + `ip-scoreboard.md` |
| **Open bugs by owner** | Donut — who actually carries the open bug load | `bugs.db` |
| **Per-IP scoreboard** | Per IP: owner, open bugs, attention count, worst idle, review pressure, and a **12-week sparkline** of that IP's open-bug count. The sparkline is coloured by the IP's worst current signal, so one cell carries both the trajectory and the severity | `bugs.db` + `tasks.db` |
| **Bug hygiene** | Open bugs carrying each flag (untriaged / no-milestone / no-sprint / no-estimate / aging / overdue) as a share of open bugs — the Jira gaps to close. Each row is a link to a Jira issue search (`key in (…)`) of exactly those bugs, so the gap is closed **in Jira**; the portal itself never writes Jira | `bugs.db` (`bm.flags_of`) |
| **Development hygiene** | Full-width tile: a "pipeline clean" gauge plus seven pass/fail rules over the *delivery* pipe — CI green on open PRs, rebased on target, approved PRs landed, PRs moving (no 7-day stall), features PLDM-tested, reviews answered within 3 days, and WIP bound to a workspace. Each row shows `good / total`, a bar, and the count plus a plain-English hint. Rules with no population are dropped, so an empty PR queue can't flatter the score | `pr-monitor` cache + `tasks.db` |
| **Review queue** | Incoming PR reviews bucketed by consolidated review status | `tasks.db` |
| **RAID — open rows** | Open RAID rows, worst RAG then worst score; overdue dates in red | `raid.db` |
| **Needs you now** | One ranked feed across bugs, RAID, reviews and blocked tasks, each row carrying a kind-coloured left border. Bugs outrank reviews at equal urgency, and RAID is capped at 3 rows so a long risk backlog cannot crowd out live bugs | all |
| **Workspace occupancy** | Every physical slot and what holds it | `workspaces.db` |

The portal uses a **dark cosmic shell** with a collapsible, grouped section rail, live
attention badges, a compact top bar, and responsive horizontal navigation on narrow screens.
The Tasks table keeps its full set of columns in a local horizontal scroll container on
narrow screens, so it does not widen the page. The portal remembers the selected section and
sidebar state in browser-local storage, and each Overview tile can be collapsed independently
with its state remembered locally; neither preference changes shared data or server workflows.
The Overview remains a **Power BI style report canvas**: an as-of line and sprint slicer pills,
a 6-card KPI band, then a 12-column tile grid
(`.ov-canvas` + `.t2`…`.t12` span classes, collapsing at 1240px and 780px). Colours come
from the `PBI` / `PALETTE` objects, which mirror the Power BI default theme.

Charts are **hand-rolled inline SVG** (`svgDonut`, `svgGauge`, `svgStackedArea`, `svgBars`,
`svgSparkline`, `barTable`) — the portal carries no third-party JS dependency and works
offline. Each chart is a **single** svg with an intrinsic `viewBox` and no explicit height,
so it scales inside its tile without the plot/label drift a split-svg layout suffers from.

> The Overview reads RAID through `rd.py` and parses `~/team-planner/guidelines.md` and
> `~/.copilot/skills/team-weekly/references/ip-scoreboard.md` **read-only**, each behind its
> own `try/except` with a hardcoded fallback. A missing or reformatted team-planner file
> degrades one panel, never the page. Endpoint: `GET /api/overview`.

### Bugs section

**Bulk actions.** Every bug row has a tick box (header box ticks every bug shown). Ticking
any row shows a bulk bar: **📣 +1 ping** and **set triage…** apply to every ticked bug, one
`POST /api/bug/<KEY>` each, after a confirm listing the keys. Local monitoring fields only —
never Jira. Ticks on rows the current filters hide are dropped, so a bulk action can never
reach a bug you cannot see.

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

#### Triage evidence (bug-triage integration)

- A **Phenomena** tab (before *All*) lists the `ph.py` registry: status, severity, platforms,
  sightings, last seen, owner, flags, linked Jira. Filters: status / signal / platform / flag,
  search, *Show closed*. Expanding a row shows root cause, fix, linked bugs (with unlink ×),
  investigation notes, the sightings table (raw evidence in a `<details>`), signatures, the activity
  log, and an **editor** that posts only changed fields (title, status, severity, owner, component,
  root cause, fix version/PR/commit/repo, tags, link Jira, link note, append note) to
  `POST /api/phenomenon/<ID>` → `ph.apply_ph_edit()`.
- The bug table gains a **🔬 evidence** column and an *Evidence* filter (PH-linked / note /
  mention-only / any / none). Evidence ranks `explicit` > `filename` > `mention`; weak
  *mentioned* matches are shown but labelled.
- An expanded bug shows a **triage block** above the editor: PH cards (status, root cause, fix,
  sightings) and the investigation notes. A linked phenomenon **owns** the bug's root cause — the
  bug's root-cause input is disabled ("owned by PH-…") and the backend rejects a divergent edit.
- Investigation notes open in a **Markdown viewer** modal (`GET /api/investigation?name=`; names
  are validated against the investigations dir). On a static file the viewer shows a hint instead.
- **Origin badge — customer vs internal.** Every bug and phenomenon carries a `customer` /
  `internal` badge under its key, an **Origin** filter (both tables) and the reason in the
  detail view. Derived by `bugdb.ph_origin()` / `bugdb.bug_origin()`, never stored: any
  `aqua-external/<repo>` git reference (the customer's own GitHub org) ⇒ **customer**, for sure;
  so is a sighting sourced `customer`/`email`, a customer Jira label, or a link to a customer
  phenomenon. Everything else is internal. Override with a `customer`/`internal` **tag** on a
  phenomenon, or the **Origin** select in the bug triage editor (`triage.origin`, local only).
- The Overview *Needs you now* feed carries a `phenom` kind for open phenomena flagged
  escaped / regressed / unexplained-recurring; clicking it opens the phenomenon in the Bugs tab.

Server endpoints added for this section: `GET /api/bugs` (full payload), `GET /api/triage`
(phenomena + note index), `GET /api/investigation?name=`, `POST /api/phenomenon/<ID>`,
`POST /api/bug/<KEY>` (apply one edit, returns the refreshed payload) and `POST /api/bug-scan`
(Jira refresh, serialised by `BUG_SCAN_LOCK`). If the `bug-monitor` skill is not installed,
`portal.py` degrades gracefully: the Bugs section renders empty.

### PRs section

Mirrors the [`pr-monitor`](../../skills/pr-monitor/SKILL.md) memoir — the feature PRs *I* have out, as
opposed to the incoming review queue that lives under Tasks.

- A KPI band (blocked features, CI failing, needs rebase, changes requested, approved,
  merge queue, merged) over a compact **feature table**. Each feature is a collapsible summary
  row (feature, **priority** pill, state, merged/total — PLDM status only in the expanded view) that expands inline, laid out like the
  code-review detail: a short **summary** (marked *auto* when derived) with the technical
  description folded underneath, a key/value strip (**Jira** links, logged, last activity,
  progress, PLDM), the **Pull Requests** block (action buttons, then the PR table — repo/PR,
  title, **CI**, check counts, review-or-merge gate, status), the full dated **activity
  history** (newest first, scrolls), and an editable metadata form folded under a
  collapsible **Edit feature** disclosure (collapsed by default; its open state is kept
  per feature across re-renders within the page session).
- Feature metadata editing is local to the pr-monitor database: expanded rows can edit
  **Name**, **Priority**, **Jira**, **Summary** and **Description**, and append a labelled activity note
  (`POST /api/feature-edit` with any of `name`, `priority`, `summary`, `jira`, `description`, `note`,
  `note_label`). Only changed fields are sent, so derived values are never written back.
  PR status and PLDM state remain
  read-only because they are derived from GitHub scans / PLDM queue state.
- **Priority is also editable on the collapsed row**: the priority pill is a select that posts
  `{feature_id, priority}` to `/api/feature-edit` on change (reverting on error), so triage
  doesn't need the row expanded.
- The toolbar keeps global controls (collapse all, track feature, scan PRs), and the section has
  a search box plus **multi-select dropdown filters with tick boxes** (per-option counts and a
  *Clear* button) for feature state, priority, PLDM state, repository, and PR status. Ticks OR
  within one filter and AND across filters; nothing ticked means *all*.
- The **CI** column is a one-word verdict pill derived from the check tallies — red *fail*,
  amber *running*, green *pass*, or a dashed *not run*. Hovering names the failing checks.
  *not run* is its own state on purpose: CI here only fires on a `/ci` comment, so an empty
  check list means nobody triggered it, not that everything is fine. The neighbouring
  **Checks** column keeps the raw `3F/9P` tallies for when the verdict isn't enough.
- Features are ordered **worst status first**, and a feature whose PRs are all merged is
  labelled *ready to drop from tracking*.
- **↻ Scan PRs** POSTs `/api/pr-scan`, which runs `pm.refresh()` server-side: `gh pr view` +
  `gh pr checks` for every tracked PR in parallel, then the new statuses are written back into
  the domain store. **This costs zero tokens** — no model is involved. ~30 PRs take ~15s. The button
  is disabled on a static dashboard file; it needs `serve`.
- Each feature row has a **tick box** (plus select-all in the header). Ticking at least one
  reveals **↻ Scan selected PRs (N)** next to *Scan PRs*, which POSTs
  `/api/pr-scan` with `{"feature_ids": [..]}` → `pm.refresh(only=[..])`: only those features'
  PRs are probed, and every other feature keeps its cached CI/check detail. The selection
  survives filtering and re-renders, and drops ids that are no longer tracked.
- **▶ PLDM test** on each feature card submits the whole feature to the
  [PLDM job queue](../../skills/pldm-job-queue/SKILL.md) — see below.
- **↻ ci** on each PR row, and feature-level **↻ CI all** / **↻ CI failed** buttons, POST
  `/api/pr-ci`, which runs `pm.trigger_ci()` server-side to post a `/ci` comment on the PR(s)
  via `gh pr comment` — the only way CI runs here. *all* covers every open PR (skips
  merged/closed); *failed* covers only `ci-fail` PRs. Results (posted / failed per PR) land in
  the card's `ci-note`. Serialised by `PR_CI_LOCK`; needs `serve`.

### Pipeline explorer (Oded's dashboard) — deep links

The portal does not rebuild CI analytics; it links into the
[CI/CD pipeline explorer](https://github.com/Elmnt-sandbox/ogabbay_cicd_pipeline_explorer),
run as the always-on systemd user service `pipeline-explorer.service` on
`http://127.0.0.1:8080` (venv at `~/odeds_sandbox/.venv`; override the URL with
`PIPELINE_EXPLORER_URL`). Wired in:

- **why red →** on a PR whose CI failed opens the explorer's `/workflow?url=<run>` analysis of
  the first failing run (`failed_runs` from the pr-monitor cache).
- **⇢** on every PR row (hover) opens `/pr?url=<PR>` — post-merge pipeline status.
- **⇢ Pipelines ▾** in the PR toolbar opens the explorer's views: open PRs & merge queue,
  pipeline history, integration queue, branch timeline, manifest versions, BKC, diagnostics.
- An **Elsewhere → Pipelines** rail link with a live up/down dot (probes `/diagnostics`).

If the explorer is down the links still render; the dot turns red.

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
   pr-monitor database, then the PR section refreshes.

The slot is released automatically whether the job succeeds or fails. Progress is polled from
the shared `/api/align/<job>` endpoint and shown as a per-repo log in the card.

Post `{"dry": true}` to validate a submission — slot resolution, availability, PR count — and
get back the plan without reserving a slot, touching a checkout or dispatching a build. Use it
whenever testing this endpoint; the normal path really does start a CI build.

Endpoints: `GET /api/prs`, `POST /api/pr-scan` and `POST /api/pldm-test` (serialised by
`PLDM_LOCK`). Tracking/metadata edits use `POST /api/feature-track`, `POST /api/feature-edit`,
`POST /api/feature-untrack`, `POST /api/pr-track`, and `POST /api/pr-untrack`; CI reruns use
`POST /api/pr-ci`. Without the `pr-monitor` skill the section renders empty and the scans return 503.

### VM jobs section

A read-only view over [`vm-delegate`](../../skills/vm-delegate/SKILL.md)'s delegation history, driven by
`vd.py`: an EC2 power strip (state dot, lease countdown, Start/Stop), the job running right now,
KPI counts, and the full history table.

- **Refresh status** (`POST /api/vm-refresh`) is a **background job**, polled through
  `/api/align/<id>` like the PR scan, and it streams one row per target into the align banner.
  It is not inline for a reason: one unreachable target costs a 90 s ssh timeout and `octl vm
  list` revalidates SSO, so the old inline version routinely outlived the browser's fetch — the
  section then never re-rendered and the whole tab looked broken while the probe was succeeding
  on the server.
- **Refresh one machine** with the `↻` on its EC2 card, or on its chip in the bare-metal target
  strip; the toolbar button follows the target filter and relabels to *Refresh vm*. Same
  endpoint with `{"target": "<name>"}`. This is the difference between ~4 s and minutes: a
  fleet refresh waits out the ssh timeout of every dead lab desktop. Each card and chip shows
  its **own** age, because a scoped refresh must not make the untouched targets look fresh —
  and `octl vm list` is skipped entirely for a target with no instance behind it.
- **Check VM status** (`POST /api/vm-machines`) re-reads only `octl vm list`; **Sync logs**
  (`POST /api/vm-sync`) only rsyncs the remote job logs, also scoped by the target filter. Both
  are narrower than a full refresh.
- The **Log** column opens the mirrored log (`POST /api/vm-log`); for a *running* job it opens a
  **live tail** (`POST /api/vm-tail`) that re-polls every 5 s with **Follow** on. Each poll is
  one `tail -n` over ssh — no websocket, nothing to tear down but a `setInterval`. Neither
  endpoint takes `VM_REFRESH_LOCK`: reading a log must never be blocked by an in-flight probe.
- The header and the power strip print how **stale** each reading is (`refresh_age_h`,
  `machines_age_h`). Without that, a day-old `stopped` is indistinguishable from a live one.

Without the `vm-delegate` skill the section renders empty and the scans return 503.

### RAID section

A full editor over [`team-planner`](../../skills/team-planner/SKILL.md)'s `~/tasks/raid.db`, driven
by `rd.py`. The Overview keeps its read-only RAID tile; this is where rows are actually changed.

- KPI band by RAG (Red / Orange / Yellow / Green) plus Overdue and Closed, over a table sorted
  worst-RAG-then-worst-score. **Show closed** reveals resolved rows.
- Clicking a row expands the full narrative (description, trigger, mitigation, contingency,
  external owner, link, status) above an inline editor for title, type, owner, P, I, RAG and due.
- **Save** submits **only the fields you actually changed** — each control carries a `data-orig`
  and unchanged ones are dropped. This matters: the displayed values are markdown-stripped, so
  blindly posting them all would silently erase backticks and links from `raid.db`.
- **Close row…** opens an inline resolution field (Enter / *Confirm close*; Esc cancels) and stamps
  `**Closed <today>** — <resolution>`. It is inline on purpose: VS Code's integrated browser throws on
  `prompt()`.
- **+ New row** allocates the next ID for the type and appends a complete block.

Endpoints: `GET /api/raid`, `POST /api/raid/<ID>` (field edit, or `{close: "..."}`) and
`POST /api/raid-new`, all serialised by `RAID_LOCK` in addition to the RAID owner's database transactions.

### Jump palette, deep links and keyboard

- **Ctrl/⌘+K** (or **⌕ Jump** in the top bar) opens a palette over every store: sections,
  tasks, bugs, phenomena, PR features and individual `repo#N` PRs, RAID rows, explorer views.
  Exact tag hits rank first. Unknown input still goes somewhere: a GitHub PR URL or unknown
  `repo#N` → explorer PR view, a run URL → explorer run analysis, an unknown Jira key → Jira.
- **The URL hash names the view** — `#prs`, `#bug/KEY`, `#ph/PH-004`, `#task/T-101`,
  `#pr/17`, `#raid/I-007` — so any view is bookmarkable, and it is honoured on load and on
  `hashchange`. Updated with `replaceState`, so it does not flood history.
- **Keyboard** (ignored while typing): `j`/`k` move a row cursor, `Enter`/`e` expand,
  `x` ticks (Bugs, PRs), `/` focuses the section search, `g` then `o w s b p l v r` switches
  section, `?` shows the cheat sheet, `Esc` closes / leaves a field.

## Dashboard server (`serve`) — operational notes

The `serve` subcommand backs the **editable** Team Portal. The static
`dashboard` output is read-only; editing only activates when the page is loaded
from the running server (it probes `/health` and `/api/tasks`).

- **Restart after upgrading `portal.py` or a skill module** (`pm.py`, `wm.py`, `bm.py`,
  `rd.py`, `pjq.py`, `vd.py`). The server holds the page template and the imported
  modules in memory. `serve --stop` then start again (or
  `systemctl --user restart task-dashboard`).
- **No restart for `assets/portal.css` / `assets/portal.js`** — they are read off
  disk per request and cache-busted by mtime. Just reload the page.
- **Open the served URL, not the file.** Editing is enabled only at
  `http://127.0.0.1:8787` (the served page). Opening `~/.copilot-private/team-portal/state/dashboard.html`
  directly (or any `file://` path) shows a **read-only** badge by design.
- **Port already in use.** If a leftover server holds the port, the systemd unit
  (`task-dashboard.service`) fails with `Address already in use` and auto-restarts
  in a loop. Run `python3 ~/.copilot/team-portal/portal.py serve --stop`
  (or kill the bound PID) before starting the unit.
- **Survive a full logout.** The systemd user service starts at login. To keep it
  running with no active session, run `sudo loginctl enable-linger $USER` once
  (needs polkit/sudo, so it is not done automatically).

### Front-end layout

The client is three files, not one:

| File | Holds |
|------|-------|
| `portal.py` | the portal server and `DASHBOARD_TEMPLATE` (markup only) |
| `assets/portal.css` | every style, built on a small token set |
| `assets/portal.js` | every behaviour, ~3k lines |

`portal.py` passes data in a single `window.__PORTAL__` bootstrap object
(`boot_payload()`); `serve` links the assets from `/assets/…`, while
`tm.py dashboard` inlines them so the static export still works over `file://`.

Styling rules worth keeping:

- Use the tokens — `--r-xs/sm/md/lg/pill` for radii, `--ok/--warn/--danger/--info`
  (plus their `-soft` tints) for state. Keep the cosmic palette in the root tokens and use
  semantic colors for state rather than adding decorative raw colors throughout the UI.
- There are exactly **three button families**: *action* (`.btn` and friends),
  *micro* (`.ci-btn`, `.pldm-btn`) and *ghost* (icon-only, e.g. `.pr-x`). A new
  button joins one of the grouped selectors; it does not get a bespoke rule.
- The left rail is generated from `SECTIONS` in `assets/portal.js`; keep its section IDs,
  attention counts, accessible current-page state, and mobile labels in sync with `portal.py`.
- **Filters are always multi-select (standard).** Any new control that filters entries must
  let the user pick several values — never a single-value `<select>`. Write it as a plain
  `<select data-filter>` whose first option is `<option value="">All …</option>` (the
  empty-state label); `portal.js` upgrades every `select[data-filter]` into a checkbox dropdown
  automatically, including ones rendered later (a document MutationObserver calls `msEnhance`),
  so no per-filter wiring is needed. In the filter logic read `msVals(sel)` and match with
  `msHas(vals, v)` / `msAny(vals, arr)` — OR within a field, AND across fields, empty = all.
  Rebuilding options and resetting with `.value=""` keep working; `change` still fires.
  Exempt: **sort pickers** and **editor fields** (they hold one value by nature) — leave those
  without `data-filter`. Tasks/PRs use the older `.dd` checkbox markup, which is equivalent.
- Validate edits with `node --check assets/portal.js` and a `{`/`}` count on the CSS.


### Auto-alignment on code-review assignment (manual-review checkout)

When you assign a **`code-review`** task to a workspace slot from the served dashboard —
either the **Workspaces** section's *Occupy with a task* control or the task editor's
**workspace** field — the server automatically aligns that workspace for a **manual
(no-AI, no-credit) review**, the dashboard equivalent of review-mgr `start --manual`:

- It checks each PR-entry repo out to its `reviews[].branch` and switches every other
  git repo in `~/projects/<project>/<slot>` to its default branch (`main`).
- It runs as a **background job** (git fetch/checkout is slow) so the POST returns
  immediately with an `align_job` id; the UI polls `GET /api/align/<job>` and shows a
  floating banner with **live per-repo progress** and a **done/error indicator**.
- It is **non-destructive**: dirty repos are skipped and reported, never reset. Repos the
  task references but that aren't present in the workspace are reported as *not-found*.
- **PR recognition requires upstream verification, not just checkout.** After dashboard,
  CLI, or script alignment (including the saved-snapshot fast path), apply review-mgr's
  [PR upstream and VS Code review detection](../../skills/review-mgr/SKILL.md#pr-upstream-and-vs-code-review-detection)
  checklist to every PR repo. Resolve the PR's live head repository and branch to the
  correct remote-tracking ref and set/verify it; do not assume `origin`, use the PR base
  or workspace revision as the upstream, or treat a PR-ref fetch as tracking setup.
  Forks require the head repository's remote. Explain the choice and future pull/push
  effect; report ambiguous/deleted/inaccessible heads rather than fabricating an upstream.
  These are workflow postconditions to verify, not a claim that the current server
  implements them: safely repair/report missing tracking and check the extension log
  before declaring PR recognition. Local Git success alone does not prove visible UI.
- On completion it appends a `workspace <slot> aligned for manual review` note to the task.
- **It snapshots the aligned composite state** via the [wip-workspace](../../skills/wip-workspace/SKILL.md)
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
- **Setting a slot free reverses it:** the guarded background operation returns
  every repository to its manifest revision and runs Sync. Dirty or local-only
  work blocks release rather than being skipped into a success result. The hold
  is removed only after the clean baseline has been verified.
