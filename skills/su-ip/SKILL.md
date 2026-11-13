---
name: su-ip
description: 'Answer Scale Up (SU) IP design, usage, C2C, PCIe DM controller and project-specific PHY questions using mapped Git specifications, available vendor collateral and Neo for RTL. Use when: scale-up, SU link, C2C, chip-to-chip, PCIe dual-mode controller, DM controller, SU PHY, SU PCIe, SU registers, SU LTSSM, SU equalization, SU loopback, SU PRBS, multi-socket, scale-up interconnect.'
argument-hint: 'Ask a question about Scale Up IP design, C2C protocol, DM controller, or PHY behavior'
---

> **Portal handoff:** For Team Portal/dashboard questions, feature requests,
> UI/API changes, bugs or operations, load
> [portal-maintainer](../portal-maintainer/SKILL.md). Domain records and
> workflows remain owned by this skill; coordinate mixed changes with the maintainer.

# Scale Up (SU) IP Documentation Q&A

## When to Use

- Questions about Scale Up (SU) link design and operation
- DWC PCIe Dual-Mode (DM) controller behavior, registers, or configuration
- C2C (chip-to-chip) protocol and specification
- SU PHY questions (project/revision-specific PCIe PHY — see vendor-source caveats below)
- SU link training, equalization, LTSSM behavior
- SU PRBS, loopback, BER testing
- Multi-socket / scale-up interconnect architecture
- RTL implementation questions

## Architecture Context

- **Controller**: Synopsys DWC PCIe Dual-Mode (DM) controller — supports both Root Complex (RC) and Endpoint (EP) modes
- **PHY**: project- and revision-specific. Neo's A2 mirror review identifies a Synopsys PCIe7 PHY integration (1.10a_pre1, TSMC 3nm); the exact PHY variant and its relationship to the EP PHY require the applicable vendor documentation. Do not generalize A1/EP PCIe6 details to A2 SU.
- **C2C Protocol**: Element's internal chip-to-chip protocol running over the PCIe DM controller
- **Use Case**: High-bandwidth scale-up links between SoCs (e.g., SU ↔ SU peer-to-peer)

## Boot Flow Responsibilities

