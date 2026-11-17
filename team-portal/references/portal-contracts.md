# Team Portal behavior contracts

Portal-specific documentation migrated from task-manager. Paths to runtime files
are relative to `~/.copilot/team-portal/`. Domain APIs, not legacy files,
are authoritative; see [architecture](./architecture.md).

## The Team Portal (dashboard)

`dashboard` / `serve` no longer render a personal task list — they render the **Team Portal**,
a single page with a top-level section nav:

| Section | Data source | Owning skill | State |
|---------|-------------|--------------|-------|
| **Overview** | all of the below (read-only, via each section's API) | `portal-maintainer` (aggregator) | live, **landing section** |
| **Tasks** | `~/tasks/tasks.db` | `task-manager` | live |
| **Workspaces** | `~/tasks/workspaces.db` | `workspace-manager` | live |
| **Bugs** | `~/tasks/bugs.db` (bugs, families, phenomena) + `~/.copilot-private/skills/bug-triage/investigations/*.md` | `bug-monitor` + `bug-triage` | live |
| **PRs** | `~/tasks/prs.db` | `pr-monitor` | live |
| **Machines** | `~/tasks/delegation.db` (mctl log ingestion for VMs and lab machines) | `vm-delegate` | live |
| **RAID** | `~/tasks/raid.db` | `team-planner` | live |
| **Team** | `~/tasks/team.db` via `team-manager` | `team-manager` | live: Overview / Plan / Members |
| **Settings** | `state/portal-settings.json` (server) + browser `localStorage` `team-portal-prefs-v1` | `portal-maintainer` | live; editable only when served |

The PLDM job queue section was discontinued (the queue is maintained in the company
PLDM portal); `/api/pldm-jobs` and `/api/pldm-scan` no longer exist. PLDM test
submission, status badge and filter remain in **PRs**.

### Settings section

Three tiles:

- **Daily maintenance routine** (server-side, `GET`/`POST /api/settings`):
  - on/off, run time and run days (Sun–Sat checkboxes with Every day / Sun–Thu / Mon–Fri presets)
  - per-step toggles for reviews, PRs, bugs, workspaces and team (a turned-off step shows as `off` and does not degrade the run)
  - auto-restart on failed health
  - snapshot retention (1–60 days)
  - action-queue retention (7–365 days)
  - team activity window (1–168 h)
  - brief stale threshold (1–168 h)

  Saving:
  - is revision-checked: a stale form gets 409 and reloads
  - is validated server-side
  - is journaled as component `portal-settings`

  A schedule change rewrites the managed timer drop-in and restarts the timer. If the apply fails, the file and the timer are rolled back.
- **Grouped settings** (same endpoint, same revision check), one tile per group:
  - **AI assistance**: allow AI runs, model (`fusion` = Copilot picks), runs per day
    (hard cap across every AI kind; 0 disables), interactive-review messages per day
    (separate cap, default 150, 0 disables), run timeout, review timeout and
    whether Explain/Ask may *read* Jira/GitHub. The routine's `ai` step toggle sits with
    the other step toggles.
  - **Machines**: terminal targets (get a `>_ Terminal` button), live-status targets
    (persistent ssh link) and the live update interval (5–300 s).
  - **Notifications**: master switch, the four kinds (action queue, machine jobs, AI,
    machine reachability) and the Windows toast fallback.
- **Routine status**: timer active/enabled, next and last trigger, linger, last brief result, and any schedule problem.
- **This browser**:
  - landing section (last visited or a fixed section)
  - clock 12/24 h/auto
  - compact tables
  - action and VM poll intervals
  - nav order and visibility

  Settings itself can never be hidden. These preferences are per browser and never reach the server.

### Date presentation standard

All user-visible date fields use `dd/mm/yyyy`, independent of browser locale.
Date-time fields use `dd/mm/yyyy` followed by the existing time and timezone
suffix. Editable date fields accept only `dd/mm/yyyy`; invalid calendar dates
are rejected with a validation message. The portal converts valid input to ISO
`yyyy-mm-dd` before calling owner APIs, and keeps ISO dates in payloads,
storage, sorting and date-range comparisons. Generated log titles retain their
existing `YYYY-MM-DD` form for compatibility; they are titles, not date fields.

### Team section

Team includes the lead and all engineers. Member pages show focus assignments,
saved activity, 1:1 sessions and incoming reviews. Sessions and assignments use
the owner payload's top-level collections, filtered by member key.
`#member/<key>` and `#team/plan` open the corresponding views.

**Add member (onboarding).** Private mode only (needs `onboarding_spec`), top
mode bar. The dialog is generated from the owner's intake spec; first submit
posts `onboarding-preview` (no write) and renders blocking errors, readiness
gaps with impact, the dated checklist and what registration does not do; the
button becomes **Register**, and any input change invalidates the preview.
Register posts `onboarding-apply` with the reviewed `plan_revision`, then opens
the new member's Items. Overview shows an `Onboarding done/total · N overdue`
badge until the checklist completes; the member header shows progress, next
step and (Private only) missing readiness fields. Edit profile exposes role,
employment type, Jira account ID and GitHub handle to close those gaps.

Presentation mode is the default on every page load. Private mode is explicit
and is not remembered across reloads. Leaving Team or switching to presentation
immediately discards the private payload from the UI and restores the last
server-filtered shared payload. Late private responses cannot overwrite it.
Member goals and meeting entries follow their owner-defined visibility.

The mode control sits at the top-right of Team, with green Presentation and red
Private fill, without a sensitive-data warning banner. Focus is an editable
status-free field on the member header card, not a tab. The Items tab contains
every tracked log row, with Task as one type alongside decisions, discussion,
feedback, roadblocks, growth, personal and freeform/legacy notes. Existing Tasks
subtab routes remain compatible.
Edit focus and Edit profile share a horizontal action row. Profile editing uses
the same Save/Cancel popup form as focus, with main IP, country, goals and goals
visibility prefilled; no browser prompt sequence. Saves remain revision-checked.

Member items display stable item IDs, type, priority, status, owner, due date,
follow-up and expandable meeting/update history. Type/status/priority/follow-up
filters, search, Open only and visible-row selection operate on all item types.
Private mode supports common tracking edits, append-only updates and atomic bulk
status/priority/due/follow-up edits. Tasks retain their separate Edit details form
for task category, description and artifact references.
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
style. Type, Topic, Date and Topic visibility share a row when space permits.
The selects fit their longest choices, the date fits `dd/mm/yyyy`, and Topic
uses the remaining space; narrow screens wrap the fields. Topic is optional;
blank date means today. Typed item rows occupy the largest area; freeform notes
are a compact 3-row box below them. Notes start
expanded, with a visible Expand/Collapse control and a single-line preview of
the current text and priority. The native disclosure is keyboard-operable, and
collapsing it preserves its values for saving. A toolbar adds
rows of any type served in `log_item_types` (Action item, Decision, Discussion
point, Status update, Roadblock, Feedback given, Feedback received,
Growth/career, Personal); rows can be collapsed individually. New rows open
with their short summary visible; optional detail fields are under an expandable
"More details" section. Each item has a changeable type/category, priority
(Critical, High, Medium or Low; default Medium) and visibility. Action-item
priority is the linked task's priority and is edited in Items after creation.
Each row, including Notes and legacy rows, has common tracking status, optional
owner/due date and Follow up next 1:1 controls. These are distinct from a status
update's optional task-status change and received feedback's follow-up text.
Existing non-task statuses remain Unspecified; new non-task rows start Open.
Topic, notes, legacy entries and typed items default to Presentation; editing
preserves each entry's saved visibility. Party selects show the member's name;
on the lead's own page the counterpart is the manager (`manager_name`, e.g.
"Manager") and blank 1:1 titles use the manager's name. State and session-level
visibility are not form fields. Presentation shows a session only if it has
presentation-visible notes/entries; topic visibility independently controls
whether its title is disclosed. Private notes remain filtered. Legacy workflow
metadata is retained internally; ordinary edits need no state transition
selection.
Save collects typed items only from their dedicated row container; freeform
notes and legacy entries are serialized separately, once each.
Saving creates/updates each item's linked record atomically with the session:
action → member task (once; task-detail edits happen in Items and the row is
read-only with a task link), status update with a task → task progress note and
optional status change; without a task → standalone progress entry with an
optional activity name and no task mutation, roadblock → roadblock record,
feedback → feedback record.
Removing the last roadblock/feedback/status occurrence deletes its linked record
and cancels its item; records still used by another active meeting remain intact.
Tasks are never deleted. Saved roadblock rows offer "Promote to RAID", which creates a RAID
issue through `/api/raid-new` and links it with `roadblock-raid-link`; linked
rows show a RAID chip instead. The Log tab renders items with type badges and
key fields and has an "Item type" multi-select filter (a session matches if it
contains any selected type). Log items are grouped by type and sorted by
priority within each type (Critical, High, Medium, Low; ties preserve entry
order); each displayed item can be collapsed or expanded. Legacy untyped entries
remain editable under "Other entries".

