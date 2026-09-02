---
name: su-ip
description: 'Answer Scale Up (SU) IP design, usage, C2C, and PCIe DM controller questions using Synopsys DWC PCIe DM controller docs, PCIe6 PHY docs, and internal C2C specification. Use when: scale-up, SU link, C2C, chip-to-chip, PCIe dual-mode controller, DM controller, SU PHY, SU PCIe, SU registers, SU LTSSM, SU equalization, SU loopback, SU PRBS, multi-socket, scale-up interconnect.'
argument-hint: 'Ask a question about Scale Up IP design, C2C protocol, DM controller, or PHY behavior'
---

# Scale Up (SU) IP Documentation Q&A

## When to Use

- Questions about Scale Up (SU) link design and operation
- DWC PCIe Dual-Mode (DM) controller behavior, registers, or configuration
- C2C (chip-to-chip) protocol and specification
- SU PHY questions (same PCIe6 PHY as EP — see PHY docs below)
- SU link training, equalization, LTSSM behavior
- SU PRBS, loopback, BER testing
- Multi-socket / scale-up interconnect architecture
- RTL implementation questions

## Architecture Context

- **Controller**: Synopsys DWC PCIe Dual-Mode (DM) controller — supports both Root Complex (RC) and Endpoint (EP) modes
- **PHY**: **Same Synopsys DWC PCIe6 PHY v2.63a as PCIe EP** — identical SerDes, PIPE interface, APB registers
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
- The boot-flow page enumerates **PCIe PHY FW** (for the EP, loaded by FLA.RT) and **UCIe PHY FW** (for D2D, loaded by PRIMEBOOT) image loads, but does **not** separately call out an SU PHY FW load — the SU reuses the **same PCIe6 PHY** as the EP.

> The Init-vs-Runtime split is still evolving: after tape-out (TO) some functionality may shift between stages. Treat this as the current plan, not a frozen contract.

## Document Catalog

### DM Controller Documents (`~/docs/C2C/`)

| File | Pages | Content |
|------|-------|---------|
| `DWC_pcie_ctl_dm_databook.pdf` | 1064 | DM controller architecture, features, RC+EP mode programming, integration |
| `DWC_pcie_ctl_reference.pdf` | 18845 | Complete register reference — all config space and DBI registers (DM variant) |
| `DWC_pcie_ctl_user.pdf` | 356 | Integration guide, clocking, reset, bring-up sequences |
| `DWC_pcie_ctl_relnotes.pdf` | 38 | Release notes, known issues, errata |
| `PCIe C2C Rev 2.1.1.docx` | — | **A2 (Atlantic2)** internal Element C2C spec — 2nd-gen scale-up; **PCIe-Compliant Mode** + **Dual-Mode Port** (RP/EP), Gen7, FLIT/Non-FLIT |
| `PCIe C2C Rev 2.0.0.docx` | — | Earlier A2 C2C revision |
| `PCIe C2C.docx` | — | **A1/Iridium** baseline C2C spec (diff for the A1→A2 delta) |

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

### PHY Documents (`~/docs/PCIe EP/phy/`)

The SU uses the **identical PCIe6 PHY** as the PCIe EP. All PHY documents are shared:

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
> RTL lives on the Iridium *freezed rtl* branch, not on `main`. When researching RTL for a
> specific project, check out the matching branch: `main` for Atlantic, the project's
> freezed-RTL branch for Iridium (and any earlier project).

**Local-first strategy**: These skills are global (user-level). The workspace may or may not contain the source code.
1. Check if FW driver code exists locally — common workspace paths:
   - `fw/ip/se/` or `ip/se/`
   - Also check the project manifest (e.g., `.star` files or `octl` manifests) for the actual deployment path
2. If found locally — read directly from the workspace (fastest, no network)
3. If NOT found locally — use GitHub search tools to query the remote repos above

## DM Controller vs EP Controller — Key Differences

| Aspect | DM Controller (SU) | EP Controller (PCIe EP) |
|--------|-------------------|------------------------|
| Modes | RC + EP (dual-mode) | EP only |
| Databook | `DWC_pcie_ctl_dm_databook.pdf` (1064p) | `DWC_pcie_ctl_ep_databook.pdf` (952p) |
| Extra docs | `DWC_pcie_ctl_user.pdf`, relnotes | — |
| Register ref | Same format, DM variant registers | Same format, EP variant registers |
| Use case | SU ↔ SU links, can act as RC or EP | Host-facing endpoint |
| PHY | Identical PCIe6 PHY | Identical PCIe6 PHY |

