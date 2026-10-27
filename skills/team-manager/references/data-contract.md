# Team manager data contract

Canonical store: `~/tasks/team.db` (`TEAM_DB` override), owned by `team-manager`.

## Entity families

- `members`: stable roster keys and profile identifiers.
- `meetings` + `meeting_entries`: historical and working 1:1 records, including full narrative source.
- `assignments` + `assignment_updates`: managerial directions and progress updates.
- Member `focus` is current work, not an assignment and has no lifecycle status.
  `focus_visibility` defaults to shared; private focus is omitted from shared payloads.
  Assignments appear separately in the member Tasks view.
- `feedback_items`: delivered feedback plus peer/stakeholder feedback (private by default).
- `roadblocks`: engineer-raised blockers and lifecycle state.
- `leave_records`: HR-backed leave entries (starts empty after migration); `status`
  (approved/cancelled/pending) and `external_ref` added in schema v2.
  `leave_type='vacation'` and `'sick'` are counted separately. Private derived
  employee leave fields are `days_taken_this_year` (Vacation) and
  `sick_leave_days_this_year` (Sick); no redundant member counter is stored.
  Country calendars exclude Fri/Sat in Israel and Sat/Sun elsewhere; missing
  country or missing type/member evidence yields unknown totals. Snapshot
  provenance lives in record notes and the import audit, not legacy exports.
- `feature_sets` + `feature_prs`: delivered-feature evidence and PR set grouping provenance.
- `issue_completions`: normalized issue completion events for Bugs/Attributes/Stories metrics.
- `typed_refs`: typed cross-domain links (`tasks`, `bugs`, `raid`, etc.) with provenance.
- `documents`: planning and summary documents captured as database records.
- `exports`: on-demand Confluence/export history.
- `imports`: migration/import provenance and conflict tracking.

## Privacy projection

- `payload --audience private`: includes private summaries and private assignments/feedback.
- `payload --audience shared`: allowlisted projection only; excludes private feedback and private assignments.

## Metrics

`metrics` returns separate values for:
- `features_delivered`
- `bugs_closed`
- `attributes_completed`
- `stories_completed`

With scope: `total`, `quarter`, `last-sprint`, `current-sprint`, plus coverage/freshness metadata.

## Migration invariants

- Keep legacy files unchanged.
- Import all historical 1:1 session archives and working docs.
- Never populate structured leave from legacy Markdown.
- Record source hashes and import status per file.
- Re-import skips unchanged files and marks changed-source conflicts instead of overwriting.

## Schema versioning

`meta.team_schema_version` is currently `8`. V8 adds typed log items
(`meeting_entries.fields_json/record_type/record_id`, `roadblocks.impact/unblocker/visibility`);
v7 adds task category and priority;
v6 adds `focus` and `focus_visibility`;
v5 adds item-level privacy, session revision history and planning calendars;
v4 adds `members.base_country` and `members.roster_role` (`member`/`lead`);
v3 adds employment type and role title. Portal roster and metrics include the lead.
Older versions are upgraded in place with
additive `ALTER TABLE` steps from `SCHEMA_UPGRADES`; newer/unknown versions are refused.

## Session form transaction

`portal_mutate("meeting-create", ...)` accepts optional `title` and `meeting_date`;
empty date becomes the controller's current date. New empty titles become
`1:1 with <display name> - <YYYY-MM-DD>` (other log types use their type label).
Editing an entry with an empty title preserves its existing title; historical
titles are never rewritten automatically. `entries`
contains the freeform notes and any individually classified entries.
Typed items are entries `{entry_id?, tag, fields, visibility}` where `tag` is a
key of `LOG_ITEM_TYPES` (served as `log_item_types`) and `fields` is validated
against that spec (unknown keys, missing required fields, bad dates/options are
rejected). `body` is derived from the type's summary field. Linked records:
`action` → assignment (`kind=action-item`; owner `me` → lead, `member` → the
member, or the lead with owner_label `manager` on the lead's page; created once),
`status` → `assignment_updates` row + optional status change, `roadblock` →
`roadblocks`, `feedback_given/received` → `feedback_items`. The entry stores
`record_type/record_id`; removing the entry deletes owned update/roadblock/
feedback records but never assignments. `personal` is always private. Entries
without `fields` keep legacy `{tag, body}` semantics (legacy `decision` rows are
projected as `{decision: body}`). `roadblock-raid-link {roadblock_id, raid_id}`
records a typed_ref (roadblock → raid-item, `escalated-to`); projections expose
it as `raid_id`. On the lead's own page, blank 1:1 titles use `TEAM_MANAGER_NAME`
(default manager).
`action_items` (legacy) is an optional list of `{title, target_date?, visibility?}`.
The session, entries, immutable snapshot and newly linked assignments are saved
in one transaction. Invalid action rows roll back the whole save.
Each action task has `kind=action-item` and `origin_meeting_id`; existing action
tasks are edited through assignment APIs, not recreated when editing a session.
Session saves do not require `state`: omitted state preserves legacy metadata
(edits of finalized records retain their amendment audit behavior). The portal
does not expose workflow-state or session-visibility controls. Presentation
session presence is derived only from shared entries, irrespective of the legacy
session visibility flag; topic visibility controls title disclosure separately.

## Member tasks and typed Log

Member tasks remain in team-manager, not duplicated into the personal task DB.
Their stable `assignment_id` is visible in the portal. `category` supports
development, debug, research, planning, testing, code-review, track and misc.
`priority` supports critical, high, medium and low. Existing rows receive misc
and medium defaults without changing their IDs or lifecycle. Newly created
portal tasks/actions start as todo. Description, status, outcome, due date,
Jira key, append-only progress notes and per-item privacy remain available.
The category names align with personal-task conventions, with debug/research
added for member work; personal task-manager categories are not silently changed.

Tasks accept `refs: [{domain_name, target_type, target_id, relation?}]`, stored in
the existing typed-reference table. PR references use domain github/type pr and
a validated GitHub PR URL. Other references can target Jira, bugs, tasks, RAID,
or team meeting IDs. They are unchecked links, not copies or cross-store foreign
keys. Saving refs replaces task-form links, preserving links owned by other APIs.
Actions created with a session save their category, priority and refs atomically.

The member Log reuses meetings/entries/history with `session_type` one_on_one,
conversation, meeting or note. Shared projections include the type but still
filter private bodies/titles. Each record belongs to one member; multi-person
meeting records are not automatically copied across profiles.

## Activity observations

`team_activity.activity_payload(store, member_keys)` reads saved activity without
performing network calls. `scan_activity(store, member_key)` explicitly scans
Jira assigned/reported ticket updates and GitHub authored PR updates/reviews.
Owner-managed `activity_observations`, `activity_source_state` and
`activity_scans` tables retain deduplicated observations and per-source cursors.
First scans cover seven days; incremental scans overlap the last successful
cursor to avoid boundary loss. Failed/incomplete scans retain the cursor and
surface errors. Jira/GitHub remain authoritative; observations are not a second
task tracker. Scans require configured source identities and authenticated
clients. `TEAM_ACTIVITY_GITHUB_ORGS` optionally limits GitHub organizations.

## Derived evidence invariants

- `issue_completions.source='bugs-db'` and `feature_sets.grouping_basis LIKE 'review-task%'`
  are mirrors of owner DBs, rebuilt by `backfill`; manual rows use other sources.
- Each derived row has a `typed_refs` row: `relation='evidence-of'`,
  `provenance='backfill:<db>'`, `check_status='resolved'`.
- `payload --audience private` includes `leave` (from `leave_stats`); shared excludes it.
