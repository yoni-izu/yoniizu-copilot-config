# Decisions format

File: `~/team-planner/manager-brief/decisions.md`

All decisions in this file are **Yoni's** \u2014 either already taken (FYI for Timor) or pending (Yoni will take by a target date; Timor sees what's coming).

**Not in this file:**
- Decisions Yoni needs Timor to make. If something requires Timor's call, it's a **risk mitigation** in the brief, not a decision. The brief never asks Timor to decide.
- Team decisions Yoni had no role in. Those go to RAID or team-weekly.

## File header

```markdown
# Decisions

<!-- Managed by the manager-brief skill. All decisions here are Yoni's. -->

## Open

<entries with status recent (taken, awaiting first surfacing) or pending (Yoni will decide)>

## Resolved

<entries with status resolved \u2014 closed for both types>

---
next-id: D-003
```

## Per-entry structure

```markdown
### D-NNN: <short title>
- **Type:** taken | pending
- **Status:** recent | pending | resolved
- **Logged:** YYYY-MM-DD
- **Target:** YYYY-MM-DD          (pending only \u2014 when Yoni will decide)
- **Decision:** <what Yoni decided>          (taken only)
- **Rationale:** <one line>
- **Surfaced in briefs:** YYYY-MM-DD, YYYY-MM-DD
- **Citations:** <Jira key | PR | RAID row | snapshot ID | commitment ID>
- **Resolution:** <one line>                  (set when resolved)
- **Resolved:** YYYY-MM-DD                    (set when resolved)
- **History:**
  - YYYY-MM-DD: logged
  - YYYY-MM-DD: <event>
```

## Field rules

| Field | Rule |
|---|---|
| `D-NNN` | Monotonic, never reused. |
| `Type` | `taken` (already decided) or `pending` (Yoni will decide). Never changes \u2014 if a `pending` decision becomes `taken`, set status `resolved` and the Resolution captures the outcome. |
| `Status` | `recent` (taken, awaiting first surfacing in a brief), `pending` (Yoni will decide), `resolved` (closed). |
| `Target` | Required for `pending`. The date Yoni intends to have decided by. Drives urgency in the brief. |
| `Decision` | Required for `taken`. The actual call Yoni made. |
| `Rationale` | One line. Why this decision / why this target. |
| `Surfaced in briefs` | Append-only. A `taken` decision is typically surfaced once and then drops off; a `pending` decision is surfaced every brief until resolved. |
| `Citations` | At least one supporting reference. |
| `Resolution` | One line on the final outcome. Required when status flips to `resolved`. |
| `History` | Append-only. |

## Status lifecycle

**Taken:**
```
log (type=taken) \u2192 recent \u2192 resolved
```
Auto-resolved after surfacing in one brief (Timor was informed). If Yoni later reverses the decision, log a new `taken` entry referencing the original.

**Pending:**
```
log (type=pending) \u2192 pending \u2192 resolved
```
Stays `pending` and surfaces in every brief until Yoni decides. On resolution, capture the call + date.

## Carry-over in briefs

When generating a brief:

1. **Taken since last sync** \u2192 entries with `Type: taken`, `Status: recent`. Render as one-line bullets under **Decisions \u2192 Taken**. After surfacing, flip status to `resolved` and append a History line.
2. **Pending (mine)** \u2192 entries with `Type: pending`, `Status: pending`. Render as one-line bullets under **Decisions \u2192 Pending (mine)** with target date. They keep appearing until resolved.
3. **Recently resolved pending decisions** \u2192 if a `pending` decision was resolved since the previous brief, surface the resolution once as a `taken` bullet (the Resolution becomes the decision text), then resolve.

## When to log a decision vs a commitment vs a risk

| Situation | Use |
|---|---|
| Yoni promises Timor a deliverable by a date | **Commitment** |
| Yoni already made a scope/direction/approach call | **Decision (taken)** |
| Yoni will make a scope/direction/approach call by a target date | **Decision (pending)** |
| Yoni needs Timor to act / approve / unblock | **NOT a decision.** It's a risk mitigation in the brief's Top risk row. |
| Timor asked Yoni to do something | **Commitment** (Committed = date Timor asked) |
| Personal task / item owed to peer (Ziv/SiVal/scrum) / team chore | **None of these \u2014 use `task-manager`.** Do not log to manager-brief. |
