# Output Templates

## Milestone Status

```
Milestone <name> Status — FW Interfaces

1. Executive Summary
- <1-3 sentences: overall RAG, key risk, recommendation>

2. Progress by Subsystem
| Subsystem | Evidence % | RAG | Key Item |
|-----------|-----------|-----|----------|
| PCIe EP   |           |     |          |
| SE        |           |     |          |
| D2D/UCIe  |           |     |          |
| Infra     |           |     |          |

3. Schedule
| Sprint | Focus | Status |
|--------|-------|--------|
| S1     |       |        |
| S2     |       |        |
| S3     |       |        |
| S4     |       |        |

4. Capacity
- Planned focus-days:
- Consumed:
- Debug/lab reserve:
- Lead cross-functional load:

5. Top Risks (from RAID)
| ID | Title | RAG | Owner | Due |
|----|-------|-----|-------|-----|

6. Cross-Functional Dependencies
| Dependency | External Owner | Status | Due |
|------------|---------------|--------|-----|

7. Next Steps
| Action | Owner | Due |
|--------|-------|-----|

8. KPIs
- Twister pass rate:
- Blocked external days this sprint:
- Open Red RAID rows:
- Stories without evidence tier:
```

---

## Weekly 5-15

Use the 5-15 format — 15 minutes to write, 5 minutes to read.

```
Subject: FW Interfaces weekly 5-15 — Week of <date>

Human note
- <1-2 sentences: team sentiment, thanks, notable event>

Key reminders
- <Milestone target, upcoming gate, important date>

Important topic 1 — <subsystem>
- Confidence: X/10 (was Y/10)
- <1 paragraph: what changed, risk, evidence, next step>

Important topic 2 — <subsystem>
- Confidence: X/10
- <1 paragraph>

Brief updates
- <ticket>: <one-line status + evidence>
- <ticket>: <one-line status + evidence>
- ...

Questions / concerns
- <Decision needed, ask, or open question>
```

---

## VP / Skip-Level Update

Use when reporting to FW Group Manager, SW Division Manager, or any audience with decision authority but limited technical context.

```
Subject: <Decision needed | Status> — <topic>

Decision needed (if any)
- <One sentence: what approval or decision is required>

Current state
- <RAG>. <1-2 sentences>

What changed
- <What was true before vs now>

Impact
- <Program/milestone/customer consequence>

Options
- A. <option>. <tradeoff>. Recommended.
- B. <option>. <tradeoff>.
- C. <option>. <tradeoff>.

Ask
- <Specific action + owner + date>

Next proof point
- <What evidence will be produced and when>
```

---

## Cross-Functional Partner Update

Use for VLSI, validation, host SW, DV, release, product.

```
Subject: FW ↔ <partner> — <topic>

What changed
- <1-2 sentences>

What we need from you
- <Specific ask + date>

Contract / assumption under review
- FW assumption: <statement>
- Partner assumption: <statement>

Shared due dates
- <partner> answer: <date>
- FW action: <date>
- Joint verification: <date>

How we will verify
- <Test/script/demo + pass criteria>
```

---

## Per-Person Briefing

```
<team-member> — Sprint Outlook (<sprint name>)

Priorities (ordered)
1. <ticket>: <goal + evidence target>
2. <ticket>: <goal + evidence target>

Blockers
- <blocker + external owner + expected resolution>

Dependencies landing this sprint
- <dependency + date + impact if late>

Evidence gaps
- <story missing DoD tier or test plan>

Capacity note
- Focus-days available: X
- Lab/debug commitment: Y days
- Review load: Z

Notes from lead
- <optional personal note, coaching, heads-up>
```

---

## Team Weekly Briefing

```
Team Weekly — <date>

Milestone reminder
- <Goal + evidence target + days remaining>

Sprint progress
- Done: X pts | In progress: Y pts | To do: Z pts
- Confidence: X/10

Top risks / blockers
- <risk/blocker + owner + mitigation>

Cross-functional updates
- <partner + what changed + what we expect>

Shout-outs
- <recognition>

Upcoming dependencies
- <what's landing + date + who is affected>

Demo plan
- <what each person will demo at sprint review>
```

---

## Slippage Communication

Always use this structure when reporting a delay, regardless of audience:

```
1. State         — current RAG + confidence
2. What changed  — factual delta from last report
3. Impact        — program / milestone / customer effect
4. Evidence      — Jira issue, CI run, log, RAID row
5. Options       — at least 2, with tradeoffs
6. Recommendation — which option and why
7. Ask           — specific action + owner + date
8. Next proof point — what evidence, when
```

---

## Slide Deck Outline

```
Slide 1: Title + milestone name + date + RAG

Slide 2: Executive summary (3 bullets)

Slide 3: Progress by subsystem (table)

Slide 4: Top risks (3-5 rows from RAID)

Slide 5: Cross-functional dependencies (table)

Slide 6: Capacity vs commitment (chart description or table)

Slide 7: Key achievements this period (3-5 bullets)

Slide 8: Ask / decisions needed

Slide 9: Next proof points + timeline
```
