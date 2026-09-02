# A2 (Atlantic2) — HSI Jira Landscape

- **Recorded:** 2026-08-30
- **Status:** Verified from Jira (ASIC Project == Atlantic2)
- **A2-vs-A1 delta:** HSI A2 planning is **nascent** — no HSI Initiatives yet; work is a Backlog
  of mostly PCIe **PLDM (Palladium emulation) enablement** Attributes + a few PLDM Stories.
- **Tags:** jira, hsi, atlantic2, a2, tickets, initiatives, epics, attributes, pldm, palladium,
  planning, backlog

## Scope / Question
All HSI-team tickets with `ASIC Project == Atlantic2`, and the broader A2 initiative/epic
landscape across other IP teams.

> **For FW planning** (which tickets to file FW work under, missing FW work, redundant tickets),
> see **[a2-fw-planning-gaps.md](a2-fw-planning-gaps.md)** — project-scoped view
> (`project in (IPSESW, IPPCIESW, IPD2DSW)`) = **155** issues, incl. the 74 architecture
> Initiatives that the team-scoped query above does not return.

## Findings (A2)

**HSI-team A2 tickets** — JQL: `"Team[Team]" = 705f36d3-e7b3-4b7e-a220-d28f31f013ab AND
cf[10334] = "Atlantic2"` → **50 tickets**: 5 Story, 1 Epic, 44 Attribute. **All Backlog**
(except 1 Closed legacy). Component breakdown: overwhelmingly `ip-pcie-firmware-ep`, a few
`ip-se-firmware` and `ip-d2d-firmware`.
- **Stories (5)** — all "A2 PLDM" (Palladium enablement):
  - IPSESW-327 A2 PLDM – C2C enablement on multi-chip image
  - IPSESW-326 A2 PLDM – C2C enablement on PLDM
  - IPPSOCSW-1015 A2 PLDM – Manageability PCIe enablement on PLDM
  - IPPCIESW-625 A2 PLDM – D2D enablement on PLDM
  - IPPCIESW-624 A2 PLDM – PCIe Resets enablement on PLDM
- **Epic (1)**: IPINFRASW-1921 "Move manifest elpci-ci to use dynamic runners" (CI infra;
  parent IPINFRASW-1900).
- **Attributes (44)**: PCIe/PLDM verification-enablement backlog — ELBI (access controller,
  indirect FSM, DOE, INT gen), PLDM DS/US message mapping to INT/MBX, FLR (HW/FW/autocompletion),
  MSI-X coalescing, reset isolation (PCIe Ctrl / PIW), secure BAR, error counters, LTSSM logger,
  Emergency Power Break, Cache Line Split, BAR mapping through BMU, PSOC FW Mailbox, DSN/Vendor-ID
  reads on PLDM, downstream access termination (reset flow + uncorrectable error), etc.
- **"PLDM" here = Palladium emulation** (not PLDM-over-MCTP manageability) — these are
  bring-up/verify-on-emulation tasks. (Cross-check with the `pldm` / `pldm-job-queue` skills.)

**Broader A2 landscape (all teams, Initiative+Epic)** — `cf[10334]="Atlantic2" AND issuetype in
(Initiative, Epic)` returns 100+ (31 Initiative, 69 Epic) spread across NON-HSI IP teams:
IPALSW/IPALGRRTL (Aligner), IPCCESW/RTL/VAL (CCE — e.g. "Change CCE order L1-Chip/L2-Die/L3-Sled",
redundant compute, hierarchical computation), IPARMCPUSW (ARM CPU), IPHSM (HSM), IPDMAH, etc.
These are other teams' A2 work — HSI's slice is the 50 tickets above.

## A2 vs A1 (Iridium) delta
- HSI A2 work has **not yet been organized into initiatives/epics** — it's a flat Backlog of
  Palladium-enablement attributes, largely carried/adapted from the Iridium PCIe/PLDM verification
  surface. Contrast with Iridium, which has a full initiative→epic→story milestone hierarchy.
- CCE architecture is evolving for A2 (e.g. IPCCESW-107 "Change CCE order (L1-Chip, L2-Die,
  L3-Sled)", IPCCESW-103 "redundant compute") — reflects the 4-die + sled topology.

## Sources
- Jira JQL (cloudId c155e965-159d-4564-a0a0-16eb85204b7f):
  `"Team[Team]" = 705f36d3-e7b3-4b7e-a220-d28f31f013ab AND cf[10334] = "Atlantic2"` (50 issues).
  `cf[10334] = "Atlantic2" AND issuetype in (Initiative, Epic)` (100+ across non-HSI teams).
- HSI team field ID `705f36d3-e7b3-4b7e-a220-d28f31f013ab`; ASIC Project = `customfield_10334`.
