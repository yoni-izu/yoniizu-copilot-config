# Portal and domain ownership

The portal is a client of domain APIs. It must not open domain databases,
allocate domain IDs, parse old JSON/Markdown stores, or reimplement domain rules.
SQLite lives on the controller's local filesystem; do not put WAL databases on
NFS or share them between machines without a supported database service.

| Domain | Owner/API | Canonical database |
|---|---|---|
| Tasks and incoming reviews | `task-manager/tm.py`, `task_store.py` | `~/tasks/tasks.db` |
| Team roster, focus, assignments, sessions and activity observations | `team-manager/team_store.py`, `team_portal.py`, `team_activity.py` | `~/tasks/team.db` |
| Bug monitoring and phenomena | `bug-monitor/bm.py`, `bugdb.py`, `bug-triage/ph.py` | `~/tasks/bugs.db` |
| Workspace roster/occupancy | `workspace-manager/wm.py` | `~/tasks/workspaces.db` |
| Outgoing features/PR observations | `pr-monitor/pm.py` | `~/tasks/prs.db` |
| CI-failure sightings and pitfalls (pr-failure-analyzer) | `pr-failure-analyzer/pfa.py` | `~/tasks/pr_failures.db` |
| RAID | `team-planner/rd.py` | `~/tasks/raid.db` |
| Local PLDM tracking (not surfaced in the portal; the job queue lives in the company PLDM portal) | `pldm-job-queue/pjq.py` | `~/tasks/pldm.db` |
| Delegation observations/history | `vm-delegate/vd.py` | `~/tasks/delegation.db` |
| Shared job record format (all runners), `job` CLI | `lib/jobs.py`, `bin/job` (spec `lib/jobs.md`) | `~/.jobs/<id>/` |
| machine-pilot local jobs (runner `pilot` + ad-hoc) and bmcjob cache (Machines view, read + stop) | `machine-pilot/scripts/pilot_jobs.py` | `~/.jobs/<id>/`, `~/.pilot/bmcjobs-cache/<target>.json` |
| Website maintenance actions | `../team-portal/maintenance.py` | `~/.copilot-private/team-portal/state/portal-maintenance.db` |
| Portal action scheduling | `../team-portal/action_queue.py`, `portal.py` | `~/.copilot-private/team-portal/state/action-queue.db` |
| Daily maintenance brief (derived) | `../team-portal/routine.py` | `~/.copilot-private/team-portal/state/morning-brief.json` |
| Portal settings (routine schedule/behaviour) | `../team-portal/portal_settings.py` via `/api/settings` | `~/.copilot-private/team-portal/state/portal-settings.json` |
| AI runs and daily usage (derived answers, not domain data) | `../team-portal/ai.py`, `portal.py` | `~/.copilot-private/team-portal/state/ai/` (`runs/*.json`, `chats/rc-*.json`, `usage.json`) |

Paths in the API column are relative to `~/.copilot/skills/`.
Use the owning skill's supported CLI/API; never directly mutate SQLite.
`~/.copilot/lib/domain_db.py` supplies common connection, backup and audit
mechanics, not business rules.

All portal source lives under `~/.copilot/team-portal/`: implementation, assets,
tests, these references and the canonical `deploy/` units (`task-dashboard.service`,
`portal-maintenance.service`/`.timer`).
Private runtime state lives under `~/.copilot-private/team-portal/state/`
(journal, PID, static export, migration report, morning brief and recovery
snapshots, including the rotating `backups/daily/`). The skill folder holds only `SKILL.md`. The systemd
user folder has registration symlinks, not other copies of the units. The one
generated file there is `portal-maintenance.timer.d/schedule.conf`, which
`portal_settings.py` owns and rewrites from the Settings schedule. Never edit it
by hand.
Domain-owned code/data and the shared SQLite helper are intentionally external.
`power_schedule.py` owns deadline/retry bookkeeping in private
`state/power-schedule.json`, not machine observations. It reads cached deadlines
through vm-delegate and performs due EC2-only observations through
`vd.portal_machines()`, under the existing VM operation lock. Attempt state is
saved before dispatch so interrupted checks cannot be endlessly replayed across
restarts. There is one scheduler in the managed portal, independent of tabs;
all actual power records remain authoritative in vm-delegate/mctl.
The Overview owns no data. `assets/pulse.js` is a pure module (no DOM, no
fetches) that turns the payloads other sections already serve into the Live
pulse cards, hygiene rules and per-member activity summaries. The page,
`team.js` and the routine all run that one module: the routine's `pulse` step
pipes owner-API payloads into `node assets/pulse.js --snapshot` and stores the
numbers in the morning brief so the page can show deltas. `GET /api/overview`
returns only the brief.
The one narrow exception to "never open domain databases" is the daily
routine's `PRAGMA integrity_check`, which opens `~/tasks/*.db` with
`mode=ro` and never writes; every domain refresh still goes through owner APIs.

## AI, terminal, live link and events

- **AI** (`ai.py`) runs the Copilot CLI headless; the portal is only its scheduler and
  context builder. Context comes from the same payloads the section renders (owner APIs),
  never from direct database reads. AI output is advisory and stored as portal-owned
  derived records; it changes no domain data. The AI paths that write are the review
  ones (draft, re-review, interactive), and they do so only through owners: workspace
  occupy/free via the workspace actions, draft registration via `tm.py review-draft`, and
  task notes via `tm`'s transaction. Posting to GitHub happens only in an interactive
  review's submit run, after the user confirmed its preview, under a dedicated profile.
