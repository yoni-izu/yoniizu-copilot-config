Below is a structured research digest. Where I use source-exact headings, formulas, or short phrases, I label them as such; where I provide firmware-specific examples, they are adapted templates built from the cited patterns.

## 1. Firmware / embedded team management — what’s different from web/SaaS Agile

### Key insights

- **Firmware Agile is gated by physical reality, not just code flow.** James Grenning’s embedded Agile paper calls out differences that do not exist in most web/SaaS work: hardware and software are developed concurrently, the development machine architecture differs from the target, target hardware may arrive late, bugs can be hard to classify as hardware or software, and hardware bugs often become software workaround work.

- **Embedded “done” has to account for constraints like determinism, real-time behavior, resource limits, manufacturing cost, peripherals, and fault tolerance.** Elecia White’s *Making Embedded Systems* describes embedded systems as purpose-built computers with constrained CPU, memory, and peripherals, often requiring deterministic, real-time, and fault-tolerant behavior.

- **Hardware coupling is a delivery risk, not just a code-quality smell.** Embedded Artistry warns that direct dependency on vendor SDKs, hardware, and RTOS APIs can force rewrites when boards, SDKs, or processors change; it recommends abstractions because even imperfect abstraction enables hardware swaps, unit tests, and PC-side testing.

- **Pre-hardware work can still be “real” if it is executable and evidence-producing.** Grenning argues that before hardware is available, stories can be demonstrated through tests and simulations based on real working code, rather than documents alone.

- **The embedded customer team should include hardware-facing partners.** Grenning explicitly says that when hardware/software interaction is involved, hardware engineers should help identify integration stories, and the “customer team” may include product, test, system, hardware, and software representatives.

### Concrete pattern: evidence-tier Definition of Done

Use this instead of a single “Done = merged” rule.

```
Firmware Definition of Done by evidence tier

L0 — Static / build evidence
- Builds for target configuration.
- Builds for native_sim, QEMU, or equivalent host-side target where applicable.
- Unit tests or ztest-style tests pass.
- Twister / CI job attached to Jira issue.

L1 — Contract evidence
- Register map, reset values, mailbox/API, interrupt contract, and error codes match current spec revision.
- Spec revision or CSR hash is recorded in the issue.
- Host-driver, RTL, DV, or validation owner has reviewed the contract if affected.

L2 — Pre-silicon executable evidence
- Feature demonstrated in simulation, virtual platform, RTL sim, emulator, FPGA, or mocked hardware model.
- Pass/fail log attached.
- Known model gaps listed as assumptions or RAID rows.

L3 — Lab / silicon / HIL evidence
- Runs on board, FPGA, emulator, or first silicon using repeatable script.
- Power-cycle or reset behavior captured if relevant.
- Logs, traces, register dumps, or waveform references attached.
- Debug steps are reproducible by another engineer.

L4 — Release / validation evidence
- Validation owner accepts result or signs off on waiver.
- Errata, workaround, feature flag, release note, or host-driver dependency documented.
- Failure modes and recovery behavior tested or explicitly deferred.
```

Source anchors: Grenning treats acceptance tests and working software as the meaningful completion signal, not document completion; Zephyr’s Twister documentation describes automated build/run statuses across boards and emulated environments; Zephyr’s `native_sim` lets Zephyr applications run as Linux executables for software testing decoupled from hardware.

### Quotable guiding principles

- “Working firmware is not a Jira status; it is an evidence trail.”

- “When silicon is absent, the planner’s job is to choose the highest-fidelity proxy, not pretend the dependency does not exist.”

### Counter-arguments and anti-patterns

- **Anti-pattern: SaaS Scrum copy-paste.** A two-week cadence is useful, but firmware teams need hardware-dependency reviews, lab scheduling, spec volatility handling, and pre/post-silicon evidence tiers.

- **Anti-pattern: “Done = code merged.”** For embedded systems, merged code without test tier, register contract, or lab/simulation evidence is only an implementation checkpoint.

- **Counter-argument: too many evidence tiers can slow the team.** Keep the tiers lightweight: the point is not paperwork; it is to make uncertainty visible and prevent fake completion.

- **Anti-pattern: direct vendor SDK / RTOS calls everywhere.** This may look faster during the first sprint, but it creates schedule risk when SDKs, boards, or IP revisions change.

---

## 2. Silicon / SoC bring-up planning patterns

### Key insights

- **Modern bring-up starts before tape-out.** Semiconductor Engineering describes software bring-up moving into pre-tapeout environments such as virtual models, RTL simulation, emulation, and FPGA prototypes, while post-silicon remains essential.

- **Diagnostics should exist before first silicon.** The same source recommends creating and debugging test cases ahead of silicon, then reusing those tests as post-silicon smoke tests.

- **Post-silicon work is not a cleanup phase; it is a major scheduled phase.** SoC Labs breaks post-silicon into board integration, software readiness, power-up, communication establishment, boot/basic behavior, subsystem validation, and performance/power validation.

- **0-day bring-up depends on prebuilt software paths and upstream-friendly preparation.** An OSFC 2024 talk on 0-day SoC bring-up emphasizes preparing software before real silicon, using simulators/emulators, and decoupling bring-up from physical geography through upstream support.

- **Chiplet / high-speed-interface bring-up has extra interoperability and observability burden.** The Hot Chips UCIe tutorial frames interoperability around form factor, power, reset/init, register access, physical layer behavior, protocol, security, test, repair, and debug controllability/observability.

### Concrete checklist: power-on readiness / first-boot plan

