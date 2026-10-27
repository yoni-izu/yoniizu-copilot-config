# Team Manager workflows (database-first)

## 1) 1:1 workflow

1. Resolve member by stable key.
2. Read prior finalized session(s), active assignments, and running entries from DB.
3. Build prep draft (private + shared projection).
4. During live capture, append tagged entries (notes, decisions, feedback, actions, roadblocks).
5. Finalize into:
   - private archive record
   - shared summary projection
6. Optional: export approved shared summary to Confluence.

## 2) Assignment workflow

Use assignments for managerial intent, not as a Jira clone.

- Create: title, owner, outcome, context, optional target date.
- Track: append progress notes and blockers.
- Link: related Jira/task/bug/RAID artifacts via typed references.
- Complete: only when outcome evidence is recorded.

Assignments can represent:
- broad direction (multi-ticket, cross-team)
- concrete short task (including one-Jira-task tracking)

### Portal single-field and batch edits

Inline task/log editing uses the ordinary revision-checked owner mutations.
The private `portal_mutate("bulk-set", data)` facade accepts:
`entity_type` (`assignment` or `meeting`), `member_key`,
`items` (`[{id, revision}, ...]`), `field` and `value`.
All 1–500 unique items must belong to that member and match current revisions.
Validation and writes use one transaction; a conflict cancels every update.

Assignment fields: category, priority, status, target_date (ISO date or null).
Meeting fields: session_type, meeting_date (required ISO date), title_visibility.
Meeting snapshots are retained before/after edits; finalized sessions become
amended. Notes, visibility of individual entries, titles, refs and other fields
are never overwritten by a batch. Activity observations remain externally owned.

## 3) Metrics workflow

Compute separately:
- features delivered
- bugs closed
- attributes completed
- stories completed

Always attach:
- scope bounds
- freshness timestamp
- coverage statement

Never silently coerce missing coverage to zero.

## 4) Leave workflow

- Post-cutover, leave records start empty.
- Backfill from HR records only.
- Migration must not seed structured leave from legacy notes/tables.

```bash
team.py leave-import hr.csv                     # preview: new/updated/errors/overlaps
team.py leave-import hr.csv --apply             # write (all-or-nothing on row errors)
team.py leave-stats --member <key> [--as-of YYYY-MM-DD]
```

CSV contract (headers case-insensitive; aliases accepted):
- required: `member` (member key, display name, Jira account ID or GitHub handle;
  else `--member-map '{"<csv value>": "<key>"}'`), `start_date`, `end_date`,
  `status` (approved/cancelled/pending/rejected; or `--assume-status`).
- optional: `leave_type` (default vacation), `amount_days`, `requested_at`,
  `approved_at`, `external_ref` (HR request ID — stable identity), `notes`.
- Dates must be ISO unless `--date-format dmy|mdy|dmy-dot` is given (no guessing).

Safety: re-importing the same file is a no-op; rejected rows are skipped; a later
`cancelled` row updates the same record; overlaps are warned; nothing is deleted;
the CSV is backed up to `~/tasks/migration-backups/team/`.
`days_taken_this_year` counts Vacation only; `sick_leave_days_this_year` separately
counts Sick (including input aliases `Sick leave`, `sick_leave`, `sick-leave`).
Both are private, derived employee fields backed by `leave_records`, not
independently editable counters. Other leave types do not enter these totals.

Working-day counts use the employee's `base_country`: Israel excludes Friday
and Saturday; all other known countries exclude Saturday and Sunday. Unknown
country gives unknown totals. HR amounts are capped at the period's working-day
count; missing amounts use that count. Partial-year/as-of periods prorate the HR
amount by working days. Inferred/capped/prorated amounts set the corresponding
`days_estimated` or `sick_leave_days_estimated` flag. Public holidays are not
inferred. Cancelled and pending records do not count.

User-supplied HR calendar snapshots may be transcribed to CSV after approval
status is confirmed. Keep source snapshot/month references in `notes`, use a
stable `external_ref`, and merge continuous cross-month periods. Import only
requested Vacation/Sick entries, ignoring holiday icons, Non Working Day,
Privilege Day and Other. Missing months/types/members remain unknown; totals
describe recorded evidence only, not certified full-year coverage.

## 4b) Evidence backfill workflow

```bash
team.py backfill                       # preview per member + unmapped PR authors
team.py backfill --apply [--confirm-merged]
```

- Re-runs are idempotent; bugs reopened/reassigned and stale *candidate* features are retracted.
- Confirmed/accepted features are never downgraded.
- Add a member: `team.py member-add <key> --data '{"display_name": "...", "onboarding_date": "YYYY-MM-DD", "employment_type": "full-time", "main_ip_ownership": "..."}'`.
- Map missing identities first: `team.py show <key>` → `team.py member-set <key> --revision <rev> --data '{"github_handle": "..."}'`.

## 5) Cross-domain link workflow

Each link stores:
- source entity/type
- target `domain/type/id`
- relationship label
- provenance (who/when/source)
- optional check status

Target availability is resolved through owner APIs, not cross-db foreign keys.