Creating a new 1:1 requests an owner-prepared draft and automatically includes
checked items from earlier 1:1s with that member, marked Follow-up from
`dd/mm/yyyy`. These references reuse item/task identities and preserve past
meeting text. Follow-ups repeat until cleared or Done/Cancelled; new Conversation,
Meeting and Note entries do not carry them. Changing the new meeting's date/type
reloads eligibility without replacing edits to still-eligible rows; removing
edited rows requires confirmation. Load failures are shown and block saving the
unprepared draft. Removing a carried row skips it only in that draft; clearing
its follow-up flag stops future carry-forward. Existing saved meetings are not
automatically rewritten. Private carried content never enters Presentation mode.

Presentation mode permits editing a log entry's shared-visible content without
loading private-only entries. The owner API revision-checks the edit, preserves
all hidden private entries and hidden topics unchanged, rejects private rows in
the request, and returns only the shared projection. Creating/editing other team
records remains Private-only.

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
selected field. Presentation mode offers Edit shared log for existing log rows.
Task rows no longer expose inline task editing. Edit details retains the full
task/link and log-entry forms. Log note edits preserve every other entry and its
existing visibility. Bulk task fields are category, priority, status and
due date (blank clears it); bulk log fields are type, date and topic visibility.
Bulk editing previews record titles and count in a confirmation form before
Save. The owner applies one member's batch atomically, checks every revision,
retains log amendment history and rejects the whole batch on conflict.
Bulk operations cannot replace titles or note bodies. Tasks, Activity and bulk
operations remain read-only in Presentation; the scoped shared-log edit described
above is the only Presentation-mode mutation.
Private-mode Log rows also offer Delete entry. Deletion requires confirmation,
checks the displayed revision, hides the session from both audiences, and
preserves its immutable revision history. Linked progress, roadblock and
feedback records are removed using the existing item-removal rules; linked task
assignments remain in Tasks.
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

### Action queue

All progress-popup jobs use one durable resource-aware queue: workspace status
checks, Sync, occupy/free, automatic review alignment, PR/review scans, machine
activity refreshes and PLDM submissions. Up to three independent actions execute at once.
Actions sharing a workspace, remote machine or affected owner domain wait in
submission order; unrelated actions can bypass them. Whole-workspace scans claim
every selected workspace. Shared task-store updates and PR operations remain
conservatively serialized within their domains. Auto-selected PLDM workspaces
and unrecognized action kinds run exclusively. Future queued action kinds must
declare all affected resources (including member IDs for member-scoped actions)
before opting into concurrency.
Busy actions wait rather than being rejected because another queued action
holds their resource. Owner validation and resource locks still apply at
execution; failed actions show an error and the queue advances.

The top-bar **Actions** card is wide and live: it names the running action
(or "N running" when several run) and clicking it toggles a dropdown anchored
to the card, not a modal popup. The dropdown lists running,
waiting positions and the latest 20 finished actions; positions describe
submission order, not a promise of execution order. Select an entry to see
its progress/result. Collapse hides the list and details while retaining the
selected action's header. Dismiss does not cancel or clear the queue. Reloading
reattaches to the queue and completed observed actions refresh saved section
data without starting external scans.