```
Power-on readiness checklist

Pre-tapeout / T-8 to T-4 weeks
- Boot path named: BootROM → FSBL / loader → Zephyr init → interface firmware.
- Current CSR/register map revision recorded.
- Reset, clock, power, and strap assumptions documented.
- PCIe / UCIe / D2D minimum smoke sequence defined.
- Host-driver minimum dependency named.
- Diagnostics compile and run in simulation, emulation, FPGA, or model.
- JTAG, serial, mailbox, trace, and log collection scripts drafted.
- Known model gaps are captured as RAID assumptions.
- Bring-up owner, backup owner, RTL contact, DV contact, validation contact, and lab contact named.

T-2 weeks
- Lab slots booked.
- Board allocation plan agreed.
- Power supplies, probes, JTAG, serial adapters, and host machines confirmed.
- Smoke test pass/fail table frozen.
- Errata / workaround template ready.
- War-room communication channel created.
- First 48-hour decision owner named.

First power
- Visual inspection complete.
- Power rail impedance / shorts checked.
- Power applied in controlled sequence.
- Boot logs captured.
- JTAG attach confirmed.
- Clocks and resets checked.
- BootROM or first-stage loader reached.
- Firmware image loaded or download path verified.
- First console / mailbox / trace output observed.
- PCIe endpoint enumeration, UCIe training, D2D link, or equivalent first interface smoke attempted.
- Every failure gets owner, evidence link, suspected domain, and next experiment.

Exit from first-boot phase
- A reproducible boot or controlled failure sequence exists.
- Debug artifacts are attached.
- Top silicon / RTL / board / firmware risks are in RAID.
- Next subsystem gate is named.
```

Board bring-up sources use a similar phase structure: initial hardware analysis, power-on and boot logs, functional tests, software validation, debugging with oscilloscope/logic analyzer/JTAG, and documentation/reporting.

### Quotable guiding principles

- “First silicon should discover silicon behavior, not discover missing scripts.”

- “A bring-up checklist is a risk register with a power switch.”

### Counter-arguments and anti-patterns

- **Anti-pattern: writing diagnostics after silicon arrives.** That consumes the most valuable bring-up window and hides whether failures are silicon, board, or test issues.

- **Anti-pattern: no common test environment between pre- and post-silicon.** Semiconductor Engineering specifically recommends a common environment and common testbench concepts across pre/post-silicon work.

- **Counter-argument: pre-silicon models are incomplete.** Correct; Semiconductor Engineering notes pre-silicon runs can be orders of magnitude slower and cannot eliminate all risk. The goal is risk reduction, not false certainty.

- **Anti-pattern: no debug-access plan.** SoC Labs notes that limited debug registers and inadequate debug architecture can make hard faults time-consuming.

---

## 3. Startup engineering management — lean / “just enough process” patterns

### Key insights

- **A five-person firmware team is near the lower bound of a stable team.** Will Larson’s team-sizing guidance says steady-state engineering teams are often six to eight people, while very small teams become fragile and “leaky abstractions”; for a five-person firmware team, the tech lead is likely operating as a technical lead manager even if not formally titled that way.

- **Use the lightest process that solves a repeated failure.** Larson’s StaffEng material recommends choosing the cheapest straightforward quality tool likely to work, starting with light solutions, avoiding enterprise-scale coordination too early, and killing ineffective process.

- **Fast feedback beats elaborate management design.** Camille Fournier’s startup management notes emphasize building quicker feedback loops, releasing as frequently as reasonable, and postmorteming quickly after major events.

- **Small teams must avoid “snacking” and “preening.”** StaffEng’s “work on what matters” essay warns against easy low-impact work and visible-but-low-impact work; it pushes senior engineers toward existential risks, leverage, mentorship, onboarding, and finishing high-impact projects.

- **Do not throw process or people at unclear scope.** LeadDev’s scaling guidance warns that bigger teams often move slower, and recommends small teams with clear scope, transparency, and incremental process changes.

### Concrete template: two-month milestone / two-week sprint operating system

```
Milestone one-pager, max 2 pages

1. Outcome
- What customer, validation, tape-out, or bring-up outcome this milestone proves.

2. Evidence target
- What logs, demos, tests, boards, emulators, or validation results will prove the outcome.

3. Scope slices
- Must ship:
- Should ship:
- Explicitly not in this milestone:

4. Cross-functional contracts
- RTL / VLSI:
- DV:
- Silicon validation:
- Host drivers:
- Product:
- Release:

5. Top RAID items
- Top 3 risks:
- Top 3 dependencies:
- Assumptions most likely to break:

6. Capacity model
- Focus-days available:
- Lab/debug reserve:
- Lead / review / cross-functional load:
- Planned sprint capacity:

7. Off-track triggers
- Trigger:
- Owner:
- Decision needed:

8. Demo plan
- Sprint 1 demo:
- Sprint 2 demo:
- Sprint 3 demo:
- Sprint 4 / milestone demo:
```

```
Two-week sprint firmware planning agenda

1. Re-read milestone evidence target.
2. Pick the highest-risk evidence gap.
3. Review changed assumptions since last sprint.
4. Confirm lab / emulation / FPGA availability.
5. Set sprint capacity using focus-days, not nominal headcount.
6. Pull stories only if each has:
   - owner
   - DoD evidence tier
   - external dependencies
   - test/proxy plan
7. Name one demo artifact per engineer:
   - log
   - test result
   - trace
   - waveform reference
   - host enumeration
   - validation result
8. Update RAID before closing planning.
```

### Quotable guiding principles

- “For a five-person firmware team, process should preserve the lead’s attention, not spend it.”

- “Add ceremony only when a repeated failure proves the missing ceremony is cheaper than the failure.”

### Counter-arguments and anti-patterns

- **Anti-pattern: premature scaling frameworks.** A five-person firmware team needs dependency clarity and evidence, not SAFe-style ceremony.

- **Anti-pattern: no process because ‘we are a startup.’** Camille Fournier warns that early random structure can become organizational debt, even though excessive structure is also harmful.

- **Counter-argument: process helps cross-functional work.** Yes, but the process should be small: milestone one-pager, RAID, weekly status, sprint plan, evidence demo.

- **Anti-pattern: the lead becomes only a scheduler.** Camille Fournier describes engineering management as a technical discipline; managers stay credible by reading design docs, understanding systems, and asking good questions.

---

## 4. Status reporting templates — concrete examples

### Key insights

- **Use a weekly “drip” to avoid surprise and build alignment.** Will Larson recommends consistent weekly updates, compiled over the week, with a human note, key reminders, a few important topics, brief updates, and questions or concerns.

- **A 5-15 update is intentionally lightweight.** Larson describes the 5-15 format as taking about 15 minutes to write and 5 minutes to read; it creates ambient connective tissue, preserves later context, and models good communication.

- **Status should surface confidence, blockers, and next priorities, not task exhaust.** Christina Wodtke’s weekly status template uses team goals, project/task status, blockers and risks, next priorities, and notes; it also suggests confidence scoring and red/green/black indicators.

