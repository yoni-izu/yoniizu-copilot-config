# Portal operations

## Entry points

Install the server-control command once (do not overwrite an existing command):

```bash
chmod +x ~/.copilot/team-portal/portalctl
ln -s ~/.copilot/team-portal/portalctl ~/bin/portal
portal restart
portal status
portal logs
portal stop
portal start
```

`portal` controls the existing `task-dashboard.service`; it never launches a
second unmanaged server. Start/restart wait for an active service and an exact
`ok` health response, returning nonzero on failure. Status checks both service
state and HTTP health. Logs shows the last 50 journal entries without a pager.
The readiness loop makes up to 30 attempts with a two-second HTTP timeout
and a one-second interval. The command targets the unit's port 8787.

If the user manager is unavailable, the command prints recovery instructions.
Inspect `systemctl status user@$(id -u).service --no-pager`; if it has failed,
run `sudo systemctl start user@$(id -u).service`, then `portal restart`.
Restoring the user manager may start other enabled user services; it is an
explicit operator action, never an automatic privileged fallback.

Low-level entry points (do not start directly while the managed service runs):

```bash
rtk proxy python3 ~/.copilot/team-portal/portal.py serve --status
rtk proxy python3 ~/.copilot/team-portal/portal.py serve --port 8787
rtk proxy python3 ~/.copilot/team-portal/portal.py dashboard
```

The static export is read-only and self-contained. The editable site is
`http://127.0.0.1:8787`. Keep binding localhost. Legacy `task-manager/tm.py`
`serve`/`dashboard` delegate here. Preserve existing API URLs and hash deep links.
The PID file is `~/.copilot-private/team-portal/state/.dashboard.pid`;
the default static export is `~/.copilot-private/team-portal/state/dashboard.html`.

The managed service remains `task-dashboard.service`, using
`~/.config/systemd/user/task-dashboard.service`, a symlink to the canonical
`~/.copilot/team-portal/deploy/task-dashboard.service`. Do not start a second
server beside it. After editing a unit, run `systemctl --user daemon-reload`.

All portal implementation and supporting files belong in `team-portal/`, not
in the skill directory. Only `skills/portal-maintainer/SKILL.md` stays there.
Private journal data, generated exports, PID, migration report and backups live
in `~/.copilot-private/team-portal/state/`; never commit them. Shared domain databases,
owner modules, machine logs and `lib/domain_db.py` remain with their owners.
Systemd's service registration and journal are managed by systemd.

Python/owner-module edits require `systemctl --user restart task-dashboard`.
CSS/JS are loaded from disk with cache-busting; reload the page for asset-only
changes. Verify readiness and check logs if startup fails. Do not mask failures
with an empty dataset or claim a successful deploy from an accepted job alone.
Portal Python dependencies are listed in `team-portal/requirements.txt`; install
them into the portal's private runtime directory with
`python3 -m pip install --target ~/.copilot-private/team-portal/python -r ~/.copilot/team-portal/requirements.txt`.

## Action queue recovery

User-initiated progress jobs are durably scheduled in
`~/.copilot-private/team-portal/state/action-queue.db`. The managed portal is
the only queue worker; do not run a second portal process against that store.
The **Actions** button and read-only `GET /api/actions` show pending/running
work and the latest 20 results. The database retains older results.

Before deploying, check that no action is running. Stop/restart retains queued
work, which resumes FIFO. A running action interrupted by restart is marked
failed on startup and is never automatically retried. Inspect the affected
owner's status/history and external system before resubmitting; a failed action
may already have performed side effects. Do not repair a queue by editing SQL
or deleting its database. Queue infrastructure/storage failures are logged and
reported in the API/UI, and stop accepting new submissions until repaired.
Tests use temporary queue databases and mock all owner/external operations:

```bash
rtk proxy python3 -m unittest discover \
  -s ~/.copilot/team-portal/tests -p 'test_action_queue.py'
```

An `approval_required` workspace return is a completed preflight, not a
successful free. Reopen it from **Actions** to review and confirm; it does not
occupy the worker while awaiting a decision. A queued approved return rechecks
the workspace snapshot and asks again if a non-clean checkout changed.

