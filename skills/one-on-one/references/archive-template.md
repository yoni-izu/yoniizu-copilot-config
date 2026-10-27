# Archive Templates

Used by capability 3 (Post) of the `one-on-one` skill. Two outputs per meeting: a verbose local archive and a sanitized published Confluence page.

---

## Local archive

Verbose, includes private notes. Lives at `~/team-planner/members/<team-member-key>/1on1/YYYY-MM-DD.md`.

```markdown
# 1:1 with <team-member> — YYYY-MM-DD

**Duration:** <if known> | **Next 1:1:** <auto-computed date>

## Summary
<3-5 line auto-drafted summary; <team-lead> edits before commit>

## Discussion
### <Topic 1>
- They said: <paraphrase>
- I said: <paraphrase>
- Outcome: <note / decision / action>

### <Topic 2>
- ...

## Decisions
- <decision> — <one-line context>

## Action items
| # | Action | Owner | Due | Status | Jira |
|---|---|---|---|---|---|
| 1 | <text> | <team-lead> / <team-member> | YYYY-MM-DD | Open | <KEY or "—"> |

## Risks captured
- <risk text> — promoted to RAID? <R-XXX or "no">

## Feedback (PRIVATE — <team-lead> only)
- Delivered: <what <team-lead> said>
- Received: <what they said>
- <team-lead>'s read: <private observation>

## Wins delivered
- <one-line> — cite: <PR / Jira KEY>

## Carry-over for next 1:1
- [ ] <unresolved topic>
- [ ] <action still open at meeting end>

## Private notes (PRIVATE — <team-lead> only)
- <impressions, concerns>
- <things to verify or follow up on outside this 1:1>

## Source artifacts
- Confluence page: <URL>
- Prep doc snapshot: see `_last.md`
- Jira tickets discussed: <KEY, KEY, ...>
- PRs discussed: <repo#num, repo#num>
```

---

## Confluence published page

Sanitized. The same page that was created in prep is **updated in place**. On publish, page restrictions are removed (default org-wide visibility). Sections mirror the prep-template structure. The published page is for the team and the org — it is organized by **work state** so each item has exactly one home, it must not duplicate information across sections, and it must not include private working state (carry-overs to the next 1:1, publish-status footers, "for-<team-lead>" reminders). It must **not** carry editorial / meta notes either — section-purpose annotations, authoring placeholders, or prep/publish footers. Any `<!-- ... -->` note in the block below is authoring guidance for you; the page itself holds only headings and content bullets.

```markdown
# 1:1 with <team-member> — YYYY-MM-DD

## Done
<!-- Work completed this cycle: merged / tested / closed / delivered. Approved wins are the highlight-worthy Done items — no separate Wins or Review section. -->
- <what it is + merged/tested/closed/delivered> (link: <PR / Jira>)

## In progress
<!-- Work actively underway: work-in-progress / in review / blocked. A blocked item is still in-progress work — state the blocker inline. Includes member-owned commitments (owner inline, no due date). -->
- <what it is + WIP / in review / blocked (blocker inline)> — <owner if a member commitment>

## Planned (not started)
<!-- Work committed for the current/next sprint whose status is not started, plus forward strategic direction and deferrals. <team-lead>'s own action items never appear here — local archive only. -->
- <what it is + not started>
- <strategic direction / deferral, no owner needed>

## Decisions
<!-- Decisions reached in the meeting. A decision is not a work item. Omit the whole section if there were none. -->
- <decision> — <one-line context>

## Meeting notes
<!-- Discussion-only narrative with no work-state home — risk discussion, design rationale, cross-cutting concerns. Omit entirely if empty; never an empty stub. -->
- <bullet captured live — paraphrase, no he-said/I-said>

## Next 1:1
<auto-computed date>
```

**Single-home rule (no duplication across sections — strict, keyed off work state):**
- **The three work-state sections are mutually exclusive.** A piece of work has exactly one current status, so it lands in exactly one of **Done** / **In progress** / **Planned (not started)**. Map the item's status word to its section:

  | Status word | Section |
  |---|---|
  | done / merged / tested / closed / delivered | **Done** |
  | work-in-progress / in review / blocked | **In progress** |
  | not started | **Planned (not started)** |