- **Startup updates should make asks easy to act on.** First Round Review’s investor-update guidance says regular updates should include wins, challenges, product development, traction, hiring, financials, and specific needs to reduce the transaction cost of asking for help.

- **Narrative and metrics serve different readers.** Larson’s measurement essay separates measurement modes such as planning, operating, optimizing, and inspiring; it warns against trying to use one broad metric board for every audience.

### Template 1: weekly 5-15 update for the firmware lead

Source-exact structure from Larson’s internal-communications template: “1–2 sentences” of human introduction, “1 sentence” of key reminders, “1 paragraph” per important topic, a “bulleted list” of brief updates, and a close with questions/thoughts/concerns.

```
Subject: FW Interfaces weekly 5-15 — Week of

Human note
- Lab week was noisy, but the team kept the PCIe debug loop moving. Thanks to validation for extending the FPGA slot.

Key reminders
- M3 exit target remains: PCIe EP enumeration on FPGA + UCIe training diagnostic in emulation by .

Important topic 1 — PCIe EP
- Confidence moved from 7/10 to 5/10 because RTL CSR map v0.86 changed BAR aperture behavior. FW has a patch in review; host-driver assumptions need confirmation by Friday.

Important topic 2 — D2D / UCIe
- UCIe training diagnostic now passes native_sim and RTL smoke. Remaining unknown is PHY reset timing in emulation.

Brief updates
- CZ-1421: Zephyr ISR refactor merged; Twister native_sim pass attached.
- CZ-1450: Scale-Engine mailbox test blocked on RTL v0.91.
- CZ-1462: validation HIL script now power-cycles board and captures serial logs.
- CZ-1477: release branch image generation automated.

Questions / concerns
- Should we cut advanced AER handling from M3 to preserve PCIe enumeration demo?
- Need VLSI owner for reset-sequence signoff by Thursday.
```

### Template 2: weekly team status using Wodtke’s headings

Source-exact headings: “Start with your team goals (or OKRs),” “Explain the status of your projects and tasks,” “Communicate blockers and risks,” “Share what’s next on the priority list,” and “Add notes.”

```
1. Team goals / OKRs
- O1: M3 power-on readiness for PCIe EP and UCIe.
  - Confidence: 6/10
  - RAG: Yellow

2. Project and task status
- PCIe EP enumeration: Yellow
  - Done: BAR init path builds and passes native_sim.
  - Not done: FPGA link still drops after reset exit.
  - Evidence: JIRA-123 log, CI run #456.

- UCIe diagnostic: Green
  - Done: training state-machine test passes in emulator.
  - Not done: compliance error-path coverage deferred.

3. Blockers and risks
- Blocker: RTL CSR map v0.86 not frozen; owner ; decision needed by Friday.
- Risk: lab slot contention next sprint may reduce HIL coverage by 2 engineer-days.

4. Next priorities
- P1: Re-run PCIe reset-exit smoke on FPGA.
- P1: Confirm host-driver MPS assumption.
- P2: Add UCIe error-path diagnostic.

5. Notes
- Validation requested release image by Tuesday; this is compatible with current scope if AER advanced handling is cut.
```

### Template 3: monthly / milestone status doc

Atlassian’s project-status guidance lists these source-exact elements: “Executive summary,” “Visual progress,” “Overall project schedule,” “Budget,” “Challenges/blockers,” “Next steps with owners/due dates,” and “KPIs/metrics.”

```
Milestone M3 status — FW Interfaces

1. Executive summary
- M3 is Yellow. PCIe EP enumeration is at risk due to RTL reset-sequence churn; UCIe training diagnostic remains on track.
- Recommendation: preserve first-silicon readiness by cutting advanced AER handling from M3.

2. Visual progress
- PCIe EP: 65% evidence complete; Yellow.
- UCIe: 80% evidence complete; Green.
- Scale-Engine mailbox: 45% evidence complete; Yellow.
- Release image automation: 90% evidence complete; Green.

3. Overall project schedule
- Sprint 1: contract and test scaffolding complete.
- Sprint 2: pre-silicon diagnostics complete.
- Sprint 3: FPGA / emulation smoke.
- Sprint 4: milestone demo and validation handoff.

4. Capacity / budget
- Planned focus-days: 30.
- Debug/lab reserve consumed: 7 of 8 days.
- Lead cross-functional load: higher than planned by 1.5 days.

5. Challenges / blockers
- RTL CSR map freeze slipped.
- FPGA lab access reduced by validation priority conflict.
- Host-driver MPS assumption unconfirmed.

6. Next steps with owners / due dates
- FW lead: recommend M3 scope cut by Thursday.
- VLSI: confirm reset sequence by Friday.
- Host SW: confirm MPS and enumeration expectations by Monday.
- Validation: reserve HIL slot for next Wednesday.

7. KPIs / metrics
- Twister pass rate:
- HIL smoke pass rate:
- External-blocked engineer-days:
- Open Red RAID rows:
- Bugs discovered only in lab:
```

### Template 4: skip-level / VP update

Use this when the reader has decision authority but limited technical context.

```
Subject: Decision needed — M3 PCIe scope tradeoff

Decision needed
- Approve cutting advanced AER handling from M3 to preserve PCIe EP enumeration and first-silicon readiness.

Current state
- M3 is Yellow.
- UCIe diagnostic remains on track.
- PCIe EP enumeration is at risk due to reset-sequence and BAR aperture changes from RTL CSR v0.86.

What changed
- Last week: BAR aperture behavior matched host-driver assumption.
- This week: RTL changed reset-exit behavior; FPGA smoke fails after link training.

Business / program impact
- If unchanged, M3 may slip one sprint.
- If we cut advanced AER handling, we preserve the demo most relevant to first-silicon bring-up.

Options
- A. Cut advanced AER handling; keep M3 date. Recommended.
- B. Keep scope; move M3 by one sprint.
- C. Pull engineer from Scale-Engine; raises D2D risk.

Ask
- Approve option A by Thursday.
- VLSI to confirm reset sequence by Friday.

Next proof point
- FPGA smoke rerun Monday 15:00 with logs attached to JIRA-123.
```

### Template 5: cross-functional partner update