`GET /api/actions` returns active jobs and recent history; existing submission
URLs and `GET /api/align/<id>` remain supported. The queue response includes
`max_workers`; scheduled waiting jobs expose `blocked_by` action IDs and a
`waiting_reason` (resource conflict or execution capacity). Job states are `queued`,
`running`, `done`, `error`, `approval_required` and `cancelled`; clients must keep
polling while queued or running.

**Cancel.** The selected action's detail offers **Cancel** (queued, or awaiting
approval) or **Stop** (running AI work), always behind a confirm, posting
`POST /api/actions/<id>/cancel` (same-origin; 404 unknown, 409 already finished
or not interruptible, 200 `{status, job}` with status `cancelled` or
`cancelling`). A queued/approval job never starts; its owner record is closed
(AI run marked "cancelled before it started", a review chat that never opened
becomes `interrupted`). A running job is only interruptible at a copilot call
(`ai`, `online-review`, and `review-chat` turns/previews/opens — never a
`submit`, which posts to GitHub): the process is killed, the job carries
`cancel_requested`, shows *cancelling*, then finishes `cancelled` without a
failure notification. Every other running kind (scans, workspace actions,
alignment, PLDM, auto-review, machine refresh) returns 409 because stopping it
mid-way could leave external state half-applied. AI budget is not refunded.
Pending jobs survive server restarts in the private portal queue database.
Interrupted running jobs become explicit errors, never automatic retries.
Check the external state before submitting an interrupted action again.
AI kinds join the same queue: `ai` (Explain/Ask/brief) claims only its own
run id, so AI calls run in parallel with everything else; `auto-review` claims
`auto-review:<workspace id>` and *not* the workspace itself, because it submits
its own occupy/free workspace actions and waits for them (claiming the
workspace would deadlock on its own sub-jobs). `review-chat` claims
`review-chat:<chat id>` (one turn at a time per chat); its `open` operation also
claims `auto-review:<workspace id>`.
Inline actions outside the popup (including Team member operations, Jira scans
and `/ci` posting) retain their existing behavior. Remote workspace actions
also acquire the VM owner lock to exclude VM controls and watchdog probes.

### Workspace lifecycle

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

Each served workspace card has an **Edit** button for its saved label, badge,
group, revision and **Dedicated purpose**. The form is prefilled and saves only changed fields through
the workspace owner's metadata API. Revision is locked while occupied or
mirrored; purpose is also locked while held or mirrored. Label, badge and group remain editable. Saving metadata does not run
a checkout, Sync or status check. Paths, machine targets, IDs, occupancy and
live readings are not editable in this form. Static exports remain read-only.

**Dedicated purpose** is persistent workspace-owner metadata, not a hold. Track
defaults to **General**; legacy records without the field retain General behavior.
Dedicated cards show an `<purpose> only` badge. The Workspaces **Purpose** filter
is multi-select (none means all), independent of grouping. Task choices and
ad-hoc tags are filtered using owner-provided `allowed_uses`; server APIs recheck
compatibility at execution and never accept client-supplied permissions.

| Purpose | Compatible work |
|---|---|
| General | Existing behavior |
| Review | Code-review tasks, review holds, outgoing source mirroring |
| Development | Non-review tasks, development/debug/experiment holds, outgoing source mirroring |
| Build root / Mirror | Incoming mirrors, build/test jobs and build/test holds; no ordinary task/review assignment |

Build root and Mirror retain distinct names but share a policy. Review and
Development slots may source delegated builds, not execute them. Automatic PLDM
workspace selection and its explicit choices include only build-compatible local
baseline slots. Forced use cannot bypass dedication. Free retains purpose.
Changing purpose requires a free, non-mirrored workspace, no running operation,
no matching running/unverified delegated job, and, for portal edits, no conflicting
queued portal action. Queued actions revalidate assignment/selection on execution. No physical
workspace is reclassified automatically. Generic remote commands are refused
on dedicated destinations; the metadata is not an OS sandbox.

**Stop tracking** opens a confirmation dialog with an unchecked **Override
occupancy/mirror checks** option. `POST /api/ws-untrack/<slot>` accepts a boolean
`force` (default false). Normal untracking refuses held workspaces and mirrors.
The explicit override removes only the roster entry: checkout files, task/ad-hoc
holds, saved readings, mirror state and history remain intact. It does not Free
or release a mirror, contact machines, or bypass active-operation locks or
workspace validation. API failures keep the dialog open for an explicit retry.

Refresh does not Sync. Sync and occupancy enforce a fresh clean-checkout guard;
independent occupied slots cannot Sync. Mirrors retain their development
branches on Sync and cannot be independently occupied. Free returns to
**manifest revisions**, then Syncs and verifies, retaining the hold/relation on
failure. It does not indiscriminately reset repositories to `main`.

For a **free, independent** workspace, Sync blocked by dirty files or unexpected
branches offers a **Force sync** confirmation. It reuses the snapshot-bound
stash-and-return safeguards below, then syncs to manifest revisions without
occupying or releasing the workspace. Occupancy is checked again when the
confirmed job executes. Mirrors retain normal Sync behavior and cannot be
force-synced; network/authentication failures do not offer this override.

Workspace actions run as background `/api/align/<id>` jobs. Endpoints:
`POST /api/ws-refresh` (local only), `POST /api/ws-check-vm` (remote only),
`POST /api/ws-refresh/<slot>` (one local or remote),
`POST /api/ws-sync/<slot>`, `POST /api/workspace/<slot>` (occupy/free),
`GET /api/workspaces` (cached), `GET /api/workspace-log/<slot>` (cached history).
Roster payloads include `id`, `target`, `location`, `status` and registered
`targets`. Re-read data after job completion; accepting a job is not success.

For **Return & free**, **Set free**, and **Release mirror**, a fresh check in
the queued job identifies repositories whose work could be lost (uncommitted
changes, local-only/unverifiable commits, errors) before any checkout change.
Repositories that are only on another pushed branch return without asking.
Review-purpose workspaces never ask: their local work is discarded (see the
workspace-manager skill). Otherwise the job finishes as `approval_required` and
shows their branches, manifest
revisions, reasons, and any safety blockers. Cancel leaves the checkout and
hold unchanged. Explicit confirmation submits a new job linked to that
approval; a changed checkout is checked again and requires fresh confirmation.
The approval remains available in Actions after a page reload or portal restart
until it is used or superseded: submitting any newer Free/Sync for the same
workspace closes older `approval_required` jobs for that action as `done`
("Confirmation used by …" / "Superseded by …"), so stale confirmation buttons
cannot replay an old snapshot.

