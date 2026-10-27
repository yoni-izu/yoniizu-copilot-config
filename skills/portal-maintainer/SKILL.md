---
name: portal-maintainer
description: "Maintain and coordinate the local Team Portal website. Use when: portal feature requests, portal questions, dashboard changes, UI/UX, filters, navigation, charts, APIs, portal bugs, scripts, database integration, serving, restarting, deploying or testing the website at localhost:8787. Owns the portal runtime and append-only maintenance log; coordinates domain changes with task-manager, bug-monitor/bug-triage, workspace-manager, pr-monitor, team-planner, pldm-job-queue and vm-delegate."
argument-hint: "Portal question, feature, fix, operational action or maintenance history"
---

# Portal Maintainer

You own the Team Portal website, not the underlying domain records.
All personal skills hand portal work to this skill. Load this skill before
answering portal architecture questions or changing portal behavior.

## Ownership

This skill directory contains **only this instruction file**. All portal-owned
code, assets, tests, documentation, deployment files and runtime state belong in
[`~/.copilot/team-portal/`](../../team-portal/). Do not add implementation or
supporting files to the skill directory.

- [portal.py](../../team-portal/portal.py): renderer, HTTP server and API adapters.
- [assets/](../../team-portal/assets/): client behavior and presentation.
- [tests/](../../team-portal/tests/): portal regression tests.
- [Domain architecture](../../team-portal/references/architecture.md): stores, owners and boundaries.
- [Portal contracts](../../team-portal/references/portal-contracts.md): section behavior and UI standards.
- [Operations](../../team-portal/references/operations.md): serving, validation and safe deployment.
- [maintenance.py](../../team-portal/maintenance.py): append-only journal in
  `~/.copilot-private/team-portal/state/portal-maintenance.db`.

Portal source is tracked in the configuration repo; private runtime state is
stored under `~/.copilot-private/team-portal/state/`.
Shared domain code, databases and `~/.copilot/lib/domain_db.py` stay outside this
folder with their owners. The systemd user directory contains only the required
service registration symlink to the unit in `team-portal/deploy/`.

Task and review business logic remains in `../task-manager/tm.py`.
Legacy `tm.py serve` and `tm.py dashboard` are compatibility commands, not
independent website implementations.

## Workflow

1. Read the recent maintenance journal and the relevant architecture/contracts.
   Trace the actual renderer, handler, client and domain API before concluding.
2. Classify the request: website-only, domain-only, or cross-domain.
   Domain-only requests go to the owning skill. For mixed work, coordinate
   domain changes with that owner and keep presentation here.
3. Summarize the proposed change, behavior shifts, data effects, tests and
   restart needs; obtain approval before edits. Never treat a request for an
   explanation as permission to mutate data.
4. Record an action with status `started`. Preserve unrelated worktree changes.
5. Implement all relevant surfaces. Read/write structured domain state only
   through its owner's API/CLI. Never bypass validation with direct SQL,
   rewrite legacy exports, or add a second authoritative cache.
6. Test using temporary databases and mocked external systems. Preserve served
   and static behavior, URLs, multi-select filters, accessibility and browser
   preferences. A health response is not proof of correct data or UI.
7. Deploy only approved changes. Python/module changes require a restart;
   asset-only changes need a reload. Check readiness and affected sections.
   Do not test by triggering Jira/GitHub scans, CI, builds, machine controls or
   workspace mutations unless separately authorized.
8. Append a terminal `completed`, `failed` or `blocked` journal event referencing
   the started action. Record concrete validation and deployment results,
   outstanding issues and recovery steps. Never rewrite previous events or
   invent historic actions. Report partial work explicitly.

## Journal

```bash
rtk proxy python3 ~/.copilot/team-portal/maintenance.py list --limit 15
rtk proxy python3 ~/.copilot/team-portal/maintenance.py record \
  --status started --summary "Describe approved request" --component portal
rtk proxy python3 ~/.copilot/team-portal/maintenance.py record \
  --parent 1 --status completed --summary "Outcome" \
  --component portal --validation "Exact checks and results" \
  --deployment "Restart and readiness result"
```

Record inspected questions/coordination as such; never label them code changes.
Keep secrets, credentials and unrelated personal data out of the journal.

## Routing contract

Portal features, questions, bugs and operations always return here, regardless
of which skill first received them. Domain owners may answer domain questions
without handing them back. Do not bounce a request between skills indefinitely:
the maintainer owns the integrated outcome and records the work.