```
Subject: FW ↔ VLSI handoff — PCIe reset-sequence dependency

What changed
- FW now depends on CSR map v0.86 reset-exit behavior for PCIe EP enumeration.

What we need from you
- Confirm whether reset bit  is self-clearing.
- Confirm whether BAR aperture registers are valid before or after LTSSM reaches L0.

Contract / assumption under review
- FW assumption: BAR aperture can be programmed before link reaches L0.
- Host-driver assumption: 256B MPS is supported for M3 demo.

Shared due dates
- VLSI answer: Friday.
- FW patch: Monday.
- FPGA smoke: Tuesday.

How we will verify handoff
- FW will run FPGA smoke script .
- Passing evidence: host sees endpoint, BARs enumerate, and FW log shows reset-exit sequence complete.
```

### Quotable guiding principles

- “Status is not proof of effort; it is a low-latency routing protocol for decisions.”

- “The farther up the org, the more every update must answer: what changed, what matters, and what decision is needed.”

### Counter-arguments and anti-patterns

- **Anti-pattern: raw Jira dump as status.** Jira is input; status is interpretation.

- **Anti-pattern: green until the day it turns red.** Wodtke explicitly emphasizes blockers, risks, and avoiding surprises.

- **Counter-argument: narrative updates can hide numbers.** Pair narrative with a small metrics appendix: blocked days, test pass rate, open Red RAID rows, and next proof point.

- **Anti-pattern: all audiences get the same update.** Engineers need details; VPs need impact, options, and asks; cross-functional partners need contracts and due dates.

---

## 5. RAID log practice — Risks, Assumptions, Issues, Dependencies

### Key insights

- **RAID is a planning and execution control surface.** IRM defines RAID as Risks, Assumptions, Issues, and Dependencies, and frames it as a tool for tracking important project items consistently during planning and execution.

- **The log must be tailored or it becomes clutter.** IRM warns that too much detail clutters the log and recommends agreeing on the right level of detail for the project.

- **Each row needs ownership, priority, and status.** Asana’s RAID guidance includes fields such as ID, description, category, date, owner, priority, and status, and recommends updating the log regularly and in real time when needed.

- **Risk scoring should separate likelihood from impact.** BrightWork describes risk evaluation using probability and impact, plus owner and action plan.

- **For firmware, dependencies are often external clocks.** RTL drops, CSR freezes, lab slots, validation windows, host-driver releases, and board availability should be first-class dependency rows, not hidden comments inside stories.

### Concrete template: lightweight firmware RAID log

Source-exact categories: **Risks**, **Assumptions**, **Issues**, **Dependencies**.

```
Firmware RAID columns

ID
Type: Risk | Assumption | Issue | Dependency
Title
Description / failing evidence
Firmware owner
External owner
Probability: 1–5
Impact: 1–5
Score: Probability × Impact
RAG: Green | Yellow | Orange | Red
Trigger
Mitigation
Contingency
Due date
Review cadence
Status
Jira / spec / log link
```

```
Risk scoring

Probability
1 — Rare; no current signal.
2 — Unlikely; weak signal.
3 — Plausible; credible path to happening.
4 — Likely; current trend points toward it.
5 — Happening or nearly certain.

Impact
1 — Local annoyance.
2 — One story or one engineer-day.
3 — Sprint goal or subsystem slice.
4 — Milestone exit criterion.
5 — Tape-out, first-silicon, customer, or release gate.

Score = Probability × Impact

RAG
1–4   Green
5–9   Yellow
10–14 Orange
15–25 Red
```

```
Example rows

R-07 | Risk
Title: PCIe reset-exit behavior may invalidate FW init sequence
Owner: FW lead
External owner: VLSI
P: 4 | I: 4 | Score: 16 | RAG: Red
Trigger: CSR map v0.86 not confirmed by Friday
Mitigation: Add reset-state probe and alternate init path in FPGA smoke
Contingency: Cut advanced AER handling from M3
Due: Friday
Status: Open

A-03 | Assumption
Title: Host driver supports 256B MPS for M3 demo
Owner: PCIe FW engineer
External owner: Host SW
P: 3 | I: 3 | Score: 9 | RAG: Yellow
Trigger: No host confirmation by Monday
Mitigation: Confirm with host SW and add fallback to 128B
Contingency: Demo with validation-owned host branch
Due: Monday
Status: Open

I-02 | Issue
Title: UCIe diagnostic hangs after PHY reset
Owner: D2D FW engineer
External owner: DV
P: 5 | I: 3 | Score: 15 | RAG: Red
Trigger: Already observed in emulator
Mitigation: Capture trace and compare reset timing with DV sequence
Contingency: Bypass PHY reset in M3 diagnostic with documented waiver
Due: Wednesday
Status: Active

D-05 | Dependency
Title: CSR map v0.86 freeze
Owner: FW lead
External owner: VLSI
P: 4 | I: 4 | Score: 16 | RAG: Red
Trigger: Freeze slips beyond Friday
Mitigation: Codegen supports v0.85/v0.86 switch
Contingency: Lock M3 to v0.85 and document incompatibility
Due: Friday
Status: Open
```

### Review cadence

```
Daily
- Update active Red issues and blockers.

Weekly
- Review top 5 RAID rows in team status.
- Confirm every Red/Orange row has owner, trigger, mitigation, and due date.

Sprint planning
- Re-score all open Red/Orange rows.
- Convert unresolved assumptions into risks or dependencies.
- Decide whether sprint scope still fits capacity.

Milestone review / retrospective
- Close resolved rows.
- Identify issues that were not predicted as risks.
- Convert repeated dependency failures into planning rules.
```

Asana recommends weekly review and real-time updates when new risks emerge; it also notes that the project manager typically owns the RAID log while the team contributes.

### Quotable guiding principles

- “A RAID row without an owner is just anxiety in spreadsheet form.”

- “Assumptions are risks that have not been asked a good enough question yet.”

### Counter-arguments and anti-patterns

- **Anti-pattern: PMO graveyard.** A RAID log with dozens of stale rows is worse than no log because it creates false confidence.

- **Anti-pattern: no trigger.** “RTL might slip” is vague; “CSR v0.86 not frozen by Friday” is actionable.