An approved return stashes staged, unstaged, and untracked files, preserves
existing named branches and their commits, and returns to each manifest
revision (including pins), not unconditionally `main`. Ignored files are not
stashed; Git refuses to overwrite them during checkout or fast-forward. Stash
IDs and original branches appear in the action and workspace logs. Local-only
or unverifiable commits on a target branch or detached HEAD, ambiguous
manifests, incomplete repository coverage, and failed stashes block the
operation. The hold or mirror clears only after return, sync, and verification
succeed; a partial failure reports saved stashes for recovery.

The section badge counts are *attention* counts, not totals: **Overview** shows the number of
visible Live pulse cards whose tone is bad, **Tasks** shows active tasks, **Workspaces** shows occupied
slots, **Bugs** shows open bugs that
are stalled, untriaged, or unassigned, **PRs** shows features with at least one blocked PR,
**Machines** shows jobs running right now, and
**RAID** shows open rows at Red or Orange.

Each section keeps its own filters, sort and expandable detail rows, plus tabs where applicable.
All table-based views follow one portal standard: header click sorts by that
column (toggle asc/desc) and header-edge drag resizes column width. Width/sort
preferences are remembered per table in browser local storage. Selection/action
columns are not sortable.
**Tasks** is one unified table with no category tabs: use the multi-select **Category** filter
to narrow it, and **Columns** to choose visible fields without category-specific layout resets.
Task rows are lean single-line rows: the default-on columns are ID, Title, Category, Priority,
Status and Age; Est, Jira and Worked are off by default but remain available through **Columns**.
The title carries an expand caret (▸/▾, `aria-expanded`), and the expanded detail keeps the
essentials in front — description (or the derived feature line when none exists), metadata
including the hidden-column values (Jira link, Est, WS, Worked), and the PR list for review
tasks — while **Notes (n)** and **Edit task** fold into collapsed disclosures (the editor
disclosure only when served).
Review status and author filters remain available in this view; **Scan reviews** is available
when served and requires selected review tasks. Selecting a single category adds its specialised
summary cards. Done/cancelled tasks remain hidden by default; its **Open only** checkbox controls
that filter.
The Tasks table has row checkboxes plus **Select visible** and **Deselect**. Selection contains
only currently filtered rows and hidden rows are pruned when filters change. **Edit selected**
previews the affected tasks and can apply any combination of status, priority, category,
complexity or estimated hours through one revision-checked task-owner transaction. Category
changes cannot convert tasks to or from code-review; a terminal status change is rejected if a
selected task still holds a workspace. Workspace assignment, manual review status, titles and
notes remain per-task operations. **Scan selected reviews** probes unresolved PRs belonging only
to selected active code-review tasks; it is disabled when no selected row qualifies and never
scans unselected tasks. A completed scan clears the selection because review/task revisions may
have changed. Static exports remain read-only.
Status-bearing tables expose an independent **Open only** checkbox: outgoing PRs exclude merged
and closed PR rows, member tasks exclude done and cancelled assignments, phenomena exclude
closed/not-a-bug/not-ours/merged records, RAID excludes closed rows, and Machine job history shows
only running jobs. Bugs already have an **Open only** filter. Each view preserves its existing
default visibility; workspaces are an occupancy roster rather than a completed/closed record list
and do not get this lifecycle filter. The existing `#work` and `#task/<id>` links still work.
**Every filter dropdown is multi-select** (Tasks, PRs, Bugs, Phenomena, machine target): tick any
number of values; a row passes when it matches **any** ticked value of that dropdown (OR within a
field, AND across fields), and an empty dropdown means *All*. This is a **portal design
standard** — see *Filters are always multi-select* under the styling rules. In Machines,
**Refresh** is scoped only when exactly one target is ticked; with several it probes the whole
fleet. Switching
sections never loses the other section's filter state.

### Overview section (morning brief + live pulse)

The page opens here. It is **read-only by design**: every panel links to the section that
owns the underlying data. No panel calls Jira or GitHub. Long-range planning views (milestones,
burn-down, per-IP scoreboards) belong in the **Team** section's planning overview, not here.

| Panel | What it shows | Source |
|-------|---------------|--------|
| **Header** | Title plus an as-of line (last refresh time, live vs static) | client clock |
| **Morning brief** | Full-width result of the latest daily maintenance run (`routine.py`, scheduled in Settings). The caption shows run time, status (ok/degraded/failed) and journal id, with a **stale** warning when the brief is older than 26 h. Step pills (health, queue, integrity, reviews, PRs, bugs, workspaces, team, housekeeping, pulse snapshot, AI summary, self-test) carry their summary as a tooltip. When the routine's `ai` step produced one, an escaped **✦ AI summary** paragraph sits above the sections. Sections are ordered bad → warn → ok, and each item jumps to its task, PR feature, bug, member or section via `goItem` | `state/morning-brief.json` (written only by the routine) |
| **Freshness strip** | Sits between the brief and the pulse cards. One chip per source: bugs scan, PR scan, team activity scan, workspace check, machine activity, and the daily routine. Each chip shows *N ago*, and is marked **stale** (amber) when it is missing or older than 26 h. Clicking a chip opens that section | each section's payload |
| **Live pulse** | One card per section (below), computed in the browser by `assets/pulse.js` from the payloads the sections already serve. Each card has a headline number, a tone (ok/warn/bad, coloured top border and dot) and a short stat list. The header opens the section; stats with a tooltip explain their rule | all section APIs |
| **What changed since your last visit** | Full-width feed merging the dated trails the stores already keep (bug field-change log and newly mirrored bugs, pr-monitor feature notes, task notes) that are newer than a day watermark. Newest first, 10 shown with *+N more*. **✓ mark all seen** moves the watermark to today; **↺ last 7 days** resets it. Seen-state is browser-local | `bugs.db`, pr-monitor database, `tasks.db` |