The same approval flow supports **Force sync** for free, independent
workspaces whose Sync is blocked by checkout issues. It never overrides
occupancy or mirror guards, and it does not release a hold or mirror.

An approved return saves staged, unstaged, and untracked changes in named Git
stashes. The action result and workspace log list each repository, original
branch, and stash commit ID, including on partial failures. After returning to
the original branch, recover a particular stash deliberately with
`rtk proxy git -C <repo> stash apply --index <stash-id>`; inspect
`rtk proxy git -C <repo> stash list` if an interrupted operation did not
finish recording its result. Do not automatically pop or discard the stash.

## Validation

Control-command tests use mocked systemd/HTTP commands and never touch live services:

```bash
bash ~/.copilot/team-portal/tests/test_portalctl.sh
```

Use the smallest relevant tests, with temporary paths/mocked external calls:

```bash
rtk proxy python3 -m unittest discover \
  -s ~/.copilot/team-portal/tests -p 'test_*.py'
rtk proxy node --test ~/.copilot/team-portal/tests/test_portal_tasks.js
rtk proxy node --check ~/.copilot/team-portal/assets/portal.js
```

Run affected owner tests as well. Validate served/static rendering, payload
shapes, empty/error states and lost-update behavior. Never run tests against
personal databases. Syntax checks are not substitutes for behavioral tests.

After deployment, read `/health` and the affected read-only API endpoints,
then check the actual page with browser tools. Verify counts and representative
fields against owner APIs without exposing personal content in chat.
Do not click refresh/scan/action buttons during a read-only smoke test.

## Remote workspace SSH authentication

Remote workspace operations resolve a key-bearing SSH agent through
`vm-delegate/vd.py` before each `mctl workspace` invocation, including the
progress-streaming path. They revalidate rather than reuse the VM-jobs probe
cache, so an empty or replaced service-inherited agent can be bypassed without
restarting the portal or importing a login-specific socket into systemd.
Local workspace operations retain their inherited environment.

This does not load keys or provision credentials. If no agent holds identities,
SSH may still use its configured identity files; a failed SSH connection includes
the missing-agent diagnostic and retains the previous successful workspace
reading. Load the authorized key into an agent and retry. A loaded identity is
not proof that the target accepts it, and failed operations are never
automatically replayed.

## Migration/cutover

1. Verify the local filesystem supports SQLite locking and WAL.
2. Back up code, unit, legacy stores and existing databases. Record hashes,
   counts and schema versions without copying personal content into Git.
3. Stop the portal before migrating live stores. Do not run competing owner
   writers during the cutover.
4. Import through each domain owner. A corrupt input, identity conflict, lost
   narrative, changed payload or orphaned hold blocks cutover.
5. Check repeat-import safety, integrity, IDs and content parity; preserve
   original sources and backup manifests.
6. Update the service entry point, reload/start it and verify readiness,
   endpoints and browser behavior. Journal the actual results and remaining
   caveats.
7. On failure, stop and diagnose. Restore only a verified pre-cutover snapshot,
   or repair forward if new writes have occurred. Never silently reset stores.

Raw evidence and human documents stay files; generated JSON/Markdown exports
are not authoritative edit targets. See the owner CLI's `--help` for supported
export/import commands.

The initial import coordinator is `team-portal/migrate.py`. Rehearse with
`--database-dir <empty-local-directory> --manifest <temporary-report-path>`,
then run with `--database-dir ~/tasks`
only after stopping the service and excluding other writers. It verifies full
task/workspace fields, exact RAID/PR document bytes, PLDM/delegation observations,
repeat import/ingestion, referential holds, source hashes and database integrity.
It writes a private migration manifest to
`~/.copilot-private/team-portal/state/migration-manifest.json`
by default. Rehearsals/tests must pass `--manifest` to avoid touching the live
report. This is an initial-cutover verifier, not
a command to synchronize obsolete legacy files over later database changes.

RAID and PR stores retain lossless Markdown documents inside SQLite with
revision-checked transactions; owner APIs expose structured records. This
preserves original narratives/unknown fields without destructive conversion to
display-only columns. They are canonical databases, not live Markdown mirrors.