- **Counter-argument: engineers dislike risk registers.** Keep only cross-functional, milestone-relevant, or high-impact items. IRM explicitly recommends tailoring detail to the project.

- **Anti-pattern: dependency rows without external owners.** Firmware cannot manage an RTL, lab, host-driver, or validation dependency by wishing harder.

---

## 6. Capacity and velocity for firmware teams

### Key insights

- **Use story points for backlog and release forecasting, not sprint capacity promises.** Mike Cohn recommends capacity-driven sprint planning for the sprint itself, reserving story points and velocity for broader backlog/release planning.

- **Do not convert story points to hours.** Cohn argues that story points are relative effort units and that equating points to hours creates misleading precision, especially across engineers with different skills.

- **Embedded velocity is useful only as a calibrated forecast.** Grenning recommends relative, unitless story estimates and historical velocity as a fact-based early-warning signal; when the plan is off, the realistic control variables are scope, resources, date, or cancellation, with scope usually the practical lever.

- **External blockers must be accounted for separately.** Because embedded teams face late target hardware, unclear hardware/software bugs, hardware workarounds, emulation/FPGA constraints, and lab contention, blocked days should be measured as a capacity drain or dependency metric, not treated as low engineer productivity.

- **Support and technical debt consume real capacity.** Camille Fournier gives a startup heuristic that the same number of features may require meaningfully more engineering capacity over time because of technical debt, support, and other drag.

### Concrete capacity model

```
Sprint focus-days =
  Σ(engineer available days × focus factor)
  - PTO / holidays
  - fixed meeting load
  - lead management / review / cross-functional load
  - planned lab support
  - planned debug support
  - external-review support
  - interrupt reserve
```

```
Planned sprint scope ≤ min(
  known-task capacity in focus-days,
  historical completed points × availability factor × confidence factor
)
```

Example for a 5-person team in a 10-workday sprint:

```
Nominal capacity
- 5 engineers × 10 days = 50 engineer-days

Subtract
- PTO / holidays: 3 days
- meetings / planning / review: 5 days
- tech lead management and cross-functional load: 4 days
- planned lab coverage: 5 days
- bring-up/debug interrupt reserve: 6 days
- support for validation / host / RTL reviews: 3 days

Estimated focus-days
- 50 - 3 - 5 - 4 - 5 - 6 - 3 = 24 focus-days

Planning rule
- Commit about 70–80% of focus-days to planned feature work.
- Keep the remainder for debug, lab, review, and fast risk retirement.
```

I did not find a credible firmware-specific universal “buffer percentage” norm for hardware-dependent work. For this team, a better rule is to make the reserve explicit and tune it from local data: blocked external days, lab wait days, escaped lab-only defects, and unplanned debug days.

### Concrete sizing guide

```
Firmware t-shirt sizing

XS
- Known Zephyr config, small bug fix, or unit test.
- No RTL, spec, host-driver, or lab dependency.

S
- Driver or firmware change with stable CSR/API.
- Testable in native_sim, QEMU, or unit harness.

M
- IP integration work with current CSR map.
- Needs emulator, FPGA, validation script, or host-driver coordination.

L
- Cross-team contract change.
- Requires RTL / DV / validation / host-driver agreement.
- Lab or emulation window needed.

XL
- Unknown silicon behavior, new interface mode, unstable spec, or missing proxy.
- Must be split into spike + evidence goal before sprint commitment.
```

### Concrete blocked-capacity rule

```
External-blocked day
A day, or meaningful fraction of a day, where the engineer cannot advance the planned work by firmware action because the next dependency is outside FW control.

Required fields
- Blocked by: RTL | silicon | lab | board | host driver | validation | release | product
- Start time
- Owner
- Next action
- Fallback work
- Whether the blocked day should be excluded from velocity interpretation
```

### Quotable guiding principles

- “Velocity is a weather forecast, not a production quota.”

- “A blocked firmware engineer is not zero capacity; they are capacity forced into debugging, support, or risk retirement—make the reallocation explicit.”

### Counter-arguments and anti-patterns

- **Anti-pattern: individual velocity.** It incentivizes gaming and ignores cross-functional dependency work.

- **Anti-pattern: fixed story-point-to-hour conversion.** Cohn explicitly warns against this because points are relative and team-dependent.

- **Anti-pattern: 100% utilization.** Firmware teams need debug reserve, lab reserve, review capacity, and interruption capacity.

- **Counter-argument: hours are still useful.** Yes, but use hours or focus-days for sprint-task capacity, not as a conversion from story points.

- **Anti-pattern: treating lab wait as failure to execute.** Lab wait is a dependency-management signal.

---

## 7. Retrospective patterns that work for small firmware teams

### Key insights

- **Use pre-mortems before risky firmware milestones.** Atlassian’s pre-mortem play is explicitly designed to anticipate what could go wrong before starting work; it recommends identifying risks, grouping them, voting, discussing, assigning owners, and tracking follow-up work.

- **Invite people outside firmware.** Mountain Goat Software recommends inviting stakeholders beyond the development team, such as sponsors, users, architects, designers, and other technical teams; for firmware, that means RTL, DV, validation, host drivers, lab, and release.

- **Pre-mortems make it safer to name risks early.** Gary Klein’s pre-mortem framing argues that imagining a future failure helps people speak up and identify risks that might otherwise remain unspoken.

- **Separate sprint retros from incident postmortems.** Atlassian distinguishes retrospectives focused on sprint/process improvement from postmortems focused on causes, impact, next steps, and preventive measures after incidents.

- **Firmware retros should improve the debug system, not just the meeting system.** David Agans’ debugging rules, summarized by Embedded Artistry, emphasize understanding the system, making the failure happen, observing evidence, dividing and conquering, changing one thing at a time, keeping an audit trail, and verifying the fix.

### Concrete pre-mortem board

Source-exact prompt headings from Atlassian’s pre-mortem play:

```
What could cause us to miss our deadline?

What will keep our project on time?

What does this project need that we don’t have?

What do we already have that this project needs?

What lessons have you learned from past projects?

What are you worried about?

What are you excited about?

To discuss
```

Firmware-specific usage:

```
Pre-mortem question
- What could prevent us from successfully delivering this milestone in the next 60–90 days?

Voting
- Each participant gets 3 votes.
- Vote for risks most likely to affect milestone exit criteria.

Output
- Top risks become RAID rows.
- Each top risk gets:
  - owner
  - trigger
  - mitigation
  - contingency
  - date to revisit
```

Mountain Goat’s startup-oriented prompt, source-exact except for the time window, is: “What could prevent us from successfully delivering this project in the next 90 days?”

### Concrete milestone retrospective for a firmware team

```
Firmware milestone retrospective

1. Evidence we moved left
- Which bugs were caught in unit, native_sim, RTL sim, emulator, FPGA, HIL, or silicon?
- Which late bugs could have been caught earlier?

2. Lab-only surprises
- What first appeared only in the lab?
- Why did our pre-silicon proxy miss it?

3. Ambiguous HW/SW bugs
- Which failures were hard to classify?
- How long did classification take?
- What instrumentation was missing?

4. Dependency truth table
- RTL: promised vs delivered vs changed.
- DV: promised vs delivered vs changed.
- Validation: promised vs delivered vs changed.
- Host SW: promised vs delivered vs changed.
- Release: promised vs delivered vs changed.

5. Debug audit trail
- Could a different engineer reproduce the failure?
- Were logs, register dumps, traces, scripts, and board setup captured?

6. RAID reconciliation
- Which issues were predicted as risks?
- Which issues were not predicted?
- Which assumptions were wrong?
- Which dependencies need earlier escalation next time?

7. One change for next milestone
- Process change:
- Test/tool change:
- Cross-functional contract change:
- Owner:
- Date:
```

### Quotable guiding principles

- “A good firmware retro leaves behind a better test bench, not just better feelings.”

- “Every lab-only surprise should create a pre-silicon experiment, a tool gap, or an explicitly accepted risk.”

### Counter-arguments and anti-patterns

- **Anti-pattern: generic Start / Stop / Continue only.** It may surface emotions, but often misses hardware dependency, instrumentation, and evidence-stage lessons.

- **Anti-pattern: blame the hardware team.** The useful question is: what signal could firmware have requested earlier?

- **Counter-argument: retros take time.** Keep them short and evidence-driven: one board, top three misses, one owner per fix.

- **Anti-pattern: no cross-functional attendance.** If RTL, DV, validation, host, or lab caused or absorbed the pain, they should be part of the learning loop.

---

## 8. Painting the picture to upper management — narrative techniques

### Key insights

- **Never surprise your manager.** StaffEng’s alignment guidance says a senior engineer should avoid surprising their manager, feed them context, and use weekly updates or equivalent channels to increase bandwidth and reduce friction.

- **Executives need narrative plus decision framing, not raw technical exhaust.** Larson’s internal-communications guidance emphasizes brief, consistent updates that reassure leaders they are not missing important information.

- **Use metrics according to audience.** Larson’s measurement framework separates measuring to plan, operate, optimize, and inspire, and warns that flawed measurement loops can reflect lack of trust or drive bad decisions.

- **Slippage communication should include impact, options, recommendation, and next proof point.** This follows from Wodtke’s emphasis on blockers/risks and “no surprises,” and First Round’s advice to make specific asks easy to act on.

- **Strategy narratives should be specific and show their work.** StaffEng’s strategy-writing guidance recommends writing specifics, being opinionated, showing your work, and grounding vision in business and user impact.

### Concrete template: slippage without credibility loss

```
State
- Yellow. M3 is still possible, but confidence moved from 8/10 to 5/10.

What changed
- RTL CSR map v0.86 changed PCIe BAR aperture behavior.
- Host-driver assumption is now unconfirmed.
- FPGA smoke fails after reset exit.

Impact
- PCIe EP enumeration demo may slip one sprint.
- UCIe diagnostic and release image automation are not currently affected.

Evidence
- native_sim and Twister pass.
- FPGA smoke fails at reset exit.
- Log attached to JIRA-123.
- VLSI review pending.

Options
- A. Cut advanced AER handling from M3; preserve enumeration demo. Recommended.
- B. Keep scope; move M3 by one sprint.
- C. Pull engineer from Scale-Engine; raises D2D risk.

Ask
- Approve option A by Thursday.
- VLSI to confirm reset sequence by Friday.

Next proof point
- FPGA smoke rerun Monday 15:00.
```

### Concrete escalation rule

```
Escalate when at least one is true

- The decision is outside firmware authority.
- The risk threatens a milestone, tape-out, bring-up, customer, or release gate.
- The external owner is not engaged or lacks a date.
- The mitigation consumes capacity from another committed milestone goal.
- The team has less than one sprint of recovery room.
- The same dependency has slipped twice.
- The status changed materially since the last update.
```

### Narrative vs metrics-style guidance

```
Use narrative-style when:
- Reporting to VP / skip-level / division manager.
- Explaining slippage, uncertainty, or tradeoffs.
- Asking for a decision.
- Translating technical risk into program impact.

Use metrics-style when:
- Managing weekly team flow.
- Detecting blocked work.
- Reviewing quality and test coverage.
- Comparing current milestone health to previous milestones.

Best executive format:
- State
- What changed
- Impact
- Options
- Recommendation
- Ask
- Next proof point
```

### Quotable guiding principles

- “Executives do not need the waveform; they need the decision the waveform demands.”

- “Credibility is built by making uncertainty visible before it becomes an excuse.”

### Counter-arguments and anti-patterns

- **Anti-pattern: waveform dump upward.** Keep the technical appendix available, but lead with impact and decision.

- **Anti-pattern: slippage without options.** A delay update should include scope/date/resource choices.

- **Anti-pattern: escalating everything.** Escalate decisions and externally owned risks; absorb normal engineering noise.

- **Counter-argument: too much transparency can create panic.** Use confidence, evidence, and road-to-green so transparency sounds like control, not chaos.

---

## 9. Tooling patterns — AI assistants, Jira planning, metrics, Goodhart risks

### Key insights

- **AI-in-Jira patterns are now productized around work breakdown, summarization, status, and blockers.** Atlassian describes AI capabilities for generating and refining stories, creating child tickets from epics, summarizing long comments, using natural-language automation/JQL, and brainstorming edge cases or dependency removal.

- **Agentic planning assistants should connect to work context, not operate from chat alone.** Atlassian’s Rovo material describes creating work from multiple sources, breaking work down, surfacing blockers and next steps, and drafting status from chat/work context.