Pulse cards (ids in `PortalPulse.CARD_IDS`):

| Card | Headline | Stats | Tone |
|------|----------|-------|------|
| **Tasks** | open tasks, excluding code reviews | in progress, blocked, overdue, due ≤ 7 days, WIP without a workspace | bad on overdue, warn on blocked |
| **Bugs** | open mirrored bugs | need attention, untriaged, new, open phenomena, escaped/regressed phenomena | bad on an alarm phenomenon or >25% needing attention, warn on any attention |
| **My PRs** | features in flight | CI failing, needs rebase, approved-not-landed, idle 7d+, not PLDM-tested, hygiene score | bad on CI failing, warn on the other rules |
| **Reviews on me** | reviews waiting on me | in queue, waiting on author, past 3-day turnaround | bad past turnaround, warn while any wait |
| **Workspaces** | slots in use / total | free, with dirty repos, free but off baseline, not checked 26h+ | warn on dirty or stray |
| **Machines** | jobs running now | passed 24h, failed 24h, stale, machines unreachable | warn on any failure, stale job or unreachable machine |
| **RAID** | open rows | red/orange, overdue, risks | bad on red/orange, warn on overdue |
| **Team** (full width) | members active in the last 7 days / members | PRs and reviews (7d), overdue tasks, onboarding overdue, on leave; plus one activity line per member | warn on overdue work or a quiet member |
| **Portal** | actions running or queued | daily routine status/next run | warn when the last run was degraded, bad when it failed |

The My PRs card carries the former **development hygiene** score as its note: seven pass/fail
rules over the delivery pipeline (CI green, rebased, approved PRs landed, PRs moving with no
7-day stall, features PLDM-tested, reviews answered within 3 days, WIP bound to a workspace).
Rules with no population are dropped, so an empty queue can't flatter the score.

**Deltas.** Each daily run calls `node assets/pulse.js --snapshot` (routine step `pulse`) and
stores the numbers in the brief as `pulse: {taken_at, values}`. Every headline and stat then shows
▲/▼ *N* against that snapshot: green when it moved the right way, red when it moved the wrong
way, muted when direction has no meaning. Without a snapshot (no node, or before the first run
after deployment) the deltas are simply absent.

**Member activity summary.** `PortalPulse.memberActivity` turns the Team payload into one
summary per member: PRs, reviews and Jira events in the last 7 days, open/overdue assigned
tasks, open items, onboarding progress, leave, and source gaps (a GitHub or Jira link that has
never been scanned). The same chips render on the Overview Team card, in the **Activity (7
days)** column of the Team overview table, and in each member's header. When the scan returned
its full 100-observation window, counts are shown as a floor (`100+`). A member on leave is
never marked quiet.

**Refresh.** While the Overview is visible it re-fetches all section payloads in parallel at the
interval chosen in Settings (*Overview live refresh*: off, 30 s, 1 min or 5 min; default 1 min).
Polling pauses while the tab is hidden and resumes on return. A failed source leaves its card
on the last good data.

**Customization (browser-local).** Settings → Browser lets you hide cards and reorder them. The
Overview nav badge counts the **visible cards whose tone is bad**.

The canvas keeps the 12-column tile grid (`.ov-canvas` + `.t*` spans) and per-tile collapse
state in browser-local storage. Endpoint: `GET /api/overview` returns only
`{brief, today, errors}`; every other number comes from the owning section's API.

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
  `pinged` flag at 2+, and a pinged bug counts as **Needs attention** (and so in the Overview Bugs card).
  Read it against idle days: *pinged + stalled* is a real problem; *pinged + moving* is a
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
- The Overview Bugs card counts open phenomena flagged escaped / regressed /
  unexplained-recurring as *Escaped / regressed*, which turns the card red.

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
  PR-first detail: a compact **Pull requests** heading/count and folded **Actions**
  menu above the PR table (repo/PR, wrapping title, **CI**, check counts,
  review-or-merge gate, status). The table has a keyboard-focusable horizontal
  scroll region on narrow screens. Feature-wide CI, add PR, PLDM test and stop
  tracking actions retain their original handlers; the menu closes on selection,
  outside click or Escape (which returns focus to its summary).
  A subdued short **summary** (marked *auto* when derived), **Jira** links and PLDM
  badge follow the table. Priority, overall status and merged/total remain in the
  feature header instead of being repeated. **Feature details** folds dates,
  PLDM details and the technical description away; **Activity history (N)** folds
  the full dated notes (newest first, scrolls). These disclosures retain their open
  state per feature across re-renders within the page session, alongside the metadata form under a
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
- Explicit CLI/Portal scans also run owner-managed Jira synchronization and retry archived
  features' pending Jira syncs. There is no polling daemon; rendering, navigation and
  payload refreshes do not scan or transition Jira. A scan is therefore not read-only:
  opted-in tickets may move to **In Review / Done** under the owner's lifecycle rules.
- Each active feature's Jira ticket has **Confirm full PR coverage** / **Revoke coverage**.
  The confirmation dialog identifies the whole current PR set and explicitly asserts that
  it fully implements that ticket. Approval is per ticket and exact PR set, not inferred
  from a Jira key, link, PR title or partial implementation. Changing the set invalidates
  coverage. Automatic transitions require assignment to the authenticated Jira user;
  approval does not override this guard. Revocation does not undo past transitions.
  Static exports show coverage and sync readings but no mutation controls.
- `POST /api/feature-jira-coverage` uses the PR mutation lock and delegates to
  `pm.confirm_jira(feature_id, key, confirmed=..., expected_revision=...)`. It requires
  an integer feature ID and revision (not booleans), string key and explicit boolean
  `confirmed`. The dialog sends the revision it displayed; stale edits remain owner-rejected.
  This endpoint records approval only; it does not scan or update Jira.
