# Evidence Tiers — Firmware Definition of Done

Use evidence tiers instead of a flat "Done = merged" rule. Each story should declare its target tier at planning time.

## Tiers

### L0 — Static / Build Evidence
- Builds for target configuration.
- Builds for `native_sim`, QEMU, or equivalent host-side target where applicable.
- Unit tests or ztest-style tests pass.
- Twister / CI job attached to Jira issue.

### L1 — Contract Evidence
- Register map, reset values, mailbox/API, interrupt contract, and error codes match current spec revision.
- Spec revision or CSR hash is recorded in the issue.
- Host-driver, RTL, DV, or validation owner has reviewed the contract if affected.

### L2 — Pre-Silicon Executable Evidence
- Feature demonstrated in simulation, virtual platform, RTL sim, emulator, FPGA, or mocked hardware model.
- Pass/fail log attached.
- Known model gaps listed as assumptions or RAID rows.

### L3 — Lab / Silicon / HIL Evidence
- Runs on board, FPGA, emulator, or first silicon using repeatable script.
- Power-cycle or reset behavior captured if relevant.
- Logs, traces, register dumps, or waveform references attached.
- Debug steps are reproducible by another engineer.

### L4 — Release / Validation Evidence
- Validation owner accepts result or signs off on waiver.
- Errata, workaround, feature flag, release note, or host-driver dependency documented.
- Failure modes and recovery behavior tested or explicitly deferred.

## Jira Status vs. Evidence Tier

- **`In Review` status = code is done** (development complete, PR submitted, awaiting review/merge). This is a **code-complete** signal, equivalent to the implementation portion of the target tier being finished.
- Code-complete is **not** the same as the tier being met: `In Review` does not by itself satisfy L1–L4 evidence (contract sign-off, lab/silicon runs, validation acceptance). A story stays short of "Done" until both it is merged/closed **and** its target tier evidence exists.
- For forecasting and capacity, treat `In Review` work as development-done; for Definition of Done, hold it to the tier rule below.

## Usage Rules

- Every story must declare its **target evidence tier** at sprint planning.
- A story is not "Done" until its target tier is met or an explicit waiver with RAID row exists.
- The tier may be adjusted during the sprint if circumstances change (e.g., lab unavailable → L2 instead of L3), but the adjustment must be recorded.
- Higher tiers subsume lower tiers: L3 implies L0-L2 are also met.

## Tier Selection Guide

| Story type | Typical target tier |
|---|---|
| Pure SW logic, no HW interaction | L0 |
| Register/CSR programming, API contract | L1 |
| IP integration, driver init, link training | L2 (pre-silicon) or L3 (if HW available) |
| Bring-up diagnostic, lab smoke test | L3 |
| Validation handoff, release candidate | L4 |
