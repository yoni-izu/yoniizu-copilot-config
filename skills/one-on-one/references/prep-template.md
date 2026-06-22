# Prep Templates

Used by capability 1 (Prep) of the `one-on-one` skill. Two outputs per prep: a verbose local doc (`_next.md`) and a sanitized Confluence draft page.

---

## Local `_next.md`

Verbose, private. Yoni's eyes only. Lives at `~/team-planner/members/<name>/_next.md`.

```markdown
# 1:1 with <Name> — <next-meeting-date>

**Cadence:** weekly | **Last meeting:** <prev-date> | **Days since:** <N>
**Upcoming PTO:** <list or "none">
**Confluence page:** <URL>

## Carry-over from <prev-date>
- [ ] <open action item> — owner: <Yoni/Name> — due: <date>
- [ ] <carry-over topic>
- [ ] <thing Yoni said he would follow up on>

## Big picture (PRIVATE)
- **Milestone:** MS<N> — <X> days remaining, their load <Y>d remaining, RAG <color>
- **Sprint:** <sprint name>, day <N> of 14
- **Subsystem focus:** <PCIe / SE / D2D / Infra>

## Current work (this sprint) (PRIVATE — raw context for the page sections)
| Status | Key | Summary | Est | Spent | Rem | Flag |
|---|---|---|---|---|---|---|
| In progress | KEY | ... | Xd | Xd | Xd | <stale / no progress / no time logged> |
| In Review | KEY | ... | Xd | Xd | Xd | PR <url>, reviewer <name> |
| Backlog | KEY | ... | Xd | — | Xd | <flag if over capacity> |
| Blocked | KEY | ... | Xd | — | Xd | <reason, days blocked> |

## Wins (proposed — PRIVATE rationale)
1. **<one-line win>** — cite: <PR URL / Jira KEY / review URL>
   - *Why this matters:* <Yoni's private context, e.g. "first major refactor they shipped solo">

## Review of last week (page-bound — sanitized recap)
- <what shipped, closed, transitioned in the last 7 days — broader than Wins>
- <e.g.: "3 segregation child PRs merged, IPPCIESW-490 host-FW bug fix landed">

## Looking Ahead (page-bound)
- <top 2-3 priorities for the next 1-2 sprints>
- <near-term carry to next sprint / next milestone>
- <items being deferred (one line)>

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

## Private notes (Yoni only — never reaches Confluence)
- <impressions, concerns, feedback drafts>
- <conversation context to remember>
- <things to verify with their work before raising>

## Live notes (filled during meeting)
<empty placeholder — populated in live mode>
```

---

## Confluence draft page

Sanitized, member-facing. Created in the member's personal space, in folder `Yoni:<Name>`, with title `Yoni:<Name> - <next-date>`. **Restrictions: Yoni + member** during prep — the member can read the agenda but only Yoni can edit it (use Yoni-only restrictions only if Yoni explicitly asks for a private prep page).

**Voice rules** (apply to every line on this page):
- **Status-first and self-contained.** Each bullet states **what the work is** in plain language the reader grasps *without opening the ticket*, then **where it stands** with an explicit status word: **not started / work-in-progress / blocked / in review / merged / tested / done**. Ticket/PR keys are trailing bracketed references, never the subject of the sentence.
- **Work, not planning.** Describe the work and its state — never the planning mechanics around it (sprint/milestone moves, re-estimation, "needs a commit/sequencing call", what should be planned, sprint/PTO capacity math). Those stay in `_next.md`.
- **Objective tone, third-person or imperative.** Never write from Yoni's perspective. No "I will", "I'll personally", "I'd like to". Use "transition X", "confirm Y", "agree Z" or "<Name> to do X".
- **No filler topics.** Do not add habitual prompts like "Anything from you?", "Reviewer bandwidth — is the review pile the blocker?", "How are you feeling?". The member will raise what they need. Only include topics with a concrete trigger.
- **RAID items: orient toward the member.** Do not paste full RAID descriptions or scoring. Translate into one line of what changed for *them* (e.g. "MS6.5 no longer exists — treat scope as MS6 + MS7") and omit the internal mechanics (escalation paths, P/I scores, owners, due dates). Those stay in `_next.md`.
- **No Yoni-private considerations.** Spillover decisions, capacity calculus, reassignment plans, and reviewer logistics stay local. The page should only contain what the member needs to walk in informed.
- **No editorial / meta notes on the page.** The storefront contains only headings and content bullets — never section-purpose annotations (e.g. "*(populated live during the meeting — discussion topics only…)*"), authoring placeholders, status hints about how a section is filled, or prep/publish footers (e.g. "*Prepared `<today>` — shared with `<Name>`…*"). Lifecycle state (prep vs. live vs. published) is conveyed by Confluence permissions and page history, not by text on the page. Any `*(…)*` parenthetical in these templates is authoring guidance for you — do **not** copy it onto the page.

```markdown
# 1:1 with <Name> — <next-meeting-date>

## Carry-over from <prev-date>
- [ ] <open action> — owner: <Yoni/Name>

## Wins
- <one-line win cite — link to PR / Jira KEY> *(only items Yoni approved)*

## Review of last week
- <what was done: merged PRs, closed tickets, status changes, decisions delivered — one line each>

## Looking Ahead
- <top in-flight items and what's coming next — one line each>

## Meeting notes
- <proposed agenda bullet — edited live during the meeting>
- <proposed agenda bullet>
```

**Sanitization rules** (what goes into the Confluence page from `_next.md`):
- Carry-over actions → **Carry-over** ✓ (member-owned items only; Yoni-owned actions stay in the local archive)
- Approved wins only → **Wins** (one line + artifact link; rationale stays local)
- Recent activity (merged PRs, closed tickets, status changes since last 1:1) → **Review of last week**
- Current work + plan + sprint goal **and member-owned commitments with `owner` + `due`** → **Looking Ahead** (one line per item, no RAG flags, no spent/remaining, no Yoni-private spillover/capacity decisions). There is **no separate Action items section on the storefront** — Looking Ahead is the single forward-looking section, mixing strategic direction with dated member commitments.
- Talking points → **Meeting notes** (proposed bullets, edited live; drop filler/habit prompts, keep only items with a concrete trigger)
- **Avoid overlap between Looking Ahead and Meeting notes.** If a meeting bullet is a forward commitment, move it to Looking Ahead and drop it from Meeting notes. Meeting notes is for discussion topics (questions, context, things that didn't become commitments)
- RAID items → one-line member-facing heads-up inside the relevant section, never the full row
- **Excluded:** "Risks / things to flag", "Private notes", win rationale, RAID detail, spillover/capacity/reassignment decisions, reviewer-bandwidth prompts, "Anything from you"-style filler, T-IDs from `task-manager`, **PTO / upcoming day-off entries** (kept in the day-off log only), **Yoni-owned action items** (anything where `owner: Yoni` — kept in the local archive only), **sprint / milestone mechanics irrelevant to the member** (sprint renames, milestone retirements by automation, planning-tool quirks — only surface when they directly affect the member's owned work).