- Feature cards display owner-provided per-ticket `jira_sync` state, target and message,
  explicitly labelled **Last Jira sync result**. These historical readings may predate
  revocation and do not imply that automatic updates remain approved.
  Scan results retain all top-level `jira_sync` rows, including archived retries, and
  expose counts for updated, unchanged, blocked and error. Blocked/error Jira outcomes
  mark the scan job as error with a clear partial-success warning and feature/ticket
  failure details, while retaining refreshed PR results. They never appear as an
  entirely successful PR scan.
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

### Machines section

A read-only view over [`vm-delegate`](../../skills/vm-delegate/SKILL.md)'s delegation history, driven by
`vd.py`: an EC2 power strip (state dot, lease countdown, Start/Stop), VM and lab-machine target status,
the job running right now,
KPI counts, and the full history table.

The short **Machines** section name covers both VM and lab-machine targets. The
navigation badge continues to count confirmed running delegated jobs; the separate
**Check VM status** control remains an EC2-only `octl vm list` check.

- **Stop** on a confirmed-running row that still owns its slot (`build`/`test`/`run`)
  posts `POST /api/vm-cancel {id}`, which queues a `vm-cancel` action (claims
  `domain:delegation` + `vm:<target>`). It runs `vd.portal_cancel` → `mctl cancel`
  through the delegate mechanism: the remote process group gets TERM then KILL,
  the log gains `__MCTL_DONE__ rc=130` + `__MCTL_CANCELLED__`, the target is
  re-probed and the row shows `cancelled`. A newer run in the slot (pid mismatch)
  is refused. Containers the job started may outlive it.
- **Refresh status** (`POST /api/vm-refresh`) is a **background job**, polled through
  `/api/align/<id>` like the PR scan, and it streams one row per target into the align banner.
  It is not inline for a reason: one unreachable target costs a 90 s ssh timeout and `octl vm
  list` revalidates SSO, so the old inline version routinely outlived the browser's fetch — the
  section then never re-rendered and the whole tab looked broken while the probe was succeeding
  on the server.
- **Refresh one machine** with the `↻` on its EC2 card, or on its chip in the bare-metal target
  strip; the toolbar button follows the target filter. Same endpoint with
  `{"target": "<name>"}`. Refresh covers both VM and lab-machine targets. This is the difference
  between ~4 s and minutes: a
  fleet refresh waits out the ssh timeout of every dead lab desktop. Each card and chip shows
  its **own** age, because a scoped refresh must not make the untouched targets look fresh —
  and `octl vm list` is skipped entirely for a target with no instance behind it.
- **Check VM status** (`POST /api/vm-machines`) re-reads only `octl vm list`; **Sync logs**
  (`POST /api/vm-sync`) only rsyncs VM and lab-machine job logs, also scoped by the target filter.
  Both are narrower than a full refresh.
- The **Log** column (and the live-job cards) open the log in a **browser terminal**
  window that always runs `tail` **on the machine** (`vd.log_view(job_id)` builds it):
  `tail -F` for a running job, the last 20 000 lines (the terminal's scrollback) of a
  finished one, and for a run whose slot a later run reused, its timestamped
  `runs/<kind>-<stamp>-<pid>.log` archive on the machine. No pager, no download, and the
  local mirror is never shown; a run with no archive reports an error.
  The job id binds the log to its run. `POST /api/vm-log` / `/api/vm-tail` remain for
  compatibility but the page no longer uses them.
- **`>_ Terminal`** on a machine card (targets listed in Settings > Machines) opens
  `/term` in a pop-up window: xterm.js over a same-origin WebSocket (`/ws/term`) to a
  local pty running `mctl -t <target> term`, attached to the remote tmux session
  `portal` (`tmux new -A`), so closing the window only detaches and reopening resumes.
  `POST /api/term/open` validates the request and returns a one-time token (120 s TTL)
  naming a server-built spec; nothing sent over the socket can change what runs. At
  most 8 sessions; `GET /api/term/active` lists them. The socket checks Origin.
- **Live status link**: each live target (Settings > Machines) keeps one long-lived
  `mctl -t <target> jobs-watch <interval>` ssh session that streams a jobs probe every
  interval; each block is reconciled through `vd.refresh(preprobed=…)`. The machine card
  shows a `live` / `reconnecting` / `down` badge. The link never powers a VM on, backs
  off 5→120 s when dropped, and the manual refresh and watchdog remain the fallback.
  Reachability transitions (after the first known state) raise notifications.
- The header and the power strip print how **stale** each reading is (`refresh_age_h`,
  `machines_age_h`). Without that, a day-old `stopped` is indistinguishable from a live one.

Without the `vm-delegate` skill the section renders empty and the scans return 503.

### RAID section

A full editor over [`team-planner`](../../skills/team-planner/SKILL.md)'s `~/tasks/raid.db`, driven
by `rd.py`. The Overview RAID card is a read-only count; this is where rows are actually changed.

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

### AI assistant (Explain / Ask)

The top-bar **✦ AI** button opens a right-side drawer for the current section.
**Explain this view** and **Ask** queue an `ai` action (`POST /api/ai/run`,
`{kind, section, question?, focus?, parent?}`) that runs one headless Copilot CLI call
(`copilot -p … --output-format json`) with the section's own payload as context,
compacted to ~30 KB (lists keep their head and say what was dropped; the answer is told
when data is partial). Follow-ups resume the CLI session (`--resume`). Answers are
Markdown rendered server-side with raw HTML disabled.

- **Read-only profile**: shell and write tools are denied, the GitHub write server and
  WorkIQ are always denied, and Atlassian write tools are denied by name. Jira/GitHub
  *reads* are allowed only when Settings > AI allows them; otherwise all MCP servers are
  disabled. No custom instructions are loaded.
- **Budget**: every run (any kind) reserves against the daily cap before it is queued
  (429 when reached); premium requests are added from the CLI's result event. The drawer
  header and `GET /api/events` (`ai`) show `used/cap · premium · model`.
- **Privacy**: Team runs carry the private flag when started in private mode; such runs
  (and their history) are served only with `X-Team-Private: 1`. Other sections never
  receive Team data.
- Runs are stored as JSON under `state/ai/runs/` (last 300); `GET /api/ai/runs?section=`
  lists, `GET /api/ai/run/<id>` returns one (prompt and raw context stripped). The
  deep link `#ai/<id>` reopens a run; AI notifications link there.
- **⧉ Pop out** detaches the current thread into its own window (`/ai-chat?run=<id>`,
  plus `&private=1` for Team runs); follow-ups from the window continue the same thread.
- The **morning brief** gets an AI paragraph from the routine's `ai` step: the routine
  posts its own sections as context (`kind: brief`), waits for the job and stores the
  text in the brief. A cap/disabled answer is a step warning, never a failed run.

### AI CI triage (PRs section)

A feature whose PRs include `ci-fail` rows shows **✦ Triage CI (n)** in its PR
toolbar (served, AI enabled; never automatic). `POST /api/ai/ci-triage
{feature_id:int, force?:bool}` (same-origin) queues one `ai` action of kind
`ci-triage` that runs the **pr-failure-analyzer** skill on the feature's PRs (failing
checks, run URLs, siblings) plus recent sightings for those repos.

