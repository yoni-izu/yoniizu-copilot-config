# Brief template

Two views of every brief: the **Confluence storefront** (what Timor reads) and the **local archive** (verbose; adds Yoni-only private notes + projection appendix + standing-topics coverage).

Both follow the same section order. The local archive may include `**Private (Yoni only):**` blocks the Confluence version omits.

## Hard caps (enforced)

| Section | Cap |
|---|---|
| Bottom line | 1 sentence |
| Milestone health | 1 row per milestone (RAG + verdict + confidence in the same row \u2014 no separate "Will we hit it?" section) |
| Commitments to Timor | table; one row per commitment |
| Decisions \u2014 Taken | bullets, one line each (mine, FYI for Timor) |
| Decisions \u2014 Pending | bullets, one line each (mine, target date) |
| Top risk | max 1 row |
| Agile changes | bullets, one line each |
| Highlights | max 2 bullets |

**No "Asks" section.** **No paragraphs anywhere.** Omit any empty section.

## Anti-duplication rule

Every fact appears in exactly one section. Before finalizing the brief, scan for repeats:

| If a fact is in\u2026 | Then remove it from\u2026 |
|---|---|
| Milestone health row | Bottom line (only the verdict-level summary stays in bottom line) |
| Commitments table | Decisions, Agile changes |
| Decisions (Pending) | Top risk, Agile changes |
| Top risk row | Agile changes, Milestone health |
| Agile changes | Highlights |

If two sections describe the same milestone state (RAG + projection + scope cut), collapse to one row in Milestone health and one Pending decision row \u2014 not three sections.

## Confluence storefront layout

Title: `Yoni:Timor - YYYY-MM-DD`

```markdown
# Yoni:Timor \u2014 YYYY-MM-DD

## Bottom line
<one sentence verdict>

## Milestone health
| Milestone | Target | RAG | Verdict | Confidence |
|---|---|---|---|---|
| MS6 | 2026-07-21 | \ud83d\udd34 | Will miss as scoped \u2014 <one-line why> | 7/10 |
| MS7 | 2026-09-01 | \ud83d\udfe1 | <one-line> | \u2014 |

## Commitments to Timor
| ID | Commitment | Due | Status | Outcome / next |
|---|---|---|---|---|
| C-NNN | <short title> | YYYY-MM-DD | in-flight \\| done \\| slipped \\| blocked | <one line> |

## Decisions
**Taken:** <one-line decision> (citation)
**Pending (mine):** <one-line decision Yoni will make> \u2014 target YYYY-MM-DD

## Top risk
| Risk | Mitigation |
|---|---|
| <one line> | <one line> |

## Agile changes
- <one line>
- <one line>

## Highlights
- <win 1 \u2014 cite PR/ticket>
- <win 2>
```

## Local archive layout

Path: `~/team-planner/manager-brief/briefs/YYYY-MM-DD.md`

Mirrors the Confluence layout, plus:

- **Header metadata block** with `Brief date`, `Previous brief`, `Confluence page URL`, `Published: <timestamp>`.
- **Inputs used** section listing snapshot IDs, RAID revision, PR-monitor pull time, velocity source file.
- **Standing topics coverage** table \u2014 one row per standing topic, status `addressed` / `no change` / `deferred (reason)`.
- **Private (Yoni only)** blocks inline under any section where Yoni captured private context. These never reach Confluence.
- **Projection details** appendix \u2014 full inputs + math per the [projection method](./projection.md).

```markdown
# Yoni:Timor \u2014 YYYY-MM-DD (local archive)

- Brief date: YYYY-MM-DD
- Previous brief: YYYY-MM-DD | first brief
- Confluence page: <URL> | not yet published
- Published: YYYY-MM-DD HH:MM | not yet

## Inputs used
- team-planner snapshot: snapshots/YYYY-MM-DD.json
- RAID revision: <mtime or commit>
- pr-monitor pull: YYYY-MM-DD HH:MM
- velocity source: ~/hsi_done_cycle_time_analysis_v2.csv
- Jira JQL window: updated >= -7d
- task-manager: tm.py list snapshot

## Standing topics coverage
| Topic | Status | Note |
|---|---|---|
| <topic> | addressed \\| no change \\| deferred | <one line> |

## Bottom line
<same as Confluence>

## Milestone health
<same table>

### Projection details (appendix)
<per projection.md \u2014 inputs, math, sensitivity, per milestone>

## Commitments to Timor
<same table>

**Private (Yoni only):** <internal framing if any>

## Decisions
<same>

## Top risk
<same>

## Agile changes
<same>

## Highlights
<same>
```

## Voice & framing rules

- One-sentence verdicts. No qualifiers ("we're tracking toward", "it looks like"). State the verdict.
- Numbers in the brief are rounded and qualitative ("~3\u00d7 capacity", "~310 effort-days"). Exact figures live in the archive projection appendix.
- Routine motion is suppressed: sprint reshuffles, single-ticket transitions, routine review cycles do not belong here.
- Citations are inline references (Jira key, PR, RAID row, snapshot ID, C-NNN, D-NNN), not footnotes or links to long pages.
- Decisions never request permission. `Taken` = Yoni did it. `Pending` = Yoni will do it by target date. If Timor must do something, it's a risk mitigation, not a decision.
- Commitments are only what Yoni owes Timor. Personal tasks, items owed to peers (Ziv/SiVal, scrum, etc.), team chores \u2014 these live in `task-manager`, not here.
