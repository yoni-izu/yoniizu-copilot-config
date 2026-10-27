---
name: team-manager
description: "Unified team-record and planning owner. Use when: 1:1 lifecycle, team-member records, managerial assignments, peer/stakeholder feedback, planning reports, leave (HR-backed), delivery metrics, and on-demand Confluence summary export."
argument-hint: "Examples: prep/start/end 1:1, log assignment for <member>, show member profile, milestone brief, team metrics for current sprint, export summary to Confluence."
---

> **Portal handoff:** For Team Portal/dashboard feature requests, UI/API changes,
> bugs, serving, restart, or maintenance operations, load
> [portal-maintainer](../portal-maintainer/SKILL.md). Domain records remain owned
> by this skill; coordinate cross-domain needs through owner APIs.

# Team Manager — Unified 1:1 + Planning + Member Records

This skill is the unified owner replacing legacy `one-on-one` and `team-planner`.

## Source of truth

- Canonical storage: `~/tasks/team.db` (owner API/CLI).
- Legacy Markdown (for example [members/](/home/yoniizu/team-planner/members)) is historical input/audit evidence only.
- Confluence pages are export targets, not primary records.

## Scope

1. **Member records**
   - identifiers, personal-space URLs, goals, onboarding date, birthday fields, IP ownership.
2. **1:1 lifecycle**
   - prep/live/finalization/history with private vs shared separation.
3. **Managerial assignments**
   - nuanced directions/outcomes beyond literal Jira tracking; may reference a single Jira ticket.
4. **Feedback and roadblocks**
   - delivered feedback, peer/stakeholder feedback, engineer-raised blockers.
5. **Planning outputs**
   - milestone/sprint status, forecasting, risk framing, team and manager briefs.
6. **Delivery metrics**
   - separate counts for features, bugs, attributes, stories with evidence and scope coverage.
7. **Typed cross-database references**
   - links to tasks/bugs/RAID/PR and other owned domains with provenance.

## Skill boundaries

| Concern | Owner |
|---|---|
| Team records, assignments, 1:1 history, planning summaries, metrics | **`team-manager`** |
| Jira reads/writes and issue mechanics | `jira-hsi-planning` |
| Personal task tracker | `task-manager` |
| RAID database operations | `team-planner/rd.py` owner API (called, not duplicated) |
| Bug/phenomenon ownership | `bug-monitor` / `bug-triage` |
| Team portal implementation and operations | `portal-maintainer` |

## Data policy

1. Database-first: write and read through owner API/CLI only.
2. Keep private data private by default; shared payloads are allowlisted.
3. Unknown values remain unknown (never auto-zero or invented).
4. Do not duplicate external authorities (Jira/GitHub/RAID/task DB); store typed references and observed facts.
5. Use append-only event notes for timeline/provenance.

## Leave policy

- Structured leave starts empty after migration.
- Do **not** import historical leave tables from legacy Markdown.
- Backfill leave only from HR-authoritative records via `team.py leave-import <csv>`
  (preview by default; `--apply` to write). See [workflows.md](references/workflows.md#4-leave-workflow).
- With no HR import, leave stats report **unknown** (`null`), never zero.
- HR calendar snapshots supplied by the user may be transcribed to the owner
  CSV importer after confirming approval status. Retain snapshot/month provenance;
  import only explicitly requested categories (Vacation and Sick for this workflow),
  not holiday icons, Non Working Day, Privilege Day or Other. Merge continuous
  periods across month boundaries; missing snapshots are not evidence of zero leave.
- Count working days using `members.base_country`: Israel excludes Friday and
  Saturday; every other known country excludes Saturday and Sunday. Apply this
  rule to both Vacation and Sick; never charge excluded days. Missing country
  means unknown totals, not an assumed calendar. Do not infer public holidays.
- Store Sick as `leave_records.leave_type='sick'`, separate from `vacation`.
  The private employee leave field `sick_leave_days_this_year` is derived from
  those database records; `days_taken_this_year` counts Vacation only. Do not
  maintain duplicate mutable annual counters or expose sickness in shared payloads.

## Evidence backfill

`team.py backfill` (preview by default; `--apply` to write) derives metric evidence
from owner databases, read-only:
- `~/tasks/bugs.db` closed bugs → `issue_completions` (Bug) matched by Jira account ID.
- `~/tasks/tasks.db` review tasks → `feature_sets` candidates matched by GitHub handle,
  grouped by the task's Jira key (else per task).
Every derived row gets an `evidence-of` typed ref back to its source. Features stay
`candidate` unless confirmed (`set feature … confirmation_state`) or `--confirm-merged`
is passed; only confirmed/accepted delivered features count.

## Metrics semantics

- Keep these metrics **separate**:
  - Features delivered
  - Bugs closed
  - Attributes completed (literal Jira issue type)
  - Stories completed (literal Jira issue type)
- Supported scopes:
  - total (all recorded)
  - selected quarter
  - last closed sprint
  - current sprint
- Include coverage/freshness metadata with each metric output.

## Feature grouping

Feature/PR-set candidates may be formed from:
- explicit curated sets
- dependency-linked PR groups
- PR titles mentioning the same Jira key

Ambiguous multi-key cases must stay unconfirmed until explicitly resolved.

## Confluence behavior

- Confluence export is **on demand**, never silent background sync.
- Summaries are previewed/approved before push.
- Record export provenance (source revision, page ID/version, audience).
- Local database remains canonical even when a page is edited externally.

## Legacy compatibility

`one-on-one` and `team-planner` remain as compatibility wrappers that route here.