- **Profile `ci-triage`**: shell and write denied (the RTK hook blocks `gh` in
  unattended runs, and shell rules cannot narrow `gh run` to `view`); logs, jobs,
  diffs and checks come from the read-only `github-mcp-server` tools. Custom
  instructions load so the skill is reachable. Follow-up questions in the drawer
  keep this profile. Uses the shared daily cap.
- **Cache**: a run is keyed by the feature's sorted failing run URLs. While one is
  queued/running, or done for the same key, the POST returns it (`cached: true`);
  `force` (the **Re-triage** button) runs again. Requires Settings › AI to allow
  Jira/GitHub reads (400 otherwise); runs get at least 600 s. `GET /api/ai/ci-triages` maps
  feature id → latest `{run_id, status, verdict, summary, stale}`; `stale` means the
  failing runs changed since. The toolbar shows a `✦ <verdict>` badge that opens the
  run in the drawer (dashed when stale).
- **After a run**: the answer's final `PORTAL-TRIAGE: {json}` line (hidden from the
  displayed answer) gives the verdict. The portal records one sighting per item
  through `pfa.record(source="portal")` and adds the PR Monitor feature note
  `AI CI triage: <verdict> — <summary>` (label `ai-triage`) via `pm.add_note`.
  A missing line or a failed note/sighting is kept as `note_error` and never fails
  the run.

### AI review (draft / re-review / interactive)

An open code-review task with open PRs shows the AI review buttons **only in its
expanded detail** (Pull Requests heading; served portal, AI enabled):

- **✦ Create review draft** only while the task has no review draft, and **↻ Re-review**
  only once it has one;
- **🌐 Online review** (**🌐 Online re-review** once a draft exists);
- **✦ Interactive review**, which becomes **✦ Resume interactive review** while a chat
  for the task is open.

The collapsed row carries no buttons, only a non-clickable marker: **✦ AI review open**
while an interactive chat is open, else **✦ AI review running** while an `auto-review` or
`online-review` action for the task is queued/running (read from the action queue; rows
re-render when that set changes).

At most one AI review (queued draft run, online run or open chat) per task.

**Online review** (`POST /api/ai/auto-review {task_id, mode, online: true}`) needs no
workspace and so runs even while every review workspace is busy. It requires AI enabled
and Settings > AI "Jira/GitHub reads". At click time a PR whose repo name is RTL
(`rtl_pr_guard.is_rtl_repo`) is refused. The queued `online-review` action (resource
`online-review:<task>`):

1. classifies every open PR with `rtl_pr_guard.classify_pr_details` and refuses unless
   all are `software` (RTL and unknown alike), pinning each verified head SHA;
2. re-review only: when every PR head equals the latest task draft's reviewed head it
   finishes "draft unchanged" without running AI;
3. runs Copilot with the **review-online profile**: `/pr-review` remote mode, shell
   denied, GitHub MCP + Atlassian reads only, cwd = the drafts directory, which is the
   only writable place (`write(<drafts>/**)`; the cwd is implicitly writable, so it must
   be the drafts dir). `--add-dir` opens `~/.copilot/skills` and the private pr-review
   directory (memoir) read-only. The prompt reviews all PRs as **one feature set**:
   DEPENDS_ON graph and merge order, cross-PR API/consumer and Kconfig/DTS/CMake
   consistency, then each PR; one report `T-NNN-<slug>-<stamp>.md` with a Feature
   overview section first. Timeout `review_timeout_s`; daily cap applies;
4. the AI ends with `PORTAL-REVIEW: {"report", "siblings"}` (hidden from displayed
   answers). The **portal** registers the report via `tm.attach_review_draft` with the
   pinned heads — only a `T-NNN-*.md` file directly in the drafts directory that was
   created or rewritten during this run (snapshot comparison; falls back to the newest
   such file); a re-review answering `PORTAL-RESULT: no-change` registers nothing;
5. adds a task note naming the report; sibling PRs the AI found that are not on the task
   are **proposed** in that note and kept as `proposed_prs` on the run (task-manager has
   no add-PR-to-task API, so nothing is added); PRs whose head moved during the review are
   flagged (`moved_prs`) with a re-review recommendation. Nothing is ever posted.

**Draft runs** (`POST /api/ai/auto-review {task_id, mode: create|rereview}`) queue an
`auto-review` action (title "AI review draft"):

1. derives the required manifest at click time: **revision** = the open PRs' common base
   branch (`gh pr view`), **project** = the `<name>` of any `project.<name>.*` PR repo
   (none for project-neutral `ip.*` repos = any project). Mixed bases or projects are
   refused;
2. picks a workspace whose **deployed** manifest matches — the project and
   `manifest_revision` in its `.repo/metadata.json` (`wm.deployed_manifest`), not the
   tracked `rev` label: the review-purpose workspace the task already holds, else the
   first free local workspace with purpose `review` that matches, skipping occupied or
   mismatched ones. Nothing else is borrowed; when none fits the 400 lists why each
   review workspace was skipped. The deployed manifest is re-checked when the job
   runs, before occupying;
3. occupies the workspace for the task through the normal occupy/align action (unless
   already held) and waits for it;
