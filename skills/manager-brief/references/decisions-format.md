# Decisions format

File: `~/team-planner/manager-brief/decisions.md`

Tracks decisions surfaced in briefs to Timor. Two types:

- **Taken** — Yoni decided; Timor is informed. FYI, not asking for permission.
- **Needed** — requires Timor's call. Carry a deadline and Yoni's recommendation.

## File header

```markdown
# Decisions

<!-- Managed by the manager-brief skill. -->

## Open

<entries with status in `pending` (needed) or `recent` (taken, not yet surfaced or surfaced once)>

## Resolved

<entries with status `resolved` for both types>

---
next-id: D-015
```

## Per-entry structure

```markdown
### D-NNN: <short title>
- **Type:** taken | needed
- **Status:** recent | pending | resolved
- **Logged:** YYYY-MM-DD
- **Deadline:** YYYY-MM-DD | none           (needed only)
- **Recommendation:** <one line>            (needed only)
- **Decision:** <what Yoni decided>          (taken only)
- **Rationale:** <one line>
- **Surfaced in briefs:** YYYY-MM-DD, YYYY-MM-DD
- **Citations:** <Jira key | PR | RAID row | snapshot ID>
- **Resolution:** <one line>                 (set when resolved)
- **Resolved:** YYYY-MM-DD                   (set when resolved)
- **History:**
  - YYYY-MM-DD: logged
  - YYYY-MM-DD: <event>
```

## Field rules

| Field | Rule |
|---|---|
| `D-NNN` | Monotonic, never reused. |
| `Type` | `taken` or `needed`. Never changes — if Timor pushes back on a `taken`, log a new `needed` entry referencing the original. |
| `Status` | `recent` (taken, awaiting surfacing in next brief), `pending` (needed, awaiting Timor), `resolved` (closed). |
| `Deadline` | Required for `needed`. Drives urgency in the brief. |
| `Recommendation` | Required for `needed`. Yoni's stance — make Timor's job easy. |
| `Decision` | Required for `taken`. The actual call Yoni made. |
| `Rationale` | One line. Why this decision / why this recommendation. |
| `Surfaced in briefs` | Append-only. A `taken` decision is typically surfaced once and then drops off; a `needed` decision is surfaced every brief until resolved. |
| `Citations` | At least one supporting reference unless purely managerial. |
| `Resolution` | One line on the final outcome. Required when status flips to `resolved`. |
| `History` | Append-only. |

## Status lifecycle

**Taken:**
```
log (type=taken) → recent → resolved
```
A taken decision is auto-resolved after it has been surfaced in one brief (Timor was informed). Subsequent reversals are logged as a new `needed` decision.

**Needed:**
```
log (type=needed) → pending → resolved
```
Stays `pending` and surfaces in every brief until Timor responds. Resolution captures Timor's call + date.

## Carry-over in briefs

When generating a brief:

1. **Taken since last sync** → all entries with `Type: taken`, `Status: recent`. Render as FYI bullets in the **Taken** subsection. After surfacing, flip status to `resolved` and append a History line.
2. **Needed from Timor** → all entries with `Type: needed`, `Status: pending`. Render as bullets with deadline + recommendation in the **Needed** subsection. They keep appearing until resolved.
3. **Recently resolved needed decisions** → if a `needed` decision was resolved since the previous brief, mention it in a one-line footer under the **Needed** subsection:
   `*Resolved since last sync: D-011 — <one-line resolution>.*`

## When to log a decision vs a commitment

| Situation | Use |
|---|---|
| Yoni promises to deliver something by a date | **Commitment** |
| Yoni made a call that affects scope / direction / approach | **Decision (taken)** |
| Yoni needs Timor to make a call | **Decision (needed)** |
| Yoni needs Timor's approval / sign-off / resource | **Decision (needed)** + matching ask in brief |
| Timor asked Yoni to do something | **Commitment** (logged with `Committed: <date Timor asked>`) |
