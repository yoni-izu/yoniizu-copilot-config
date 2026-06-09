# Prep Templates

Used by capability 1 (Prep) of the `team-weekly` skill. Two outputs per prep: a verbose local doc (`_next.md`) and a sanitized Confluence draft page.

---

## Local `_next.md`

Verbose, private. Yoni's eyes only. Lives at `~/team-planner/team-weekly/_next.md`.

```markdown
# HSI FW Team Weekly — <next-meeting-date>

**Sprint:** <sprint name>, day <N> of 14 (ends <date>) | **Milestone:** MS<N> (effective end <date>)

---

## Announcements
- [ ] <new process, policy change, or carry-over action — owner: <name>>

---

## Past — what shipped (last 7 days)
- <one-line win> — cite: <PR URL / JIRA KEY>
- <one-line win> — cite: <PR URL / JIRA KEY>

---

## Present — sprint health

**Health:** <qualitative framing, e.g. "heavily loaded, confidence low">

**Sprint focus:**
- <focus area 1>
- <focus area 2>
- <focus area 3>

**Blocked:**
- **<KEY>** — <one-line: what, blocker, owner>

---

## Future

**Milestone MS<N>** — ~<N> working days remaining. <one-line risk narrative>

**Dependencies:**
- <dep + date>
- <dep + date>

---

## Global updates

- <update — one line>
- <update — one line>

**Yoni-private (do NOT present to team):**
- <personal action items from staff meetings>
- <general FW org context that doesn't need team airtime>

---

## Topics
1. <topic — one-line context>
2. <topic — one-line context>
3. <topic — one-line context>

---

## Open discussion (proposed seeds — PRIVATE)
- <prompt: e.g., "what would you cut from MS6 if we had to drop 1 week?">

---

## Risks / things to flag (PRIVATE — Yoni only)
- <e.g., over-committed vs remaining capacity>
- <e.g., RAID row still unresolved>

---

## Private notes (Yoni only — never reaches Confluence)
- <impressions, concerns, coaching notes>

---

## Live notes (filled during meeting)
*(empty placeholder — populated in live mode)*
```

---

## Confluence draft page

Sanitized, team-facing. Created in SWENG folder `695369790` with title `HSI FW Weekly — <next-date>`. **Restrictions: Yoni only** during prep.

**Voice rules** (apply to every line on this page):
- **Objective tone.** No "I'd like to" / "I plan to". Use "agree X", "confirm Y", "<name> to do Z".
- **No filler prompts.** Don't include habitual "anything to add?" lines. Only include topics with a concrete trigger.
- **Wins are evidence.** Every win cites a PR, Jira key, merged feature, lab run.
- **Risks: oriented to action.** One line per risk: what changed, what's the next step. No RAID scoring or owners-list on the team page (those stay in RAID).
- **No private framing.** No capacity calculus, morale notes, individual coaching considerations.

```markdown
# HSI FW Weekly — <meeting-date>

## Announcements
- [ ] <new process, policy change, or carry-over action>

## Past — what shipped (last 7 days)
- <one-line win>
- <one-line win>

## Present — sprint health
**Health:** <qualitative framing>

**Sprint focus:**
- <focus area 1>
- <focus area 2>

**Blocked:**
- <KEY> — <one-line: what, blocker, owner>

## Future
**Milestone MS<N>** — ~<N> working days remaining. <one-line risk narrative>

**Dependencies:**
- <dep + date>

## Global updates
- <update — one line>
- <update — one line>

## Topics
1. <topic — one-line context>
2. <topic — one-line context>

## Notes / decisions / actions
*(filled during meeting)*
```

**Sanitization rules** (what goes into the Confluence page from `_next.md`):
- Announcements ✓ (only team-facing actions)
- Wins ✓ (brief, no internal rationale)
- Sprint health → qualitative only (no raw effort-day numbers, utilization %, or capacity calculus)
- Sprint focus ✓ (what the sprint concentrates on — 2-3 bullet points)
- Blocked list ✓ (no internal escalation notes)
- Future → milestone one-liner + dependencies only (no planning conclusions, no rest-of-sprint task assignments)
- Global updates ✓ (one-liner; **never cite staff meeting as source**; **exclude Yoni-personal action items**)
- Topics ✓ (with one-line context each)
- PTO → **omit by default** unless Yoni explicitly requests
- **Excluded:** Agenda block (redundant), per-IP scoreboard table, "Readiness" section (folds into Present), "Open discussion" seeds, "Risks / things to flag", "Private notes", per-person capacity numbers, planning churn detail, RAID P/I scores, Yoni-personal staff action items

**Anti-repetition rule:** Each fact appears in exactly ONE section. If a topic is in Past (as a win), do NOT repeat the same detail in Present or Topics. Topics list should reference items briefly — context lives in the section where they first appear.