4. runs Copilot in the workspace with the **review profile** — `/pr-review local
   draft-only` (create) or `/pr-review local re-review draft-only` (re-review): shell/write
   allowed, but `git push/reset/clean/stash`, `gh pr
   comment/review/merge/edit/close/create/ready/reopen`, `gh api`, `wip`, `rm`, `sudo`,
   the GitHub write server and Atlassian writes are denied. Custom instructions and
   skills load. Timeout from Settings (default 40 min);
5. requires a new/updated draft registered through `tm.py review-draft` and appends the
   task note "initial auto review draft done (…)" or "AI re-review draft done (…)". A
   re-review that finds no new commits answers `PORTAL-RESULT: no-change` and counts as
   done with the draft unchanged;
6. frees the workspace if this run occupied it (a failed release is reported, not hidden).

Draft runs post nothing to GitHub.

**Interactive review** (`POST /api/ai/review-chat {task_id}`) is a server-side
`/pr-review local` conversation in a review workspace, shown in a right-side panel and
detachable with **⧉** into `/ai-chat?chat=<id>`. The chat record lives in
`state/ai/chats/rc-*.json`; each message is one queued `review-chat` action that resumes
the same Copilot session.

- **open**: picks and checks the workspace exactly like a draft run, occupies it, then
  starts pr-review in walkthrough style (summary, then findings one at a time; the draft
  is kept up to date). A failed start ends the chat and frees the workspace.
- **turn** (`…/<id>/turn {message}`): one user message (≤ 8000 chars), review profile,
  nothing posted. One turn at a time; a busy chat refuses new messages.
- **Submit** is two steps. `…/<id>/preview` asks for exactly what would be posted,
  without posting it. The panel shows **Confirm & post** only while that preview is the
  latest turn. `…/<id>/submit {preview_run}` then posts that confirmed preview with the
  **review-submit profile**:
  - `gh pr review` and `gh pr comment` are allowed, as are the GitHub MCP review tools;
  - merge/edit/close, `gh api`, the GitHub MCP non-review write tools, pushes and
    destructive git stay denied.

  Submitting records a task note.
- **End** (`…/<id>/end {free}`): ends the chat and, by default, releases the workspace
  it occupied. The draft and the task note remain.
- **Idle**: a chat idle for 24 h is ended and its workspace released, keeping the draft.
- **Restart**: chats survive a portal restart. Chat jobs that were still queued resume with
  the action queue. A turn cut off mid-answer is marked failed ("send the message again"),
  and the chat stays open on its session. A start cut off mid-run becomes `interrupted`:
  the workspace stays with the task (an occupy that had already happened is recorded) and
  the panel offers **Retry start** (`…/<id>/retry`, which re-runs open without
  re-occupying) or **End review**. Nothing is replayed automatically, and the 24 h idle
  release still applies.
- **Budget**: each message, preview, submit or start retry reserves against the separate interactive
  daily cap, not the general one.
- `GET /api/ai/review-chats?task=&open=1` lists chats; `GET /api/ai/review-chat/<id>`
  returns the chat with its turns, busy state and usage. Deep link `#review-chat/<id>`.
  A finished chat message is silent; start, submit, end and errors notify.

### Notifications

`GET /api/events?since=<seq>` (polled by every open tab, also a presence heartbeat)
returns new events plus the live-link and AI usage state. Kinds: action-queue results,
delegated machine-job completions, AI answers and machine reachability changes, each
switchable in Settings > Notifications. A tab shows them as browser notifications
(in-page cards as fallback) and clicking one follows its deep link. When no tab has
polled for 90 s the server raises a Windows toast through `powershell.exe` instead.

### Jump palette, deep links and keyboard

- **Ctrl/⌘+K** (or **⌕ Jump** in the top bar) opens a palette over every store: sections,
  tasks, bugs, phenomena, PR features and individual `repo#N` PRs, RAID rows, explorer views.
  Exact tag hits rank first. Unknown input still goes somewhere: a GitHub PR URL or unknown
  `repo#N` → explorer PR view, a run URL → explorer run analysis, an unknown Jira key → Jira.
- **The URL hash names the view** — `#prs`, `#bug/KEY`, `#ph/PH-004`, `#task/T-101`,
  `#pr/17`, `#raid/I-007` — so any view is bookmarkable, and it is honoured on load and on
  `hashchange`. Updated with `replaceState`, so it does not flood history.
- **Keyboard** (ignored while typing): `j`/`k` move a row cursor, `Enter`/`e` expand,
  `x` ticks (Bugs, PRs), `/` focuses the section search, `g` then `o w s b p v r` switches
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


### Review draft documents

A review task has **one report per review pass**, not one file per PR. Below the
PR list, an expanded review task shows a single **Open review draft** button for
the newest report, with its saved timestamp and every PR it covers with that PR's
full reviewed head SHA (`repo#N <sha>`). Earlier passes, including legacy per-PR
drafts (labelled *Per-PR draft*) and reports combined from them (labelled
*Combined from per-PR drafts*), are under **Earlier drafts**. Tasks without an
attachment show no button. Drafts are registered through task-manager's
`review-draft` CLI (one `--head PR_URL=SHA` per covered PR), never inferred from
filenames or free-text notes. Review workflows must register the report before
reporting success, and Auto-review counts task-level reports. Manual workspace
assignment still runs no AI review.

The served portal opens `/review-drafts/<task-id>/<attachment-id>` in a new tab.
The viewer renders GitHub-flavored Markdown (including tables, fenced code,
strikethrough, task lists and automatic links) in the portal's dark palette,
labels the draft as local/unsubmitted, and links back to the task. Raw HTML is
escaped and unsafe link schemes are not made clickable. Content is loaded on
demand through `tm.read_review_draft`, restricted to registered private Markdown
documents; missing/unreadable files report errors. No arbitrary path-serving
endpoint, browser cache or external rendering service is used.
The static export shows metadata and a served-portal hint rather than a broken
relative link. It does not embed private draft contents.

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
- The shared operation banner has a keyboard-accessible **Collapse / Expand**
  button beside Dismiss. Collapsing hides only the details, keeping the live
  title/spinner visible; polling and completion/error updates continue. The
  choice survives updates and new operations within the current page, but a
  reload starts expanded. Dismiss remains separate and does not cancel a job.
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