Reference: [Boot flow](https://elementlabs.atlassian.net/wiki/spaces/SWENG/pages/598671382/Boot+flow) (SWENG space).

FW boots through staged, chained-authenticated images (root of trust starts at FLA.ROM). The MuC-resident runtime stages are **FLA.RT → PRIMEBOOT → MANAGEMENT**; **POWER & THERMAL** runs on the PTuC. Drivers are split into **INIT** (one-time bring-up) and **RUNTIME** (interrupt/operational) components.

Scale Up (SU / C2C) responsibilities by stage (per the proposed driver-functionality split):

| Driver / Module | FLA.RT | PRIMEBOOT | MANAGEMENT | POWER & THERMAL |
|-----------------|--------|-----------|------------|-----------------|
| EL C2C          | —      | —         | FULL       | —               |
| EL SE           | —      | —         | FULL       | —               |

- SU is brought up **entirely in MANAGEMENT** (FULL) — both the C2C protocol driver (`EL C2C`) and the Scale Up engine (`EL SE`, repo `ip.se.sw.fw`). It is **not** active in FLA.RT, PRIMEBOOT, or POWER & THERMAL.
- The boot-flow page enumerates PCIe PHY FW for the EP and UCIe PHY FW for D2D, but does not separately call out an SU PHY FW load. That omission does not establish whether A2 SU requires a distinct PHY image or shares one with another block; confirm the project-specific sequence with the owning FW/PHY teams and vendor materials.

> The Init-vs-Runtime split is still evolving: after tape-out (TO) some functionality may shift between stages. Treat this as the current plan, not a frozen contract.

## Document Catalog

### Git specification sources

Use the [scale-up / SE Git source map and pinned lookup](../../installed-plugins/ai-toolkit/fw-soc/references/specification-sources.md#scale-up-and-se):
general architecture in `Elmnt-Internal/module.arch.hwarch.specs`; A2 C2C
and verified legacy A1 context in `Elmnt-Internal/project.atlantic2.hwarch.specs`,
both on `main`. The deprecated project repo is retained by explicit approval.
A2 C2C's cover is verified Rev2.1.1; the exact A1 C2C and earlier A2 originals
are unverified. Historical notes and revision-comparison claims are secondary.

Synopsys DM controller/DBI references, guides, errata and PCIe PHY/PCS
manuals listed below are **unavailable in the verified two-repo catalog**.
Preserve their vendor authority; internal C2C integration specs are not
replacements. Report missing originals instead of using local-folder fallbacks.

### Historical DM controller / C2C document titles

| File | Pages | Content |
|------|-------|---------|
| `DWC_pcie_ctl_dm_databook.pdf` | 1064 | DM controller architecture, features, RC+EP mode programming, integration |
| `DWC_pcie_ctl_reference.pdf` | 18845 | Complete register reference — all config space and DBI registers (DM variant) |
| `DWC_pcie_ctl_user.pdf` | 356 | Integration guide, clocking, reset, bring-up sequences |
| `DWC_pcie_ctl_relnotes.pdf` | 38 | Release notes, known issues, errata |
| `PCIe C2C Rev 2.1.1.docx` | — | **A2 (Atlantic2)** internal Element C2C spec — 2nd-gen scale-up; **PCIe-Compliant Mode** + **Dual-Mode Port** (RP/EP), Gen7, FLIT/Non-FLIT |
| `PCIe C2C Rev 2.0.0.docx` | — | Earlier A2 C2C revision |
| `PCIe C2C.docx` | — | Historical **A1/Iridium** title; exact Git original unverified, not available for a verified revision diff |

> **Project note (A2 / Atlantic2 vs A1 / Iridium):** the A2 C2C spec (`PCIe C2C Rev 2.1.1.docx`)
> is the **2nd generation** scale-up interconnect and is **~2.5× larger** than the A1 baseline
> (`PCIe C2C.docx`). The headline A2 delta is a **PCIe-Compliant Mode** built on a **Dual-Mode
> (DM) PCIe controller**, statically configurable as **Root Port (RP) or Endpoint (EP)**, to
> **interoperate with third-party PCIe devices (GPUs)**. Highlights: up to **Gen7 (128 GT/s)**,
> FLIT + Non-FLIT modes, PCIe Base Spec Rev 7.0 compliance, ~100 ns/dir latency; Switched (EP
> behind switch) + Switchless P2P (RP↔EP) topologies; PCIe Rail + Dragonfly topologies; ordering
> models (compliant vs non-compliant), aggregation buffer, GPU producer/consumer support; RAS
> (ECRC/LCRC/AER, SECDED ECC, poison/viral, automatic lane failover, multiple concurrent links).
> For project context see `atlantic2-prj`.

### PCIe PHY vendor manuals (unavailable in the verified Git catalog)

The following titles describe PCIe6 PHY collateral historically associated with PCIe/EP.
Their applicability to A2 SU is **not verified**; A2 RTL review instead identified a PCIe7
PHY integration. Do not use these manuals as evidence for A2 SU until the exact vendor
revision and integration are confirmed:

| File | Pages | Content |
|------|-------|---------|
| `dwc_pcie6phy_g2_tsmc5ff_x4ns_databook.pdf` | 200 | PHY architecture, PRBS/loopback, lane margining, signal integrity |
| `dwc_pcie6phy_g2_tsmc5ff_x4ns_reference.pdf` | 1991 | Complete PHY register reference (APB registers) |
| `dwc_pcie6_phy_pcs_wrapper_databook.pdf` | 206 | PCS wrapper architecture and registers |
| `dwc_PCIe6_SDK_appnote.pdf` | 128 | SDK usage, test patterns, validation flows |
| `dwc_pcie6_phy_firmware_appnote.pdf` | 60 | PHY firmware loading, calibration, training |
| `dwc_pcie6phy_corekit_user.pdf` | 50 | Integration, clocking, reset sequences |
| `dwc_pcie6_phy_ate_testbench_appnote.pdf` | 40 | ATE testing, production test patterns |
| `dwc_pcie6_phy_fusion_compiler_appnote.pdf` | 32 | Synthesis/P&R guidance |
| `dwc_pcie6_phy_gate_level_simulations_appnote.pdf` | 28 | Gate-level sim setup |
| `dwc_SerDes_pcb_packaging_design_guide.pdf` | 112 | PCB layout, SerDes routing |
| `dwc_serdes_phy_occ_distribution_routing_guidelines_appnote.pdf` | 10 | OCC clock distribution |
| `dwc_pcie6phy_g2_tsmc5ff_x4ns_config_reference_manual.xlsx` | — | PHY configuration spreadsheet |
| `readme_2.63a_cust4_pre1.txt` | — | Release notes for PHY v2.63a |

## Source Code Repositories

| Repo | GitHub URL |
|------|------------|
| RTL | https://github.com/Elmnt-Internal/ip.se.rtl |
| Firmware | https://github.com/Elmnt-Internal/ip.se.sw.fw |

> **RTL branch strategy:** In the RTL repo, the **`main`** branch holds the **latest
> implementation for the most recent project** we are working on (currently **Atlantic**).
> Each **previous** project is frozen on its own **freezed RTL** branch — e.g. **Iridium**'s
> RTL lives on the Iridium *freezed rtl* branch, not on `main`. Neo knows both: ask
> with `--chip a1` (Iridium freeze) or `--chip a2` (Atlantic/A2 `main`).

**Source strategy**:
1. Check if FW driver code exists locally — common workspace paths:
   - `fw/ip/se/` or `ip/se/`
   - Also check the project manifest (e.g., `.star` files or `octl` manifests) for the actual deployment path
2. Read FW and CSR software headers locally, or search their specific software repos on GitHub.
3. For RTL, follow [rtl-vm-policy](../rtl-vm-policy/SKILL.md): ask **Neo** —
   `~/bin/neo-ask --chip a1|a2 "<question>"` — and relay only its paraphrased
   findings and citations (repo, path, symbol, mirror commit), never HDL or
   patches. No local/VM checkout or GitHub RTL reads. If Neo is unavailable,
   report the prerequisite and stop.

## DM Controller vs EP Controller — Key Differences

| Aspect | DM Controller (SU) | EP Controller (PCIe EP) |
|--------|-------------------|------------------------|
| Modes | RC + EP (dual-mode) | EP only |
| Databook | `DWC_pcie_ctl_dm_databook.pdf` (1064p) | `DWC_pcie_ctl_ep_databook.pdf` (952p) |
| Extra docs | `DWC_pcie_ctl_user.pdf`, relnotes | — |
| Register ref | Same format, DM variant registers | Same format, EP variant registers |
| Use case | SU ↔ SU links, can act as RC or EP | Host-facing endpoint |
| PHY | A2 Neo mirror identifies Synopsys PCIe7 1.10a_pre1, TSMC 3nm; relationship to EP-specific PHY is unverified | PCIe6 collateral is listed historically; A2 applicability is unverified |

## Knowledge Base (Expert Memory)

This skill keeps an **expert memory** of everything it has already researched and verified, so
repeat questions on the same subject are answered fast without re-reading the (very large) DM
controller docs, PHY docs, C2C spec, or RTL.

- **Location:** `~/.copilot/skills/su-ip/knowledge/`
- **Index:** `~/.copilot/skills/su-ip/knowledge/INDEX.md` — catalog of recorded topics
- Detail files: `knowledge/<topic-slug>.md` — verified findings with full citations

Consult source-free knowledge first when available (Step 0) and update it with
paraphrased findings after non-trivial research (final step). Cross-link a PHY
fact to `pcie-ep-ip` only after independently confirming its project, vendor
revision and applicability to both IPs.

> **Recording trigger — read this twice.** Record a finding whenever you **verify** a
> non-trivial IP fact during this turn, regardless of *why* you were invoked. The
> trigger is the verification, not the question shape. This fires when:
> - the user asked you directly (the obvious case);
> - another skill (e.g. `pr-review`, `iridium-prj`, `atlantic-prj`, `ras-manageability`) consulted you;
> - you read FW driver code, generated register headers, or RTL during a code review
>   and locked in a durable register/protocol/sequence/erratum fact;
> - you reconciled two sources (databook vs RTL, C2C spec vs FW) and resolved which is current.
>
> **"Non-trivial IP fact"** = anything a future query could re-ask: a register's
> reset/behaviour, a field/bit semantic, a bring-up or training sequencing rule, a
> C2C protocol nuance, a DM controller / PHY quirk, a cross-IP interaction, a HW–FW
> contract. If in doubt, **record**. A too-detailed knowledge base costs milliseconds;
> a missing entry costs the next query a full re-read of an 18k-page databook.
>
> Skip only: pure code-style nits, project-specific FW architecture (belongs in the
> project skill, e.g. `iridium-prj` / `atlantic-prj`), tooling/CI/Python issues, and
> re-statements of facts already fully covered by an existing entry.

## Procedure

### Step 0: Consult the Knowledge Base (do this first)

1. Use the topic names in this skill. Consult an index or note only if it is
   already known not to contain HDL excerpts; do not bulk-search the knowledge
   directory, which may contain historical source excerpts.
2. If no verified source-free note is available, proceed using the specifications
   or ask Neo (`neo-ask`) for a paraphrased finding.
3. If a recorded finding fully answers the question, use it (cite it). Otherwise use it to
   **narrow** the search before diving into the docs/RTL.

### Step 1: Classify the Question

Determine which document(s) are most relevant:

| Topic | Primary Doc | Secondary Doc |
|-------|------------|---------------|
| DM controller registers | `DWC_pcie_ctl_reference.pdf` (C2C dir) | `DWC_pcie_ctl_dm_databook.pdf` |
| DM controller architecture/features | `DWC_pcie_ctl_dm_databook.pdf` | |
| DM integration/bring-up | `DWC_pcie_ctl_user.pdf` | `DWC_pcie_ctl_dm_databook.pdf` |
| RC mode specifics | `DWC_pcie_ctl_dm_databook.pdf` | Controller reference |
| Known issues/errata | `DWC_pcie_ctl_relnotes.pdf` | |
| C2C protocol/spec | Mapped A2 C2C Markdown, cover Rev2.1.1 | General C2C, not an A1 original |
| PHY registers (APB) | `dwc_pcie6phy_g2_tsmc5ff_x4ns_reference.pdf` | |
| PHY architecture, PRBS, loopback | `dwc_pcie6phy_g2_tsmc5ff_x4ns_databook.pdf` | |
| PCS wrapper | `dwc_pcie6_phy_pcs_wrapper_databook.pdf` | |
| PHY firmware/calibration | `dwc_pcie6_phy_firmware_appnote.pdf` | |
| RTL implementation | Neo — `~/bin/neo-ask --chip a1\|a2 "…"` | |

### Step 2: Search the Document

Resolve a commit and fetch the exact mapped C2C Markdown with `gh api` as shown
in the source map. Verify project/revision; search headings, text and tables.
Cite repository, commit, path and section/table. For vendor-specific DM/PHY
questions, report the missing manual/revision; the historical filename catalog
does not imply availability and internal integration Markdown is not a substitute.

### Step 3: Extract and Answer

1. Search with broad keywords first, then narrow down
2. Generated CSR lookups use `csr-bot`; vendor registers need the exact manual.
3. DM implementation details require the DM databook, unavailable in this catalog.
4. The PCIe6 PHY manuals listed here are unavailable and do not establish A2 SU or EP behavior.
5. Cite Git provenance and section/table; old PDF pages are historical locators.
6. If the docs don't cover it, ask Neo (`~/bin/neo-ask`) for a paraphrased RTL
   finding; never use GitHub, local or VM-worktree RTL reads.

### Step 4: Record Verified Findings (do this last)

After answering a non-trivial question, persist what you verified so the skill remembers it:

1. Create or update `~/.copilot/skills/su-ip/knowledge/<topic-slug>.md` using the template in
   `INDEX.md`. Keep it concise, durable, and reusable.
2. Add or refresh the topic's row in `INDEX.md` (newest at top) with tags, status, and date.
3. Always include citations (doc + page, RTL repo + file + symbol) so the finding can be
   re-verified and trusted.
4. If new research contradicts a prior entry, **correct** that entry rather than leaving stale
   information.

Skip recording only for trivial lookups already fully covered by an existing entry.

### Search Strategy Tips

- **DM vs EP registers**: The DM reference may have additional RC-mode registers not present in the EP reference. Search the C2C-dir reference for DM-specific registers.
- **RC mode**: Search DM databook for "root complex", "RC mode", "type 1"
- **Dual-mode switching**: Search for "mode switch", "dual mode", "DM"
- **C2C protocol**: search the mapped Git Markdown, including register tables.
- **PHY questions**: identify the project and exact vendor PHY revision first. The PCIe6
  manuals listed here are unavailable in the verified catalog and do not establish A2 SU behavior.
- **The 18845-page reference**: Always search by keyword first, never scan sequentially

## Important Notes

- The DM controller reference is 18845 pages — ALWAYS use keyword search, never sequential scan
- Do not assume SU and EP share a PHY across projects; internal C2C specs do not replace vendor authorities.
- PHY registers are per-lane: offset = base + lane*0x400 + register_offset
- For questions that span both controller and PHY, cross-reference both databooks
- The DM controller has superset functionality vs EP controller (includes RC mode)