## Knowledge Base (Expert Memory)

This skill keeps an **expert memory** of everything it has already researched and verified, so
repeat questions on the same subject are answered fast without re-reading the (very large) DM
controller docs, PHY docs, C2C spec, or RTL.

- **Location:** `~/.copilot/skills/su-ip/knowledge/`
- **Index:** `~/.copilot/skills/su-ip/knowledge/INDEX.md` — catalog of recorded topics
- Detail files: `knowledge/<topic-slug>.md` — verified findings with full citations

The knowledge base is **always consulted first** (Step 0) and **always updated after**
non-trivial research (final step). This is what makes the skill grow into a true Scale Up
expert over time. (PHY facts equally true for the EP can also be recorded in the `pcie-ep-ip`
knowledge base — the PHY is identical.)

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

1. Read `~/.copilot/skills/su-ip/knowledge/INDEX.md` and scan the topic table.
2. If the question overlaps a recorded topic, read that detail file. Also full-text search the
   `knowledge/` directory for the question's keywords:
   ```python
   import os, glob
   kb = os.path.expanduser("~/.copilot/skills/su-ip/knowledge")
   for f in glob.glob(os.path.join(kb, "*.md")):
       txt = open(f, encoding="utf-8").read()
       if "<keyword>" in txt.lower():
           print(f"== {os.path.basename(f)} ==")
   ```
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
| C2C protocol/spec | `PCIe C2C Rev 2.0.0.docx` | |
| PHY registers (APB) | `dwc_pcie6phy_g2_tsmc5ff_x4ns_reference.pdf` | |
| PHY architecture, PRBS, loopback | `dwc_pcie6phy_g2_tsmc5ff_x4ns_databook.pdf` | |
| PCS wrapper | `dwc_pcie6_phy_pcs_wrapper_databook.pdf` | |
| PHY firmware/calibration | `dwc_pcie6_phy_firmware_appnote.pdf` | |
| RTL implementation | GitHub repo | |

### Step 2: Search the Document

Use `pymupdf` (fitz) for PDF files, `python-docx` for .docx files.

#### PDF keyword search:
```python
import fitz
doc = fitz.open("<path>")
for page_num in range(len(doc)):
    page = doc[page_num]
    text = page.get_text()
    if "<keyword>" in text.lower():
        print(f"Page {page_num}: {text[:200]}")
```

#### PDF targeted read:
```python
import fitz
doc = fitz.open("<path>")
for page_num in range(START, END):
    page = doc[page_num]
    print(f"--- Page {page_num} ---")
    print(page.get_text())
```

#### DOCX search (for C2C spec):
```python
import docx
doc = docx.Document("/home/yoniizu/docs/C2C/PCIe C2C Rev 2.0.0.docx")
for i, para in enumerate(doc.paragraphs):
    if "<keyword>" in para.text.lower():
        print(f"Para {i}: {para.text[:300]}")
```

#### DOCX table search (C2C register definitions):
```python
import docx
doc = docx.Document("/home/yoniizu/docs/C2C/PCIe C2C Rev 2.0.0.docx")
for t_idx, table in enumerate(doc.tables):
    for r_idx, row in enumerate(table.rows):
        row_text = " | ".join(cell.text.strip() for cell in row.cells)
        if "<keyword>" in row_text.lower():
            print(f"Table {t_idx}, Row {r_idx}: {row_text}")
```

### Step 3: Extract and Answer

1. Search with broad keywords first, then narrow down
2. For register lookups: search the reference PDF by register name or offset
3. For DM-specific features (RC mode, dual-mode switching): check the DM databook first
4. For PHY questions: use the same PHY docs as PCIe EP (located under `~/docs/PCIe EP/phy/`)
5. Always cite the source document and page number in your answer
6. If the docs don't cover it, check RTL via GitHub search tools

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
- **C2C protocol**: Search the docx spec. Registers may be in tables.
- **PHY questions**: Always use `~/docs/PCIe EP/phy/` — the PHY is identical across EP and SU
- **The 18845-page reference**: Always search by keyword first, never scan sequentially

## Important Notes

- The DM controller reference is 18845 pages — ALWAYS use keyword search, never sequential scan
- PHY docs are shared with PCIe EP — located under `~/docs/PCIe EP/phy/`, NOT under `~/docs/C2C/`
- PHY registers are per-lane: offset = base + lane*0x400 + register_offset
- For questions that span both controller and PHY, cross-reference both databooks
- The DM controller has superset functionality vs EP controller (includes RC mode)
