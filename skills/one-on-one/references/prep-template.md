# Prep Templates

Used by capability 1 (Prep) of the `one-on-one` skill. Two outputs per prep: a verbose local doc (`_next.md`) and a sanitized Confluence draft page. A third file, the per-member running record (`running.md`), is maintained continuously between meetings (capability 0) and consumed at prep.

---

## Running record (`running.md`)

Per-member rolling log <team-lead> updates throughout the week via `log 1:1 with <team-member>: <text>`. Private to <team-lead>; never reaches Confluence. Appended to continuously; consumed and reset at each prep. Lives at `~/team-planner/members/<team-member-key>/running.md`.

```markdown
# Running record — <team-member>

Quick notes logged between 1:1s via `log 1:1 with <team-member>: <text>`.
Consumed into the agenda and reset at each prep. Private to <team-lead>.

_Last consumed: <prep-date or "never">_

## Entries
- YYYY-MM-DD — [topic] <text>
- YYYY-MM-DD — [win] <text + artifact ref>
- YYYY-MM-DD — [action] <text>
- YYYY-MM-DD — [followup] <text>
- YYYY-MM-DD — [private] <text>
- YYYY-MM-DD — <untagged note>
```

**Reset form** (written at prep, after entries are folded into `_next.md`):

```markdown
# Running record — <team-member>

Quick notes logged between 1:1s via `log 1:1 with <team-member>: <text>`.
Consumed into the agenda and reset at each prep. Private to <team-lead>.

_Last consumed: <prep-date>_

## Entries
```

---

## Local `_next.md`

Verbose, private. <team-lead>'s eyes only. Lives at `~/team-planner/members/<team-member-key>/_next.md`.

```markdown
# 1:1 with <team-member> — <next-meeting-date>

**Cadence:** weekly | **Last meeting:** <prev-date> | **Days since:** <N>
**Upcoming PTO:** <list or "none">
**Confluence page:** <URL>

## Carry-over from <prev-date>
- [ ] <open action item> — owner: <team-lead> or <team-member> — due: <date>
- [ ] <carry-over topic>
- [ ] <thing <team-lead> said they would follow up on>

## From running record (logged since <prev-date>)
*(raw entries consumed from `running.md` this prep — routed into the sections below; kept here for traceability)*
- <YYYY-MM-DD> [topic] <text> → agenda
- <YYYY-MM-DD> [win] <text> → Wins (verify resolution before promoting)
- <YYYY-MM-DD> [action] <text> → Owed by me / commitment
- <YYYY-MM-DD> [private] <text> → Private notes

## Big picture (PRIVATE)
- **Milestone:** MS<N> — <X> days remaining, their load <Y>d remaining, RAG <color>
- **Sprint:** <sprint name>, day <N> of 14
- **Subsystem focus:** <PCIe / SE / D2D / Infra>

## Current work (this sprint) (PRIVATE — raw context for the page sections)
| Status | Key | Summary | Est | Spent | Rem | Flag |
|---|---|---|---|---|---|---|
| In progress | KEY | ... | Xd | Xd | Xd | <stale / no progress / no time logged> |
| In Review | KEY | ... | Xd | Xd | Xd | PR <url>, reviewer <team-member> |
| Backlog | KEY | ... | Xd | — | Xd | <flag if over capacity> |
| Blocked | KEY | ... | Xd | — | Xd | <reason, days blocked> |

## Wins (proposed — PRIVATE rationale)
1. **<one-line win>** — cite: <PR URL / Jira KEY / review URL>
   - *Why this matters:* <team-lead>-private rationale: <specific context>

## Done (page-bound — completed this cycle, mirrors the page's Done section)
- <what shipped/closed/delivered in the last 7 days — merged/tested/closed — incl. approved wins>
- <e.g.: "Warm-reset callback handling — merged [ep#248]">

## In progress (page-bound — mirrors the page's In progress section)
- <work actively underway — WIP / in review / blocked (blocker inline) — incl. member-owned commitments>

## Planned / not started (page-bound — mirrors the page's Planned section)
- <committed for current/next sprint, not started>
- <forward strategic direction / deferral (one line)>

## Owed by me (from task-manager) (PRIVATE)
- [ ] T-XXX: Review PR <repo>#<num> — <title> — pending <N>d
- [ ] T-XXX: <task tagged with their name>

## RAID rows naming them (PRIVATE)
- R-XXX | <title> | RAG: <color> | <one-line context>

## Risks / things to flag (PRIVATE)
- <e.g., over-committed by Nd vs remaining capacity>
- <e.g., D2D-101 blocked since Jun 2 — what is the unblock plan?>
- <e.g., 3 stories with no time logged this sprint — progress visibility>

## Meeting notes (proposed agenda — edited live)
1. <highest-priority topic with concrete trigger>
2. <topic from member file 1:1 Topics queue>
3. <career / growth from Goals section>

## Questions for them (PRIVATE)
- <e.g., what is blocking progress on SE Intr cluster?>
- <e.g., do you want help on Y?>

## Private notes (<team-lead> only — never reaches Confluence)
- <impressions, concerns, feedback drafts>
- <conversation context to remember>
- <things to verify with their work before raising>

## Live notes (filled during meeting)
<empty placeholder — populated in live mode>
```