- **Status-first, self-contained, work-focused phrasing.** Each bullet = plain-language description of the work (understandable without opening the ticket) + its status word; ticket/PR keys are trailing references only. Do **not** describe planning mechanics (sprint/milestone moves, re-estimation, "needs a commit/sequencing call", capacity math).
- Each fact, commitment, or item appears **exactly once** on the page. Before finalizing, walk every section and remove any topic that appears twice; then walk again at the **ticket-key level** — every Jira key / PR link / epic appears at most once.
- **No `Carry-over` section.** A carried action from the previous 1:1 is reclassified by its **current** status into Done / In progress / Planned. Loop-closing across cycles lives in the local archive only.
- **No `Action items` section.** Member-owned commitments are work items — they live under **In progress** or **Planned (not started)** per their status (owner inline, no due date). <team-lead>-owned actions → **local archive only**, never on the storefront.
- **Decisions captured in the meeting → `Decisions` only.** Do not also restate the decision as a work bullet. If the decision changed a work item's state, the work item carries its own state bullet and the decision row references it by key without re-describing the work.
- **Meeting notes is for narrative that has no work-state home** — risk discussion, design rationale, cross-cutting concerns. If a Meeting-notes bullet duplicates a work-state bullet or a Decision, delete it. Meeting notes is the **last** section to fill — keep only what is genuinely unique. Omit if empty.
- **Consolidate related sub-topics.** When two bullets share the same root (e.g., "reset pre/post handling" and "reset state machine"), merge them into a single entry in the single correct section, classified by the thread's overall state. Never split one topic across two bullets to "give it more weight".

**Sanitization rules** (what is excluded from the published Confluence page):
- "Feedback" section (entirely private).
- "Private notes" section (entirely private).
- "Carry-over to next 1:1" — that is local-archive-only. The published page is not a working tool for the next cycle.
- Publish-status footers (e.g. `*Published <date> — visibility: anyone in the organization*`). Visibility is conveyed by Confluence permissions, not page text.
- **Editorial / meta notes of any kind** — section-purpose annotations (e.g. `*(populated live during the meeting…)*`, `*(discussion-only…)*`), authoring placeholders, "edited live" hints, and prep footers (e.g. `*Prepared <date> — shared with <team-member>…*`). The page carries only headings and content bullets.
- Risk text — only mentioned if promoted to RAID, with a one-line summary.
- Live `private:`-tagged entries — never reach the page.
- Task IDs from `task-manager`.
- Win rationale (only the one-line win + artifact link).
- Spillover/capacity/reassignment decisions, reviewer-bandwidth musings, "Anything from you"-style filler.
- **PTO / day-off entries** — kept only in the day-off log and (if relevant) the local archive. Never on the storefront (a forward PTO heads-up affecting sprint capacity may appear under **Planned (not started)**).
- **<team-lead>'s own action items** (anything where `owner: <team-lead>`) — kept only in the local archive. The storefront's work-state sections show member-owned items only.
- **Sprint / milestone mechanics that are irrelevant to the member** — e.g., sprint renames, milestone retirements by automation, planning-tool quirks, sprint-board hygiene. These belong in the team-planner snapshot, not on a member's 1:1 page. Only mention milestone/sprint context when it directly affects the member's owned work (a due date, a scope change, a blocker).

---

## `<team-member-key>.md` history entry

Prepended to the `## 1:1 History` section (latest first) in the member file owned by `team-planner`.

```markdown
### YYYY-MM-DD
- **Summary:** <one line>
- **Top actions:**
  - <action 1>
  - <action 2>
  - <action 3>
- **Confluence:** <page URL>
- **Archive:** [<YYYY-MM-DD.md>](./<team-member-key>/1on1/YYYY-MM-DD.md)
```

If `## 1:1 History` section does not yet exist in the member file, create it immediately after the existing "Notes" section (or at the end of the file if Notes is absent).