- **Security and permission boundaries matter.** Reporting on Atlassian’s ChatGPT connector describes Jira/Confluence summarization, issue creation, workflow assistance, OAuth, permission controls, audit logs, tool invocations, and domain allowlists.

- **DORA metrics are useful but must be translated carefully for firmware.** DORA’s official guidance names five software-delivery metrics: change lead time, deployment frequency, failed deployment recovery time, change fail rate, and deployment rework rate; it also warns that context matters and that metrics should be applied to one application or service at a time.

- **Metric-driven management is dangerous when metrics become targets.** DORA explicitly lists pitfalls such as setting metrics as goals, looking for one metric to rule them all, making disparate comparisons, and competing rather than improving; this is a direct Goodhart/Campbell-style risk.

### Concrete architecture: AI team-planner assistant wired into Jira

```
Inputs

Jira
- Epics, stories, bugs, labels, linked issues, status transitions.
- Sprint and milestone assignments.
- Blocked status and blocker reason.
- Assignee and reviewer metadata.

RAID
- Risks, assumptions, issues, dependencies.
- Owner, external owner, score, RAG, trigger, mitigation, contingency.

CI / test
- Build status.
- Zephyr Twister status.
- ztest results.
- native_sim / QEMU runs.
- FPGA / emulator / HIL smoke results.

Specs / docs
- CSR revision.
- IP integration notes.
- Host-driver contract.
- Validation plan.
- Release checklist.

Lab / validation
- Lab booking.
- Board allocation.
- Power-cycle logs.
- Serial/JTAG traces.
- Validation pass/fail dashboards.
```

```
AI assistant jobs

1. Work readiness checker
- Flags stories missing owner, DoD evidence tier, test/proxy plan, dependency owner, or acceptance evidence.

2. Child-ticket drafter
- Converts feature intent into draft FW / RTL / DV / host / validation / lab tasks.
- Requires human approval before creation.

3. Dependency scanner
- Finds Jira comments mentioning CSR churn, RTL drop, lab slot, board, host driver, validation, or release.
- Suggests RAID rows.

4. Status drafter
- Generates team, VP, and cross-functional updates from Jira + RAID + CI.
- Every claim must cite a Jira issue, spec revision, CI run, log, or RAID row.

5. Risk prompt engine
- Asks at sprint planning:
  - What changed since last sprint?
  - Which assumptions lost evidence?
  - Which external dependencies lack owners?
  - Which stories have no pre-silicon proxy?

6. Retrospective evidence miner
- Groups bugs by discovery stage:
  - unit
  - native_sim
  - RTL sim
  - emulator
  - FPGA
  - lab / HIL
  - silicon
  - customer / validation escape
```

```
Guardrails

- AI may draft, suggest, summarize, and flag.
- Human lead approves scope, priority, estimates, and status.
- AI cannot mark firmware “Done” unless required evidence links exist.
- AI-generated status must cite source artifacts.
- Permissions follow Jira / Confluence / Git access controls.
- Tool invocations and bulk actions are auditable.
```

### Firmware delivery-health metrics

```
Flow
- Lead time: story ready → merged.
- Lead time: merged → image available.
- Lead time: image available → validation run.
- PR review age.
- WIP age.
- BlockedExternal days by category: RTL, silicon, lab, board, host, validation, release.

Evidence
- Twister pass rate.
- native_sim / QEMU pass rate.
- FPGA / emulator smoke pass rate.
- HIL smoke pass rate.
- Number of stories lacking DoD evidence tier.
- Bugs by discovery stage.

Bring-up gates
- Days to JTAG attach.
- Days to first boot log.
- Days to first Zephyr thread.
- Days to PCIe enumeration.
- Days to link L0 / UCIe training / D2D smoke.
- Days to first validated error path.
- Days to first release candidate image.

Quality
- Regression failures by subsystem.
- Reopened bugs.
- Workarounds added.
- Errata candidates.
- Lab-only surprises.
- Validation escapes.

Cross-functional health
- Open Red RAID rows.
- Dependencies overdue by owner team.
- Spec revision churn.
- Host-driver contract changes.
- Validation-blocked items.
```

For DORA adaptation:

```
Firmware adaptation of DORA

Deployment frequency
- Released firmware image to validation, lab, bring-up branch, or customer branch.

Change lead time
- Code commit → validated image available.

Change fail rate
- Firmware image requiring rollback, workaround, waiver, or emergency patch.

Failed deployment recovery time
- Time from failed lab / validation / bring-up image to known-good image or accepted workaround.

Deployment rework rate
- Fraction of image releases requiring follow-up fixes before validation can proceed.
```

### Quotable guiding principles

- “The AI planner should be a dependency radar, not an estimation oracle.”

- “Metrics should explain where flow is losing evidence, not rank engineers.”

### Counter-arguments and anti-patterns

- **Anti-pattern: AI-generated certainty.** The assistant must cite Jira/spec/log evidence or mark claims as assumptions.

- **Anti-pattern: individual productivity metrics.** They are fragile, easy to game, and hostile to cross-functional debugging.

- **Anti-pattern: DORA cosplay.** Firmware does not always deploy to production daily; adapt DORA to validation images, lab releases, and bring-up artifacts.

- **Counter-argument: metrics can help.** Yes, when used as improvement signals, not targets. DORA’s own guidance warns against setting metrics as goals and searching for one universal metric.

---

## 10. Agile Fluency Model — applicability to a firmware startup team

### Key insights

- **The Agile Fluency Model has four zones.** The official model describes Focusing as planning in terms of customer/user benefit, Delivering as technical practices that support reliable delivery, Optimizing as responding to market needs, and Strengthening as improving the broader organization.

- **It is not a maturity ladder.** Martin Fowler’s overview emphasizes that “more is not always better”; teams should choose the fluency zone that fits their context, not treat every zone as mandatory.

- **A five-person silicon firmware team should target Focusing + Delivering first.** Focusing gives business-visible priorities and tradeoffs; Delivering is essential because firmware is long-lived, defects are expensive, and late hardware feedback is costly. Fowler and James Shore both describe Delivering as requiring meaningful technical-practice investment, not just ceremonies.

