# Portal operations

## Entry points

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

## Validation

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
