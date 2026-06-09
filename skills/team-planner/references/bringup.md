# Bring-Up Readiness Checklist

## Pre-Tapeout / T-8 to T-4 Weeks

| # | Item | Status | Owner | Evidence |
|---|------|--------|-------|----------|
| 1 | Boot path named: BootROM → FSBL/loader → Zephyr init → interface FW | | | |
| 2 | Current CSR/register map revision recorded | | | |
| 3 | Reset, clock, power, and strap assumptions documented | | | |
| 4 | PCIe / UCIe / D2D minimum smoke sequence defined | | | |
| 5 | Host-driver minimum dependency named | | | |
| 6 | Diagnostics compile and run in sim, emulation, FPGA, or model | | | |
| 7 | JTAG, serial, mailbox, trace, and log collection scripts drafted | | | |
| 8 | Known model gaps captured as RAID assumptions | | | |
| 9 | Bring-up owner + backup owner named | | | |
| 10 | RTL contact, DV contact, validation contact, lab contact named | | | |

## T-2 Weeks

| # | Item | Status | Owner | Evidence |
|---|------|--------|-------|----------|
| 11 | Lab slots booked | | | |
| 12 | Board allocation plan agreed | | | |
| 13 | Power supplies, probes, JTAG, serial adapters, host machines confirmed | | | |
| 14 | Smoke test pass/fail table frozen | | | |
| 15 | Errata / workaround template ready | | | |
| 16 | War-room communication channel created | | | |
| 17 | First 48-hour decision owner named | | | |

## First Power

| # | Item | Status | Owner | Evidence |
|---|------|--------|-------|----------|
| 18 | Visual inspection complete | | | |
| 19 | Power rail impedance / shorts checked | | | |
| 20 | Power applied in controlled sequence | | | |
| 21 | Boot logs captured | | | |
| 22 | JTAG attach confirmed | | | |
| 23 | Clocks and resets checked | | | |
| 24 | BootROM or first-stage loader reached | | | |
| 25 | FW image loaded or download path verified | | | |
| 26 | First console / mailbox / trace output observed | | | |
| 27 | First interface smoke attempted (PCIe enum, UCIe training, D2D link) | | | |
| 28 | Every failure has: owner, evidence link, suspected domain, next experiment | | | |

## Exit from First-Boot Phase

| # | Item | Status | Owner | Evidence |
|---|------|--------|-------|----------|
| 29 | Reproducible boot or controlled failure sequence exists | | | |
| 30 | Debug artifacts attached | | | |
| 31 | Top silicon / RTL / board / FW risks in RAID | | | |
| 32 | Next subsystem gate named | | | |

## Usage

- Walk this checklist as part of the **bring-up readiness** capability.
- For each item, check Jira / RAID / local state for evidence.
- Flag gaps as Red (missing, no plan) / Yellow (in progress) / Green (done with evidence).
- Produce a go/no-go summary table.
