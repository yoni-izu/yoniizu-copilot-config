# Brief template

Two views of every brief: the **Confluence storefront** (what Timor reads) and the **local archive** (verbose, includes private notes Yoni added during review).

Both follow the same section order. The local archive may include `**Private (Yoni only):**` blocks the Confluence version omits.

---

## Hard caps (enforced)

| Section | Cap |
|---|---|
| Bottom line | 2 sentences |
| Milestone health | 1 line per milestone |
| Will we hit it? | 1 short paragraph per milestone (verdict + rationale + confidence) |
| Commitments to Timor | table; one row per commitment |
| Decisions — Taken | bullet list, one line each |
| Decisions — Needed | bullet list, one line each + recommendation |
| Top risks | max 3 |
| Agile changes | bullets, only items Timor needs to know |
| Highlights | max 3 |
| Asks | bullets, explicit |

Omit any section that is empty after applying caps. Do not pad.

---

## Confluence storefront layout

Title: `Yoni:Timor - YYYY-MM-DD`

```markdown
# Yoni:Timor — YYYY-MM-DD

*Brief covering changes since <prev-brief-date>.*

## Bottom line
<one or two sentences: on-track verdict + the single biggest change since last sync>

## Milestone health
| Milestone | RAG | Why |
|---|---|---|
| MS6 | 🟢 | <one line> |
| MS6.5 | 🟡 | <one line> |
| MS7 | 🟠 | <one line> |

## Will we hit it?
**MS6** — <verdict>. <rationale, one paragraph>. Confidence: <N>/10.
**MS6.5** — <verdict>. <rationale>. Confidence: <N>/10.

## Commitments to Timor
| ID | Commitment | Due | Status | Outcome / next step |
|---|---|---|---|---|
| C-007 | <short title> | 2026-06-16 | in-flight | <one line> |
| C-005 | <short title> | 2026-06-09 | done | <one line> |

*Closed since last sync: C-003 (done), C-004 (cancelled — see decisions log).*

## Decisions
**Taken since last sync** (FYI):
- D-012 — <one line decision + rationale> [link: <Jira/PR/RAID>]

**Needed from Timor**:
- D-014 — <one line decision needed>. Deadline: <date>. My recommendation: <one line>.

## Top risks
| Risk | Blast radius | Mitigation | Ask |
|---|---|---|---|
| <one line> | <impact> | <one line> | <ask or "none"> |

## Agile changes
- <replan / milestone move / scope cut / people change Timor needs to know — one line each>

## Highlights
- <win 1 — cite PR/ticket>
- <win 2>
- <win 3>

## Asks
- <explicit ask 1>
- <explicit ask 2>
```

---

## Local archive layout

Path: `~/team-planner/manager-brief/briefs/YYYY-MM-DD.md`

Mirrors the Confluence layout, plus:

- **Header metadata block** at the top with `Brief date`, `Previous brief`, `Confluence page URL`, `Published: <timestamp>`.
- **Inputs used** section (collapsible-style block) listing snapshot IDs, RAID revision, PR-monitor pull time, velocity source file — for audit/debugging future briefs.
- **Standing topics coverage** table — one row per standing topic with status `addressed` / `no change` / `deferred (reason)`.
- **Private (Yoni only)** blocks inline under any section where Yoni captured private context during review (gut-checks, things to watch, framing notes). These never reach Confluence.
- **Projection details** appendix — the full input + math for "Will we hit it?", per the [projection method](./projection.md). The Confluence version only shows the verdict; the archive shows the work.

```markdown
# Yoni:Timor — YYYY-MM-DD (local archive)

- Brief date: YYYY-MM-DD
- Previous brief: YYYY-MM-DD
- Confluence page: <URL>
- Published: YYYY-MM-DD HH:MM

## Inputs used
- team-planner snapshot: snapshots/YYYY-MM-DD.json
- RAID revision: <file mtime or commit>
- pr-monitor pull: YYYY-MM-DD HH:MM
- velocity source: ~/hsi_done_cycle_time_analysis_v2.csv (rows N..M)
- Jira JQL window: updated >= -7d

## Standing topics coverage
| Topic | Status | Note |
|---|---|---|
| Headcount vs plan | no change | — |
| MS6 RAG | addressed | see Milestone health |
| ... | ... | ... |

## Bottom line
<same as Confluence>

**Private (Yoni only):** <framing Yoni wants to remember but not publish>

## Milestone health
<same as Confluence>

## Will we hit it?
<same verdicts as Confluence>

### Projection details (appendix)
<per projection.md — inputs, math, sensitivity>

## Commitments to Timor
<same table>

## Decisions
<same>

## Top risks
<same>

## Agile changes
<same>

## Highlights
<same>

## Asks
<same>
```

---

## Voice & framing rules

- Executive register. No firmware jargon unless Timor uses it (PCIe / SE / D2D / SU are fine).
- Verdict first, evidence second. "We will hit MS6 — completion is 78% with 9 working days and 22 effort-days remaining; velocity supports it." Not the reverse.
- Asks are imperative and single-sentence. "Approve adding 1 contractor for D2D bring-up through MS7." Not "I was wondering if maybe..."
- Decisions Yoni took are FYI, not requests for permission. State the decision + one-line rationale; do not re-litigate.
- Risks are always paired with mitigation + ask. A risk with no ask is a status line, not a risk.
- Suppress routine motion. Sprint reshuffles, single-ticket transitions, and routine review cycles do not belong in this brief.