- **Terminal** (`terminal.py`, `assets/term.js`) is an in-process WebSocket↔pty bridge.
  Workspace terminals resolve owner IDs to local roots or registered remote targets;
  remote access uses `mctl -t <target> term` without auto-start. Separate tmux
  sessions retain state on their respective machines across portal restarts.
- **Live link** (`live_status.py`) is one `mctl jobs-watch` ssh session per target;
  readings still land in `delegation.db` only through `vd.refresh(preprobed=…)`.
- **Domain change watcher** (`domain_watch.py`) checks only stat metadata for the
  tracked `~/tasks/*.db` files and their SQLite WAL files every scan interval
  (Settings > Automatic updates, default two seconds; reconfigured live). It
  never opens owner databases, and coalesces changes before publishing one
  domain event. Domain events are silent and cause only the affected visible
  section to re-read its owner API; other sections refresh when opened.
- **Events** (`events.py`) is an in-memory bus (replay window from Settings, default 200) for notifications and
  domain-change signals; it is not a record and is lost on restart by design.
  `/api/stream` delivers events over SSE, replays within the retained window,
  and asks tabs to resync after a restart or replay gap. `/api/events` remains
  available for compatibility and as a browser fallback.

## Authority

Jira fields, GitHub checks/reviews, PLDM queue execution and remote machine
readings retain their external authority. Local stores retain source IDs and
observation timestamps. A failed refresh does not make an old reading fresh.
`~/.mctl/joblog.jsonl` is an externally produced ingestion source, not a portal
file to rewrite. Remote workspace identity markers and raw machine logs remain
owned by the machine tooling.

JSON and Markdown legacy stores are import/backup/export formats after cutover,
not competing writable sources. Owner APIs preserve narrative Markdown and
unknown legacy fields. Investigation files, meeting archives, planning
guidelines, snapshots and specifications remain documents where appropriate.

## Consistency and recovery

Each owner uses transactions and a versioned schema. Preserve stable task,
workspace, feature, bug and job IDs. Audit mutations transactionally; report
stale/conflicting edits explicitly instead of overwriting intervening changes.
Across databases, use stable typed references, not cross-file foreign keys.

Incoming review PR entries may reference private Markdown draft documents using
task-owner attachment IDs and metadata. `tm.py review-draft` registers them;
`tm.read_review_draft` is the portal's only document resolver. Draft bodies remain
under the private `skills/pr-review/drafts/` directory, not in portal storage or
tracked skills. The portal serves only registered attachments by task/opaque ID.

Physical workspace/job operations cannot be rolled back by a SQL transaction.
Record outcomes, preserve holds on failure, and make interrupted actions
recoverable. Portal locks coordinate HTTP jobs; they do not replace owner
storage concurrency control for independent CLI processes.

The portal's action queue owns execution order and job readings, not domain
records. A resource-aware scheduler runs at most three descriptors concurrently
through existing owner APIs. Conflicting actions retain submission order;
independent actions may bypass a blocked action. Canonical workspace IDs,
remote machine targets and shared owner domains provide exclusive claims.
Batch actions claim their complete resource set atomically. Review scans and
task reassignment claim all workspaces because they can release other holds;
automatic PLDM workspace selection and unknown action kinds run exclusively.
Mirror operations also claim their source workspace. Claims are resolved from
owner APIs at dispatch, not accepted from clients or persisted as authority.
Submission is durable before acknowledgement. Pending descriptors resume
after a restart; an interrupted running action is marked failed, never replayed
automatically, because its external side effects may already have occurred.
Domain locks and checkout guards are acquired/rechecked at execution, not while
waiting. Claims remain held until results are persisted, including failures.
Inline mutations (including Team member operations) remain outside this queue
and retain owner validation and locks. Remote workspace jobs additionally take
the existing VM lock to exclude inline machine controls and watchdog probes.
These portal claims do not coordinate independent external CLI operations.

Workspace dedication is owned by `workspace-manager/wm.py`, persisted on roster
records in `workspaces.db`. General is the backward-compatible default; Review,
Development, Build root and Mirror carry owner-defined exclusive-use policies.
The portal renders owner-provided permissions and uses the same assignment APIs;
it does not maintain a second classification cache. Purpose changes in the
portal take a scheduler resource guard so queued/running work cannot race the
metadata write. Workspace operation locks and delegation observations prevent
repurposing while managed dispatches or unverified jobs reserve the checkout.
The owner policy also guards supported `mctl` operations, resolving remote
physical aliases and requiring status reconciliation before dispatch when a
tracked path differs. Independent arbitrary terminal commands remain outside
this application-level enforcement.

Imports must be backed up, repeat-safe and verified against their source.
Common import backups live under `~/tasks/migration-backups/<database>/`.
Do not delete legacy sources or backups after cutover. Restore them only
through an explicit coordinated rollback; blindly reverting code after new DB
writes would discard subsequent work. Use SQLite's backup API for a live DB,
not a copy of only its main file while WAL is active.

## Routing

All personal skills route website feature requests, questions, errors and
operations to `portal-maintainer`. Domain-only work stays with its owner.
For mixed requests the maintainer coordinates owner changes, verifies the
integrated result, and journals what actually happened.
