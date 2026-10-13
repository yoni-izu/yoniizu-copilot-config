# Pilot Sessions

One note per flight: `<station>-<YYYYMMDD-HHMM>.md`. Opened at Step 0, closed at
Step 6 after the memoir/pitfalls were updated. Keep it factual; the memoir gets the
2–4 line summary, this file keeps the detail.

```markdown
# <station> — <YYYY-MM-DD HH:MM UTC> — <topic>

**Goal:** <what the user asked for>
**Setup (memoir says / probe says):** <type> / <type> — match? <yes|no — why>
**Allocation:** <free | allocated to X (approved by user: yes/no)>
**Devices:** <k/8 or 1/1>, vendor <208b|1d0f>, BDFs <…>, driver <el_pci|none>, link <…>

## Actions (UTC, class, command, exit)
- 14:02 observe `lspci -d 1d0f: -nn` → 6 devices
- 14:05 touch  (approved) `…` → rc=0
- 14:10 disturb (approved) `…` → …

## Evidence
- `~/.pilot/evidence/<station>/<UTC>_<topic>/` (sha256sums present)
- key lines: <dmesg/UART excerpts, 3–6 lines max>

## Findings
- *observed*: …
- *inferred*: …
- anomalies: <missing OAMs, link down, atl errors, port holders>

## Handoffs
- fw-flasher / fw-deploy / bug-triage: <what and why> | none

## Memoir / pitfalls updated
- memoir: <registry row changed? activity entry added>
- pitfalls: <entry added | none>

**Status:** closed
```
