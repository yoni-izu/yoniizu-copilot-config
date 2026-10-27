# Commitments format

File: `~/team-planner/manager-brief/commitments.md`

This is the **canonical** store for everything the team lead has committed to the manager. The `task-manager` mirror (if any) is just for daily visibility — rich state, history, and outcome narrative live here.

## File header

```markdown
# Commitments to the manager

<!-- Managed by the manager-brief skill. ID counter is at the end of the file. -->

## Open

<entries with status in-flight | slipped | blocked>

## Closed

<entries with status done | cancelled>

---
next-id: C-008
```

## Per-entry structure

```markdown
### C-NNN: <short title>
- **Status:** in-flight | done | slipped | blocked | cancelled
- **Committed:** YYYY-MM-DD
- **Due:** YYYY-MM-DD
- **Surfaced in briefs:** YYYY-MM-DD, YYYY-MM-DD
- **Task mirror:** T-NNN (task-manager) | none
- **Description:** <what I committed to — one or two sentences>
- **Outcome / next step:** <one-line current state — what the brief will show>
- **History:**
  - YYYY-MM-DD: logged (due YYYY-MM-DD)
  - YYYY-MM-DD: <event — e.g., "slipped: blocked on RTL drop">
  - YYYY-MM-DD: <event>
```

## Field rules

| Field | Rule |
|---|---|
| `C-NNN` | Monotonic, never reused. Allocated from `next-id`; bump after allocation. |
| `Status` | One of the listed values. Closed values (`done`, `cancelled`) move the entry to the `## Closed` section. |
| `Committed` | Date the team lead made the promise. Never changes. |
| `Due` | Current committed-by date. Updated on slip; old value stays in `History`. |
| `Surfaced in briefs` | Append the date of each brief this commitment appeared in. Drives "since last sync" carry-over. |
| `Task mirror` | If a `task-manager` task was created, its ID. The task `context` field references back: `manager-brief C-NNN`. |
| `Description` | Stable narrative. Edit only for clarity, not to rewrite what was promised. |
| `Outcome / next step` | Refreshed every update. This is what the brief shows in the Commitments table. |
| `History` | Append-only. Never edit or delete prior entries. |

## Status lifecycle

```
                       ┌── done (closed)
                       │
log → in-flight ───────┼── slipped → in-flight  (new due)
                       │
                       ├── blocked → in-flight  (unblocked)
                       │
                       └── cancelled (closed)   (with reason in History)
```

- `slipped` and `blocked` are transient states — they must transition back to `in-flight` or to a closed state. They are not terminal.
- `done` requires an `Outcome` line that describes what was delivered, ideally with a citation (Jira key, PR, document link).
- `cancelled` requires a `reason` recorded in the History line of cancellation.

## Task-manager mirror

When mirroring is enabled for a commitment:

| Commitment event | Mirror action |
|---|---|
| Log new commitment | `tm.py add "<title>" -c planning -p high --context "manager-brief C-NNN" --no-workspace` |
| Mark done with outcome | `tm.py done T-NNN -n "<outcome>"` |
| Mark cancelled | `tm.py cancel T-NNN` |
| Mark blocked | `tm.py block T-NNN` |
| Mark slipped (still open) | `tm.py update T-NNN todo -n "slipped, new due YYYY-MM-DD"` |

The mirror exists for daily visibility in `tm list` — it is **not** the source of truth. If the two ever disagree, the commitments file wins; reconcile by re-running the mirror action.

## Carry-over in briefs

When generating a brief:

1. **Open commitments** → all entries in `## Open`, shown in the Commitments table.
2. **Closed since last sync** → entries in `## Closed` with a `History` event after the previous brief date. Shown as a footer line under the table:
   `*Closed since last sync: C-003 (done), C-004 (cancelled — see decisions log).*`
3. **No drops without trace.** A commitment never disappears from view between briefs without an explicit closure event in `History`.
