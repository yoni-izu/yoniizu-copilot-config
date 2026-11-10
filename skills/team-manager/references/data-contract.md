# Team manager data contract

Canonical store: `~/tasks/team.db` (`TEAM_DB` override), owned by `team-manager`.

## Entity families

- `members`: stable roster keys and profile identifiers.
- `meetings` + `meeting_entries`: historical and working 1:1 records, including full narrative source.
- `tracked_items` + `item_updates`: persistent identities, lifecycle and append-only
  updates for every log row. Meeting rows reference items rather than duplicating
  their identity when following up.
- `assignments` + `assignment_updates`: managerial directions and progress updates.
- Member `focus` is current work, not an assignment and has no lifecycle status.
  `focus_visibility` defaults to shared; private focus is omitted from shared payloads.
  Assignments are the Task type in the member Items view.
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

`meta.team_schema_version` is currently `11`. V11 adds tracked items, item updates,
`meeting_entries.item_id/follow_up_from`, and repeat-safe registration of existing
and imported rows. Existing non-task lifecycle states remain Unspecified unless
explicitly recorded; existing task statuses and discussion follow-up flags are
preserved. Schema upgrades and registration run in one transaction. V10 adds the nullable
`meetings.deleted_at` marker for audited session deletion. V9 adds
`meeting_entries.priority` (default Medium); existing action rows inherit their
linked task priority, or their saved item priority when unlinked. V8 adds typed
log items (`meeting_entries.fields_json/record_type/record_id`,
`roadblocks.impact/unblocker/visibility`);
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
rejected). Every row also has a `priority` (`critical`, `high`, `medium`, or
`low`; default `medium`); action-item priority follows the linked assignment
after creation. `body` is derived from the type's summary field. Linked records:
`action` → assignment (`kind=action-item`; owner `me` → lead, `member` → the
member, or the lead with owner_label `manager` on the lead's page; created once),
`status` with a task → `assignment_updates` row + optional task status change;
without a task, it is a standalone session entry with optional activity name and
required progress text (no assignment update or task status mutation). `roadblock` →
`roadblocks`, `feedback_given/received` → `feedback_items`. The entry stores
`record_type/record_id`; removing the entry deletes owned update/roadblock/
feedback records when no other active meeting uses the tracked item, but never
assignments. Removing the last non-task occurrence cancels its tracked item and
clears follow-up; removing one occurrence never removes another meeting's item.
`personal` is visibility-controlled.
Portal visibility controls default to shared for new entries, including
Personal; edits preserve saved values, and owner mutations that omit visibility
still default to private. Entries without `fields` keep legacy `{tag, body}`
semantics (legacy `decision` rows are projected as `{decision: body}`).
`roadblock-raid-link {roadblock_id, raid_id}`
records a typed_ref (roadblock → raid-item, `escalated-to`); projections expose
it as `raid_id`. On the lead's own page, blank 1:1 titles use `TEAM_MANAGER_NAME`
(default manager).
`portal_mutate("meeting-shared-save", ...)` accepts a shared projection's opaque
`edit_token` as its revision and only updates shared-visible entries. It merges
submitted rows with the current private snapshot, preserving all hidden private
rows and a hidden topic, rejects stale revisions or private rows, and returns
only the shared projection.
`portal_mutate("meeting-delete", {id, revision})` is private-only and
revision-checked. It marks the session deleted while preserving immutable
revision snapshots, removes linked progress/roadblock/feedback records using the
same rules as removing their entries, and leaves task assignments intact.
Deleted sessions are omitted from private and shared portal payloads.
`action_items` (legacy) is an optional list of `{title, target_date?, visibility?}`.
The session, entries, immutable snapshot and newly linked assignments are saved
in one transaction. Invalid action rows roll back the whole save.
Each action task has `kind=action-item` and `origin_meeting_id`; existing action
tasks retain their assignment identity and are not recreated when editing or
carrying a session item. Task details use assignment APIs; common tracking controls
may update status/owner/due date through the linked assignment.
Session saves do not require `state`: omitted state preserves legacy metadata
(edits of finalized records retain their amendment audit behavior). The portal
does not expose workflow-state or session-visibility controls. Presentation
session presence is derived only from shared entries, irrespective of the legacy
session visibility flag; topic visibility controls title disclosure separately.

## Tracked items and recurring follow-ups

`portal_payload` includes audience-filtered `items` and `item_statuses`.
Each item has an ID, type, summary, status, priority, optional owner and due date,
visibility, follow-up flag, meeting occurrences and progress updates. Task items
read status, ownership, priority, due date and visibility from their assignments;
there is no second task lifecycle. Non-task states are Unspecified, Open,
In progress, Blocked, Done, Cancelled and Deferred; new non-task rows start Open.

`portal_mutate("meeting-prepare", {member_key, meeting_date?, session_type?})`
returns an unsaved draft's follow-up entries. It does not create a meeting.
Only a new 1:1 carries checked items for the same member, excluding Done/Cancelled,
deleted sources and future occurrences. Same-day consecutive 1:1s are supported.
Standalone task items may also carry after checking follow-up. Other log types
return no carried entries. Carrying repeats until the flag is cleared or the item
is completed; each occurrence retains its source date and the same item ID.

Meeting rows accept `item_id`, `item_revision`, `follow_up` (strict boolean),
`tracking_status`, `tracking_owner` (`me`/`member`/null) and `tracking_due`
(ISO date/null). Existing rows need the current item revision when submitting
tracking controls; carried rows always require it. All item revisions and
membership checks are validated before writes, and a conflict rolls back the save.
Unchanged tracking status/owner/due date need not be resubmitted. Historic row
text is preserved independently of the current item state.

`item-set {id, revision, status?, priority?, owner?, target_date?, follow_up?,
visibility?}` edits tracking. `item-note {id, revision, note, visibility?}` appends
an update (assignment progress for tasks). Both are Private-only through HTTP.
`bulk-set` also accepts `entity_type=item` with status, priority, target_date or
follow_up; mixed-type edits are revision-checked and atomic.

Every typed, freeform Notes and legacy row is tracked. The old discussion
`fields.follow_up` boolean remains accepted for compatibility; the common
top-level flag is authoritative when supplied. Received feedback's text
`fields.follow_up` is unrelated and remains unchanged.
Presentation requires both the occurrence and current item/linked record to be
shared. Shared log edits preserve all hidden rows and use opaque item edit tokens.

Before upgrading a live store, use `team.py backup <private-destination>`.
The owner takes an integrity-checked SQLite backup without upgrading the source
or overwriting an existing backup.

## Member tasks and typed Log

Member tasks remain in team-manager, not duplicated into the personal task DB.
Their stable `assignment_id` remains available; the Items table shows the persistent
`item_id`. Task category supports
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