---

## Confluence draft page

Sanitized, member-facing. Created in the member's personal space, in folder `<team-lead>:<team-member>`, with title `<team-lead>:<team-member> - <next-date>`. **Restrictions: <team-lead> + member** during prep — the member can read the agenda but only <team-lead> can edit it (use lead-only restrictions only if <team-lead> explicitly asks for a private prep page).

**Voice rules** (apply to every line on this page):
- **Status-first and self-contained.** Each bullet states **what the work is** in plain language the reader grasps *without opening the ticket*, then **where it stands** with an explicit status word: **not started / work-in-progress / blocked / in review / merged / tested / done**. Ticket/PR keys are trailing bracketed references, never the subject of the sentence.
- **Work, not planning.** Describe the work and its state — never the planning mechanics around it (sprint/milestone moves, re-estimation, "needs a commit/sequencing call", what should be planned, sprint/PTO capacity math). Those stay in `_next.md`.
- **Objective tone, third-person or imperative.** Never write from <team-lead>'s perspective. No "I will", "I'll personally", "I'd like to". Use "transition X", "confirm Y", "agree Z" or "<team-member> to do X".
- **No filler topics.** Do not add habitual prompts like "Anything from you?", "Reviewer bandwidth — is the review pile the blocker?", "How are you feeling?". The member will raise what they need. Only include topics with a concrete trigger.
- **RAID items: orient toward the member.** Do not paste full RAID descriptions or scoring. Translate into one line of what changed for *them* (e.g. "MS6.5 no longer exists — treat scope as MS6 + MS7") and omit the internal mechanics (escalation paths, P/I scores, owners, due dates). Those stay in `_next.md`.
- **No lead-only considerations.** Spillover decisions, capacity calculus, reassignment plans, and reviewer logistics stay local. The page should only contain what the member needs to walk in informed.
- **No editorial / meta notes on the page.** The storefront contains only headings and content bullets — never section-purpose annotations (e.g. "*(populated live during the meeting — discussion topics only…)*"), authoring placeholders, status hints about how a section is filled, or prep/publish footers (e.g. "*Prepared `<today>` — shared with `<team-member>`…*"). Lifecycle state (prep vs. live vs. published) is conveyed by Confluence permissions and page history, not by text on the page. Any `*(…)*` parenthetical in these templates is authoring guidance for you — do **not** copy it onto the page.

```markdown
# 1:1 with <team-member> — <next-meeting-date>

## Done
- <completed-this-cycle work — what it is + merged/tested/closed/delivered — link to PR / Jira KEY> *(approved wins are the highlight-worthy Done items; no separate Wins section)*

## In progress
- <work actively underway — what it is + work-in-progress / in review / blocked (state the blocker inline) — refs>

## Planned (not started)
- <work committed for the current/next sprint, status not started — what it is + "not started" — refs>
- <forward strategic direction / deferral — one line>

## Decisions
- <decision reached in the meeting — one-line context> *(omit the whole section if none)*

## Meeting notes
- <discussion-only topic with a concrete trigger — edited live> *(omit the whole section if empty)*
```

**Routing rule — every work item maps by its status word to exactly one of the three work-state sections:**

| Status word | Section |
|---|---|
| done / merged / tested / closed / delivered | **Done** |
| work-in-progress / in review / blocked | **In progress** |
| not started | **Planned (not started)** |

**Sanitization rules** (what goes into the Confluence page from `_next.md`):
- Completed-this-cycle work (merged PRs, closed tickets, delivered reviews) **and approved wins** → **Done** (one line + artifact link; win rationale stays local). There is no separate `Wins` or `Review of last week` section.
- In-flight work (WIP / in review / blocked), including member-owned commitments → **In progress** (owner allowed inline, **no due date on the storefront**, no RAG flags, no spent/remaining).
- Committed-but-not-started work + forward strategic direction / deferrals → **Planned (not started)**.
- A carried action from the previous 1:1 → reclassify by its **current** status into Done / In progress / Planned. There is no `Carry-over` section on the storefront.
- Decisions reached in the meeting → **Decisions** (one line each). Do not also restate as a work bullet.
- Discussion-only topics with a concrete trigger → **Meeting notes** (edited live; drop filler/habit prompts). Omit the section if empty.
- **No overlap:** every Jira key / PR link / epic appears **at most once** across the whole page. If an item could fit two sections, its status word decides — it has exactly one.
- RAID items → one-line member-facing heads-up inside the relevant work-state section, never the full row.
- **Excluded:** "Risks / things to flag", "Private notes", win rationale, RAID detail, spillover/capacity/reassignment decisions, reviewer-bandwidth prompts, "Anything from you"-style filler, T-IDs from `task-manager`, **PTO / upcoming day-off entries** (kept in the day-off log only; a forward PTO heads-up affecting sprint capacity → **Planned (not started)**), **<team-lead>-owned action items** (anything where `owner: <team-lead>` — kept in the local archive only), **sprint / milestone mechanics irrelevant to the member** (sprint renames, milestone retirements by automation, planning-tool quirks — only surface when they directly affect the member's owned work).
