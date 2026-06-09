# Archive Templates

Used by capability 3 (Post) of the `team-weekly` skill. Two outputs per meeting: a verbose local archive and a sanitized published Confluence page.

---

## Local archive

Verbose, includes private notes. Lives at `~/team-planner/team-weekly/archive/YYYY-MM-DD.md`.

```markdown
# HSI FW Team Weekly — YYYY-MM-DD

**Attendees:** <list> | **Duration:** <if known> | **Next weekly:** <auto-computed date>
**Sprint:** <sprint name>, day <N> | **Milestone:** MS<N>, <X> days remaining, RAG <color>

## Summary
<3-5 line auto-drafted summary; Yoni edits before commit>

## Past — what shipped
### Wins delivered
- <name>: <one-line> — cite: <PR / JIRA>

### Closed tickets (resolution=Done)
| Key | Owner | Summary | Effort |
|---|---|---|---|

## Present — sprint state at meeting time
- Done: <X>d | In progress: <Y>d | Blocked: <Z>d | To-do: <W>d
- Confidence: <X>/10
- Per-IP scoreboard (snapshot):
  | IP | Win | Gap | Status |
  |---|---|---|---|

## Future — agreed direction
- Rest of sprint: <top 3 priorities, with owners if reassigned in meeting>
- Milestone MS<N>: <one-line RAG narrative + top risks>
- Dependencies tracked: <list>

## Global updates relayed
- <date>: <one-line summary> — source: <URL> — team reaction: <one line>

## Discussion
### <Topic 1>
- <name> said: <paraphrase>
- <name> said: <paraphrase>
- Outcome: <note / decision / action>

### <Topic 2>
- ...

### Open discussion (open-floor)
- <prompt>: <key responses, paraphrased>

## Decisions
- <decision> — <one-line context>

## Action items
| # | Action | Owner | Due | Status | Jira |
|---|---|---|---|---|---|
| 1 | <text> | <name> | YYYY-MM-DD | Open | <KEY or "—"> |

## Risks captured
- <risk text> — promoted to RAID? <R-XXX or "no, deferred"> — confirmation: <yes/no>

## Wins recognized publicly
- <one-line> — cite: <PR / JIRA>

## Carry-over for next weekly
- [ ] <unresolved topic>
- [ ] <action still open at meeting end>
- [ ] <parking-lot item>

## Readiness toward MS<N> — meeting outcome
- Items the team agreed are on-track this week:
- Items the team flagged as at-risk this week:
- Net change vs last weekly: <Green ↑ / flat / Red ↓>

## Private notes (PRIVATE — Yoni only)
- <impressions, team-energy notes, individual concerns>
- <coaching follow-ups for 1:1s>
- <things to verify before next weekly>

## Source artifacts
- Confluence page: <URL>
- Prep doc snapshot: see `_last.md`
- Snapshot used: `snapshots/<YYYY-MM-DD>.json`
- Staff updates relayed: see `staff-updates.md`
- RAID rows touched: <R-XXX, R-XXX>
```

---

## Confluence published page

Sanitized. The same page that was created in prep is **updated in place**. Visibility was already lifted to org-wide on `start team weekly`; on publish the body is finalized to the sanitized archive draft (no restriction change).

```markdown
# HSI FW Weekly — YYYY-MM-DD

## Summary
<3-5 line summary, sanitized — same as local archive Summary, reviewed by Yoni>

## Past — what shipped
- <name>: <one-line win> ([link](<URL>))

## Present — sprint health
- Done <X>d · In progress <Y>d · Blocked <Z>d · To-do <W>d · Confidence <X>/10

### Per-IP scoreboard
| IP | Win | Gap | Status |
|---|---|---|---|
| PCIe EP | <one-line> | <one-line> | <RAG> |
| SE | <one-line> | <one-line> | <RAG> |
| D2D | <one-line> | <one-line> | <RAG> |
| Infra/Mng | <one-line> | <one-line> | <RAG> |

## Future
- Rest of sprint: <top 3 priorities>
- Milestone MS<N>: <one-line narrative + top risks>
- Dependencies tracked: <list>

## Global updates
- <date>: <one-line> ([source](<URL>))

## Topics discussed
- <Topic 1> — <one-line outcome>
- <Topic 2> — <one-line outcome>

## Decisions
- <decision>

## Action items
| # | Action | Owner | Due | Jira |
|---|---|---|---|---|
| 1 | <text> | <name> | YYYY-MM-DD | <KEY or "—"> |

## Readiness toward MS<N>
- On track: <list>
- At risk: <list>
- Net change vs last weekly: <↑ / flat / ↓>

## Next weekly
<auto-computed date>

---
*Published <today> — visibility: anyone in the organization (lifted on `start team weekly`)*
```

**Sanitization rules** (excluded from the published page):
- "Private notes" section (entirely private)
- "Open discussion" / open-floor block (entirely internal — stays in the local archive only)
- Individual-team-member capacity, morale, or coaching observations
- Discussion verbatim — collapsed into one-line outcomes
- Risk detail beyond "promoted to RAID R-XXX" or "deferred"
- Win rationale beyond the one-line + link
- Snapshot/RAID internal IDs (only RAID IDs the team is expected to track stay)

**Mention rules** (principle 14):
- Push the published page in HTML (`contentFormat: "html"`).
- Every named attendee, action-item owner, and sprint-focus owner is rendered as a Confluence mention: `<span data-type="mention" data-user-id="ACCOUNT_ID">@Display Name</span>`.
- Cache account IDs in `~/team-planner/team-weekly/_attendee-ids.md` (one row per team member: `Display Name | accountId`). Look up missing IDs with `mcp_atlassian-mcp_lookupJiraAccountId` (uses parameter `searchString`).
- Local archive may keep plain-text names — tagging is published-page only.

---

## History

No separate index page is maintained. The Confluence parent folder (`SWENG/695369790`) lists all weekly pages chronologically. Use `team weekly history` (capability 4) to read the local archive index from `~/team-planner/team-weekly/archive/`.
