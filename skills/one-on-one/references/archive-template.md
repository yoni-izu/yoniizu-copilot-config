# Archive Templates

Used by capability 3 (Post) of the `one-on-one` skill. Two outputs per meeting: a verbose local archive and a sanitized published Confluence page.

---

## Local archive

Verbose, includes private notes. Lives at `~/team-planner/members/<name>/1on1/YYYY-MM-DD.md`.

```markdown
# 1:1 with <Name> — YYYY-MM-DD

**Duration:** <if known> | **Next 1:1:** <auto-computed date>

## Summary
<3-5 line auto-drafted summary; Yoni edits before commit>

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
| 1 | <text> | Yoni / <Name> | YYYY-MM-DD | Open | <KEY or "—"> |

## Risks captured
- <risk text> — promoted to RAID? <R-XXX or "no">

## Feedback (PRIVATE — Yoni only)
- Delivered: <what Yoni said>
- Received: <what they said>
- Yoni's read: <private observation>

## Wins delivered
- <one-line> — cite: <PR / Jira KEY>

## Carry-over for next 1:1
- [ ] <unresolved topic>
- [ ] <action still open at meeting end>

## Private notes (PRIVATE — Yoni only)
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

Sanitized. The same page that was created in prep is **updated in place**. On publish, page restrictions are removed (default org-wide visibility). Sections mirror the prep-template structure. The published page is for the team and the org — it must not duplicate information across sections, and it must not include private working state (carry-overs to the next 1:1, publish-status footers, "for-Yoni" reminders).

```markdown
# 1:1 with <Name> — YYYY-MM-DD

## Carry-over from <prev-date>
*(Omit this section entirely on the first 1:1 with this member, or whenever there is no prior 1:1 to carry from.)*
- [x] <closed action> — outcome
- [ ] <still-open action> — owner, due

## Wins
- <one-line win> (link: <PR / Jira>)

## Review of last week
- <what shipped, closed, transitioned — one line each>

## Decisions
- <decision> — <one-line context>

## Looking Ahead
*(Single forward-looking section on the storefront. Includes the **member's** owned-and-dated commitments — each as a bullet with owner + due — alongside strategic direction and deferrals. There is **no separate Action items table** on the published page. **Yoni's own action items never appear on the storefront** — they live only in the local archive's Action items table.)*
- <Mateusz-owned commitment with `owner` + `due YYYY-MM-DD`>
- <strategic direction / deferral, no owner needed>

## Meeting notes
*(discussion-only — context, questions, topics raised that did not become commitments; omit entirely if all live capture was forward-looking and folded into Looking Ahead.)*
- <bullet captured live — paraphrase, no he-said/I-said>

## Next 1:1
<auto-computed date>
```

**Single-home rule (no duplication across sections — strict):**
- Each fact, commitment, or item appears **exactly once** on the page. Before finalizing, scan every section and remove any topic that appears twice.
- **No `Action items` section on the storefront.** Member-owned commitments → **Looking Ahead** (with `owner` + `due`). Yoni-owned actions → **local archive only**, never on the storefront. The local archive keeps the full `Action items` table for both Yoni and the member.
- Strategic direction or deferrals without an owner → **Looking Ahead** as well (single forward-looking section).
- Closed work that landed during the cycle → **Wins** (with artifact link) **or** **Review of last week** (one-line summary), not both. If a single item naturally fits both, keep the artifact-linked Wins entry and drop the Review echo.
- Decisions captured in the meeting → **Decisions** only. Do not paraphrase the same decision under Looking Ahead or as a separate Meeting-notes bullet.
- **Meeting notes is for narrative that has no home elsewhere** — risk discussion, design rationale, cross-cutting concerns. If a Meeting-notes bullet duplicates a Decision or a Looking Ahead item, delete it. After all other sections are drafted, Meeting notes is the **last** section to fill — keep only what is genuinely unique.
- **Consolidate related sub-topics.** When two bullets share the same root (e.g., "reset pre/post handling" and "reset state machine proposal"), merge them into a single entry in the single correct section. Never split one topic across two bullets to "give it more weight".
- Carry-over from the previous 1:1 → **Carry-over from `<prev-date>`** only (and omit entirely on a first 1:1).

**Sanitization rules** (what is excluded from the published Confluence page):
- "Feedback" section (entirely private).
- "Private notes" section (entirely private).
- "Carry-over to next 1:1" — that is local-archive-only. The published page is not a working tool for the next cycle.
- Publish-status footers (e.g. `*Published <date> — visibility: anyone in the organization*`). Visibility is conveyed by Confluence permissions, not page text.
- Risk text — only mentioned if promoted to RAID, with a one-line summary.
- Live `private:`-tagged entries — never reach the page.
- Task IDs from `task-manager`.
- Win rationale (only the one-line win + artifact link).
- Spillover/capacity/reassignment decisions, reviewer-bandwidth musings, "Anything from you"-style filler.
- **PTO / day-off entries** — kept only in the day-off log and (if relevant) the local archive. Never on the storefront.
- **Yoni's own action items** (anything where `owner: Yoni`) — kept only in the local archive. Storefront `Looking Ahead` shows member-owned items only.
- **Sprint / milestone mechanics that are irrelevant to the member** — e.g., sprint renames, milestone retirements by automation, planning-tool quirks, sprint-board hygiene. These belong in the team-planner snapshot, not on a member's 1:1 page. Only mention milestone/sprint context when it directly affects the member's owned work (a due date, a scope change, a blocker).

---

## `<name>.md` history entry

Prepended to the `## 1:1 History` section (latest first) in the member file owned by `team-planner`.

```markdown
### YYYY-MM-DD
- **Summary:** <one line>
- **Top actions:**
  - <action 1>
  - <action 2>
  - <action 3>
- **Confluence:** <page URL>
- **Archive:** [<YYYY-MM-DD.md>](./<name>/1on1/YYYY-MM-DD.md)
```

If `## 1:1 History` section does not yet exist in the member file, create it immediately after the existing "Notes" section (or at the end of the file if Notes is absent).
