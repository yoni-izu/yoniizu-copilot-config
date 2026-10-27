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
| RAID | `team-planner/rd.py` | `~/tasks/raid.db` |
| Local PLDM tracking | `pldm-job-queue/pjq.py` | `~/tasks/pldm.db` |
| Delegation observations/history | `vm-delegate/vd.py` | `~/tasks/delegation.db` |
| Website maintenance actions | `../team-portal/maintenance.py` | `~/.copilot-private/team-portal/state/portal-maintenance.db` |

Paths in the API column are relative to `~/.copilot/skills/`.
Use the owning skill's supported CLI/API; never directly mutate SQLite.
`~/.copilot/lib/domain_db.py` supplies common connection, backup and audit
mechanics, not business rules.

All portal source lives under `~/.copilot/team-portal/`: implementation, assets,
tests, these references and the canonical `deploy/task-dashboard.service` unit.
Private runtime state lives under `~/.copilot-private/team-portal/state/`
(journal, PID, static export, migration report and recovery snapshots). The skill folder holds only `SKILL.md`. The systemd
user folder has a registration symlink, not another copy of the unit.
Domain-owned code/data and the shared SQLite helper are intentionally external.

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

Physical workspace/job operations cannot be rolled back by a SQL transaction.
Record outcomes, preserve holds on failure, and make interrupted actions
recoverable. Portal locks coordinate HTTP jobs; they do not replace owner
storage concurrency control for independent CLI processes.

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
