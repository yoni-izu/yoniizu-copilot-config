# Projection method — "Will we hit it?"

The brief's most important section. Goal: a defensible verdict per active milestone, with Yoni in the loop to review and adjust.

## Inputs

For each active milestone, gather:

1. **Remaining scope (effort-days)** — via `jira-hsi-planning`:
   - JQL: `project = <proj> AND fixVersion = "<milestone>" AND status != Done`
   - Sum `timeoriginalestimate` (seconds) ÷ 28800 → effort-days. Use individual `getJiraIssue` calls per ticket since `searchJiraIssuesUsingJql` strips the estimate field (see team-planner's snapshot note).
   - **`In Review` tickets are code-complete** (development done, awaiting merge — see `jira-hsi-planning` → Jira Status Semantics). They are not `Done`, so they appear in this query, but their **remaining engineering effort is ~0**. Track their effort-days separately and exclude (or heavily discount) them from `remaining_scope` for the burn computation — counting them as full remaining work understates how close the milestone is. Report `In Review` effort-days as a distinct "code-complete, awaiting merge" line.
   - Separate **unestimated tickets** count — these are a risk indicator on their own.

2. **Velocity (effort-days per working day, team-wide)** — three sources, used together:
   - **Recent (last 30d):** sum of effort-days of tickets transitioned to Done with `resolution=Done` in the last 30 days ÷ 22 working days.
   - **Milestone-to-date:** sum of effort-days of Done-with-resolution tickets in the *current* milestone window ÷ working days elapsed since milestone start.
   - **Cycle-time-derived:** read `~/hsi_done_cycle_time_analysis_v2.csv` (or latest `v*`); use the team-wide median cycle time and ticket-throughput to compute a third estimate. This is the calibration anchor.

   Report all three; use the **median of the three** as the projection input. If the spread is >2x, lower the confidence.

3. **Working days remaining** — calendar days from today to milestone target date, minus weekends, minus team-wide approved PTO (read all `~/team-planner/members/*.md` day-off logs), minus known holidays.

4. **Blocked-external reserve** — read RAID for active Red/Orange dependencies (RTL drops, lab slots, host SW, board access). For each such dependency, estimate days at risk and subtract from working days remaining. Source: `team-planner` capability 7 (Risk Briefing).

5. **Capacity hits** — read upcoming PTO (member files), known headcount changes, known cross-functional commitments (validation slots, FAE support). Subtract from velocity-days.

## Computation

```
projected_burn  = velocity_used × (working_days_remaining − blocked_external_reserve − capacity_hits)
projected_done  = projected_burn               (effort-days)
gap             = remaining_scope − projected_done
                  (positive: scope > burn → at risk; negative: burn > scope → on track)
```

## Verdict mapping

| Gap (effort-days) | Verdict |
|---|---|
| ≤ 0 with confidence ≥ 7 | **Will hit.** Green. |
| ≤ 0 with confidence < 7 | **Likely hit, watch.** Yellow. |
| 0 < gap ≤ 15% of remaining scope | **At risk.** Orange. State the mitigation and the trigger to escalate. |
| > 15% of remaining scope | **Will miss.** Red. State scope-cut options or new target date. |

## Confidence (1-10)

Start at 7. Adjust:

| Factor | Adjustment |
|---|---|
| Velocity sources within ±20% of each other | +1 |
| Velocity sources spread > 2x | −2 |
| Unestimated tickets > 10% of milestone scope | −2 |
| Active Red RAID rows affecting this milestone | −1 each (cap −3) |
| Recent week's burn matched projection | +1 |
| Recent week's burn missed projection by >30% | −2 |
| Significant new scope added since last snapshot | −1 |

Floor at 1, ceiling at 10. Show the adjustments in the projection appendix.

## Sensitivity check

Always compute and report:
- **Best case:** use the highest velocity source and zero blocked reserve.
- **Worst case:** use the lowest velocity source and double the blocked reserve.
- **Spread:** worst-case gap vs best-case gap. A wide spread means low confidence regardless of the score.

## Output in the brief

**Confluence (one paragraph per milestone):**
> **MS6** — Will hit. Completion is 78%; 22 effort-days remain with 9 working days and team velocity of ~3 effort-days/day. Confidence 8/10.

**Local archive appendix (full audit trail):**

```markdown
### Projection — MS6

| Input | Value | Source |
|---|---|---|
| Remaining scope | 22 effort-days | Jira fixVersion=MS6, status != Done, summed timeoriginalestimate |
| Unestimated tickets | 2 | (small risk) |
| Velocity — recent 30d | 3.1 ed/day | Jira Done, last 30d / 22 working days |
| Velocity — milestone-to-date | 2.8 ed/day | Jira Done since MS6 start / 28 days elapsed |
| Velocity — cycle-time-derived | 3.0 ed/day | ~/hsi_done_cycle_time_analysis_v2.csv median |
| Velocity used (median) | 3.0 ed/day | |
| Working days remaining | 9 | calendar to 2026-06-22 minus PTO/holidays |
| Blocked external reserve | 1 day | RAID Red: D2D RTL drop |
| Capacity hits | 0 | |
| Projected burn | 24 ed | 3.0 × (9 − 1 − 0) |
| Gap | −2 ed | scope < burn → on track |
| Verdict | Will hit | Green |
| Confidence | 8/10 | +1 velocity sources tight, no Red RAID |
| Best case | gap −5 ed | velocity 3.1, no reserve |
| Worst case | gap +1 ed | velocity 2.8, reserve doubled |
```

## Yoni-in-the-loop

Before publishing, show Yoni:

1. The verdict per milestone.
2. The full inputs table (collapsible in the chat — Yoni scans it).
3. Any verdict where Yoni's gut disagrees with the math — ask him to override and capture his reasoning in the appendix.

A Yoni-override always wins in the published brief, but both the computed verdict and Yoni's adjusted verdict are recorded in the local archive appendix.

## Anti-patterns

- **Do not** publish a projection without showing Yoni the inputs first.
- **Do not** average across milestones — report each separately.
- **Do not** use story points — this team uses effort-days only.
- **Do not** count `resolution != Done` tickets as completed (see team-planner Ticket Resolution Semantics).
- **Do not** silently inflate confidence to mask a wide sensitivity spread.
