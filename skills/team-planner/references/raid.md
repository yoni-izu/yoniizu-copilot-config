# RAID Log Reference

Canonical records live in `~/tasks/raid.db`, owned by `../rd.py`.
Use `rd.py list/show/payload` to read and `set/new/close` to mutate.
The old `~/team-planner/raid.md` is a backed-up import/export document only.
Do not hand-edit Markdown or SQLite to update a live record. Preserve narrative
Markdown through owner APIs.

## Columns

| Field | Description |
|---|---|
| ID | `R-NNN` (Risk), `A-NNN` (Assumption), `I-NNN` (Issue), `D-NNN` (Dependency) |
| Type | Risk \| Assumption \| Issue \| Dependency |
| Title | Short descriptive name |
| Description | What is happening or could happen; evidence or signal |
| FW Owner | Team member responsible for tracking/mitigation |
| External Owner | Person/team outside FW who owns the dependency or root cause |
| Probability | 1–5 (see scale below) |
| Impact | 1–5 (see scale below) |
| Score | Probability × Impact |
| RAG | Green (1-4) \| Yellow (5-9) \| Orange (10-14) \| Red (15-25) |
| Trigger | Observable condition that means this risk/assumption has materialized |
| Mitigation | Action to reduce probability or impact |
| Contingency | Fallback if mitigation fails |
| Due | Date by which trigger should be checked or mitigation completed |
| Status | Open \| Active \| Mitigated \| Closed |
| Link | Jira issue, spec section, CI run, log, or other evidence |

## Probability Scale

| Score | Meaning |
|---|---|
| 1 | Rare; no current signal |
| 2 | Unlikely; weak signal |
| 3 | Plausible; credible path to happening |
| 4 | Likely; current trend points toward it |
| 5 | Happening or nearly certain |

## Impact Scale

| Score | Meaning |
|---|---|
| 1 | Local annoyance |
| 2 | One story or one engineer-day |
| 3 | Sprint goal or subsystem slice |
| 4 | Milestone exit criterion |
| 5 | Tape-out, first-silicon, customer, or release gate |

## RAG Thresholds

| Score range | RAG |
|---|---|
| 1–4 | Green |
| 5–9 | Yellow |
| 10–14 | Orange |
| 15–25 | Red |

## Review Cadence

| When | What |
|---|---|
| Daily | Update active Red issues and blockers |
| Weekly (team status) | Review top 5 RAID rows; confirm Red/Orange rows have owner + trigger + mitigation + due |
| Sprint planning | Re-score all open Red/Orange rows; convert unresolved assumptions into risks/dependencies; check sprint scope still fits capacity |
| Milestone review / retro | Close resolved rows; identify issues not predicted as risks; convert repeated dependency failures into planning rules |

## Rules

- A RAID row without an owner is just anxiety in spreadsheet form — always assign.
- Assumptions are risks that have not been asked a good enough question yet.
- Dependency rows without external owners are unmanaged — escalate or accept explicitly.
- Keep only cross-functional, milestone-relevant, or high-impact items. Prune stale rows.
- Every Red/Orange row must have: owner, trigger, mitigation, due date.