- **Selective Optimizing is useful only where the team has real product authority.** Optimizing requires product/market expertise and delegated decision-making; a firmware lead can apply pieces of it to milestone tradeoffs, but should not pretend the FW team alone owns product strategy.

- **Avoid Strengthening-style process too early.** Fowler warns that growth and bureaucracy can damage fluency, especially when scaling frameworks are introduced before teams have mastered local feedback loops.

### Concrete investment map for this team

```
Target zone: Focusing + Delivering, with selective Optimizing

Focusing practices to invest in first
- Milestone goals written as business / bring-up / validation outcomes.
- Sprint reviews demonstrate evidence, not activity.
- Product, validation, RTL, DV, host, and release dependencies visible in planning.
- Scope tradeoffs explicit: must / should / not now.
- Status reports include confidence, risk, ask, and next proof point.

Delivering practices to invest in first
- Small changes.
- Trunk-friendly integration.
- CI build for every relevant Zephyr target.
- Twister / ztest coverage for firmware logic.
- native_sim / QEMU / mock testing for hardware-independent code.
- Emulator / FPGA / HIL smoke tests for hardware-facing code.
- Definition of Done by evidence tier.
- Debug audit trail attached to issues.

Selective Optimizing
- Use customer / validation / first-silicon priorities to choose scope.
- Let the FW lead recommend milestone tradeoffs, but make authority explicit.
- Compare options by program risk, not only engineering preference.

Defer
- Heavy process office.
- SAFe-style scaling.
- Organization-wide metrics regime.
- Large ceremony set before CI, RAID, and status hygiene work.
```

### Quotable guiding principles

- “For this team, Agile Fluency should mean evidence fluency.”

- “Do not buy a scaling framework to solve a five-person feedback problem.”

### Counter-arguments and anti-patterns

- **Anti-pattern: treating Agile Fluency as a maturity score.** The model itself says the right destination depends on context.

- **Anti-pattern: ceremonies before engineering practices.** Delivering fluency requires technical discipline, not just standups and sprint boards.

- **Counter-argument: startups need Optimizing.** True, but only where decision rights exist. If product and milestone authority sit elsewhere, the FW planner should support tradeoff recommendations rather than pretending the team has full market authority.

- **Anti-pattern: Strengthening too early.** Organization-wide process is premature when the five-person team still lacks evidence-tier DoD, CI coverage, RAID discipline, and cross-functional planning clarity.

---

# Synthesis: 10 opinions the AI team-planner skill should bake in

1. **Plan around evidence, not activity.** For firmware, a story is not meaningfully done until it has the right evidence tier: build, contract, pre-silicon proxy, lab/silicon run, or validation/release acceptance.

2. **Treat hardware dependencies as first-class schedule objects.** RTL drops, CSR freezes, emulation windows, FPGA access, boards, host drivers, validation slots, and release windows deserve owners, dates, and RAID rows.

3. **Shift bring-up left.** Diagnostics, smoke tests, scripts, log capture, and failure triage templates should exist before first silicon. First silicon should test silicon, not the team’s preparation.

4. **Use two planning clocks.** The two-month milestone is for risk narrative, cross-functional contracts, and evidence targets; the two-week sprint is for capacity-limited execution and the next proof point.

5. **Keep the process small enough to survive real work.** For a five-person team, the minimum useful system is: milestone one-pager, sprint plan, evidence-tier DoD, RAID log, weekly 5-15, and milestone retro/pre-mortem.

6. **Use points for forecasting and focus-days for sprint commitments.** Do not convert points to hours. Use historical velocity as a calibration signal, and use focus-days to decide what can actually fit in the next sprint.

7. **Measure blocked external days.** A firmware planner should distinguish “we are slow” from “we are waiting for RTL/lab/host/board/validation.” This is one of the most important firmware-specific health metrics.

8. **Escalate with options, not complaints.** Every escalation should say: state, what changed, impact, options, recommendation, ask, and next proof point.

9. **Use AI as a dependency radar and status drafter, not as an oracle.** The assistant should flag missing evidence, stale assumptions, unowned dependencies, and contradictory Jira/spec/log data. It should not silently mark work done or invent certainty.

10. **Aim for Focusing + Delivering fluency.** The team needs customer/program-visible priorities and strong technical delivery practices before it needs scaling frameworks, enterprise metrics, or heavy ceremony.

---

# Recommended reading list

1. **James Grenning — “Agile Embedded Software Development.”** Highest-value source for adapting Agile to embedded realities: late hardware, pre-hardware simulation, acceptance tests, relative estimates, velocity, and hardware/software integration stories.

2. **Elecia White — *Making Embedded Systems*.** Best grounding text for why embedded systems differ from general software: constraints, determinism, peripherals, manufacturing, and robust low-level design.

3. **Semiconductor Engineering — “Transforming Silicon Bring-Up.”** Strong industry source on pre-silicon/post-silicon continuity, diagnostics before silicon, smoke reuse, and common environments.

4. **SoC Labs — “Post Silicon.”** Useful structured view of post-silicon phases: power-up, communication, boot, subsystem validation, performance/power validation, and debug limitations.

5. **OSFC 2024 — “Getting your open source software ready for 0-day SoC bringup.”** Practical conference-talk framing for software readiness before silicon, simulators/emulators, and upstream-first bring-up preparation.

6. **Hot Chips UCIe and CXL tutorials.** Particularly relevant to chiplet / high-speed-interface firmware because they expose reset/init, register access, software, RAS, compliance, protocol, and debug-observability dependencies.

7. **Embedded Artistry — hardware/firmware coupling and David Agans debugging rules.** Useful for coding and planning principles: abstraction from hardware/RTOS, unit-testability, audit trails, reproducible failures, and disciplined debug.

8. **Will Larson / StaffEng — team sizing, weekly 5-15s, internal comms, engineering metrics, strategy, and technical quality.** Best management source set for lean, credible, startup-compatible engineering leadership.

9. **Camille Fournier — startup engineering management / engineering management as technical discipline.** Good grounding for feedback loops, tech debt, support drag, and why engineering management cannot be divorced from technical judgment.

10. **Agile Fluency Model — Diana Larsen / James Shore / Martin Fowler.** Best model for choosing “enough Agile” without turning a five-person firmware team into a process program.