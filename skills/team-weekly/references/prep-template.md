# Prep Templates

Used by capability 1 (Prep) of the `team-weekly` skill. Two outputs per prep: a verbose local doc (`_next.md`) and a sanitized Confluence draft page.

---

## Local `_next.md`

Verbose, private. <team-lead>'s eyes only. Lives at `~/team-planner/team-weekly/_next.md`.

```markdown
# HSI FW Team Weekly — <next-meeting-date>

**Sprint:** <sprint name>, day <N> of 14 (ends <date>) | **Milestone:** MS<N> (effective end <date>)

---

## Announcements
- [ ] <new process, policy change, or carry-over action — owner: <team-member>>

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

**Private to <team-lead> (do NOT present to team):**
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

## Risks / things to flag (PRIVATE — <team-lead> only)
- <e.g., over-committed vs remaining capacity>
- <e.g., RAID row still unresolved>

---

## Private notes (<team-lead> only — never reaches Confluence)
- <impressions, concerns, coaching notes>

---

## Live notes (filled during meeting)
*(empty placeholder — populated in live mode)*
```

---

## Confluence draft page

Sanitized, team-facing. Created in SWENG folder `695369790` with title `HSI FW Weekly — <next-date>`. **Restrictions: <team-lead> only** during prep.

**Voice rules** (apply to every line on this page):
- **Objective tone.** No "I'd like to" / "I plan to". Use "agree X", "confirm Y", or "<team-member> to do Z".
- **No filler prompts.** Don't include habitual "anything to add?" lines. Only include topics with a concrete trigger.
- **Wins are evidence.** Every win cites a PR, Jira key, merged feature, lab run.
- **Ticket context is mandatory.** Never present a bare Jira key. Always pair the key with the story it represents, in plain language — e.g. "deferred link-training validation ([IPPCIESW-399])", not "([IPPCIESW-399])". No one remembers numbers; the reader must understand the item without opening the link. The same applies to PRs where the title isn't self-evident.
- **Domain-first layout.** Each IP is its own top-level section (`##`): **Cross-cutting/shared**, **PCIe**, **D2D**, **SE** (only include domains that have content). Inside each domain, use **Done / In progress / Next** as `###` sub-headings. Cross-IP items (e.g. shared PHY API, the interrupt-policy layer, PHY-FW-from-flash) go under Cross-cutting; single-IP items go under their domain. This lets each owner narrate their IP end-to-end.
- **Risks: oriented to action.** One line per risk: what changed, what's the next step. No RAID scoring or owners-list on the team page (those stay in RAID).
- **No private framing.** No capacity calculus, morale notes, individual coaching considerations.

**Section order (fixed):** Announcements → Global updates → Cross-cutting/shared → PCIe → D2D → SE → Cross-team items to discuss → Notes / decisions / actions. Announcements and Global updates lead so the team gets program-level context first; then each domain is walked end-to-end (Done → In progress → Next as `###` sub-headings). No "sprint health" section by default.

```markdown
# HSI FW Weekly — <meeting-date>

## Announcements
- [ ] <new process, policy change, or carry-over action>

## Global updates
- <program/org update — one line; never cite the staff meeting as source>
- <program/org update — one line>

## Cross-cutting / shared
### Done
- <story in plain language> (<KEY> — <what it is>) — <evidence: PR/merge/lab>
### In progress
- <story in plain language> (<KEY> — <what it is>)
### Next
- <planned item in plain language> (<KEY> — <what it is>)

## PCIe
### Done
- <story in plain language> (<KEY> — <what it is>)
### In progress
- <story in plain language> (<KEY> — <what it is>)
### Next
- <planned item, or "—" if none>

## D2D
### Done
- <story in plain language> (<KEY> — <what it is>)
### In progress
- <story in plain language> (<KEY> — <what it is>)
### Next
- <planned item in plain language> (<KEY> — <what it is>)

## SE
### Done
- <story in plain language> (<KEY> — <what it is>)
### In progress
- <story in plain language> (<KEY> — <what it is>)
### Next
- <planned item in plain language> (<KEY> — <what it is>)

## Cross-team items to discuss
1. <topic — one-line context that interests everyone, not IP-specific minutiae>

## Notes / decisions / actions
*(filled during meeting)*
```

**Domain-first layout (why):** each IP (Cross-cutting / PCIe / D2D / SE) is its own `##` section, and **Done / In progress / Next** are `###` sub-headings inside it. This lets each owner narrate their IP end-to-end (what shipped → what's active → what's next) without jumping around the page. Only include the domains that have content; within a domain, keep an empty bucket as `—` rather than deleting the sub-heading, so the past/present/future rhythm is visible.

**Sanitization rules** (what goes into the Confluence page from `_next.md`):
- Announcements ✓ (only team-facing actions)
- Global updates ✓ (one-liner; **never cite staff meeting as source**; **exclude <team-lead>'s personal action items**)
- Per-domain Done / In progress / Next ✓ (each item carries ticket context; no internal rationale; no per-person capacity numbers)
- Cross-team items ✓ (favor interest-everyone topics; with one-line context each)
- PTO → **omit by default** unless <team-lead> explicitly requests
- **Excluded:** "Sprint health" section, per-IP scoreboard table, "Open discussion" seeds, "Risks / things to flag", "Private notes", per-person capacity numbers, planning churn detail, RAID P/I scores, <team-lead>'s personal staff action items

**Anti-repetition rule:** Each fact appears in exactly ONE bullet. Within a domain, an item lives in exactly one of Done / In progress / Next. Cross-team topics should reference items briefly — full context lives in the domain bucket where they first appear.

**Cross-week de-dup rule (MANDATORY):** Before finalizing each domain's **Done** bucket, diff every item against the previous weekly archive (`archive/<latest>.md`). Drop anything already reported done last week. Keep items that were "in progress / next / pulled into sprint" last week and are now genuinely `resolution=Done` (legitimate progression). For standing infra already announced as merged, do not re-list it as a Done win — make it an Announcement call-to-action instead. Verify every "Done" against Jira `resolution`: only `Done` counts; `Won't Do`/`Duplicate`/`Declined` are scope churn, never wins (and prefer citing the merged PR over a mis-attributed or aborted ticket key — 1:1 notes sometimes cite the wrong key).
