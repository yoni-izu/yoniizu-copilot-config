# Retrospective & Pre-Mortem Templates

## Firmware Milestone Retrospective

Use after each milestone closes. Write output to `~/team-planner/retros/<milestone>.md`.

```
Milestone <name> Retrospective — <date>

1. Evidence We Moved Left
- Which bugs were caught at each stage: unit, native_sim, RTL sim, emulator, FPGA, HIL, silicon?
- Which late bugs could have been caught earlier? What was missing?

2. Lab-Only Surprises
- What first appeared only in the lab?
- Why did our pre-silicon proxy miss it?
- What instrumentation was missing?

3. Ambiguous HW/SW Bugs
- Which failures were hard to classify as HW vs SW?
- How long did classification take?
- What debug tool, trace, or register was missing?

4. Dependency Truth Table
| Partner | Promised | Delivered | Changed | Impact |
|---------|----------|-----------|---------|--------|
| RTL     |          |           |         |        |
| DV      |          |           |         |        |
| Validation |       |           |         |        |
| Host SW |          |           |         |        |
| Release |          |           |         |        |

5. Debug Audit Trail
- Could a different engineer reproduce each major failure?
- Were logs, register dumps, traces, scripts, and board setup captured?

6. RAID Reconciliation
- Which issues were predicted as risks?
- Which issues were NOT predicted?
- Which assumptions were wrong?
- Which dependencies need earlier escalation next time?

7. Capacity vs Actuals
- Planned focus-days vs actual
- Blocked external days (by category)
- Unplanned debug days
- Lab wait days

8. One Change for Next Milestone
- Process change:
- Test/tool change:
- Cross-functional contract change:
- Owner:
- Due:
```

### Key principle
> "A good firmware retro leaves behind a better test bench, not just better feelings."

---

## Pre-Mortem

Use before starting a milestone. Write output to `~/team-planner/premortems/<milestone>.md`.

### Prompt

> "It is 60-90 days from now and this milestone has failed. What went wrong?"

### Procedure

1. Each participant (or the AI, simulating team perspectives) identifies 3-5 failure scenarios.
2. Group by theme.
3. Vote on likelihood (each person gets 3 votes).
4. For each top risk:
   - Owner
   - Trigger (observable condition)
   - Mitigation
   - Contingency
   - Date to revisit

### Firmware-specific failure prompts

```
- What spec or CSR change could invalidate our current code?
- Which dependency has no confirmed delivery date?
- Where are we assuming HW behavior we haven't verified?
- Which engineer is a single point of failure for a critical path?
- What lab/emulation/FPGA contention could block us?
- Which host-driver or validation assumption is untested?
- What if first silicon has an errata in our IP area?
- What cross-functional handoff has no verification plan?
```

### Output

Top risks → RAID rows in `~/team-planner/raid.md` with:
- Type: Risk
- Source: Pre-mortem for `<milestone>`
- Full scoring (P × I), RAG, trigger, mitigation, contingency, due
