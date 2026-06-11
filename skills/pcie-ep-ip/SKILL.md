---
name: pcie-ep-ip
description: 'Answer PCIe EP IP design, usage, register, PHY, controller, and PIW questions using Synopsys IP documentation and RTL. Use when: PCIe endpoint, DWC PCIe EP controller, PCIe6 PHY, SerDes, PIPE, lane margining, LTSSM, equalization, PRBS, loopback, RAS DES, config space, TLP, DLLP, Flit mode, L0p, error injection, event counters, link training, PCIe EP registers, MSIX gateway, MSI-X interrupt coalescing, PIW.'
argument-hint: 'Ask a question about PCIe EP IP design, registers, PHY, or controller behavior'
---

# PCIe EP IP Documentation Q&A

## When to Use

- Questions about DWC PCIe EP controller behavior, registers, or configuration
- Questions about PCIe6 PHY (SerDes) operation, tuning, PRBS, loopback
- PCS wrapper functionality
- Register lookups (controller config space or PHY APB registers)
- LTSSM behavior, link training, equalization
- RAS DES features, error injection, event counters
- Lane margining, BER testing
- Flit mode, L0p, power management
- PCIe EP internal specification questions
- PIW (PCIe IP Wrapper) questions, MSIX gateway micro-architecture
- MSI-X interrupt generation, coalescing, doorbell mechanism
- RTL implementation questions

## Architecture Context

- **Controller**: Synopsys DWC PCIe EP controller v6.42a-lca01. PCIe 6.0 compliant. Flit mode capable.
- **PHY**: Synopsys DWC PCIe6 PHY v2.63a. SerDes PIPE architecture.
- **PCS Wrapper**: Thin layer — CDC + reorder only. MAC does encode/decode/scramble.
- **PIPE Interface**: PHY ↔ Controller communication. In SerDes arch, raw data crosses PIPE (no 8b/10b or 128b/130b at PIPE boundary).

## Boot Flow Responsibilities

Reference: [Boot flow](https://elementlabs.atlassian.net/wiki/spaces/SWENG/pages/598671382/Boot+flow) (SWENG space).

FW boots through staged, chained-authenticated images (root of trust starts at FLA.ROM). The MuC-resident runtime stages are **FLA.RT → PRIMEBOOT → MANAGEMENT**; **POWER & THERMAL** runs on the PTuC. Drivers are split into **INIT** (one-time bring-up) and **RUNTIME** (interrupt/operational) components.

PCIe EP responsibilities by stage (per the proposed driver-functionality split):

| Driver / Module | FLA.RT | PRIMEBOOT | MANAGEMENT | POWER & THERMAL |
|-----------------|--------|-----------|------------|-----------------|
| EL PCIE EP      | INIT   | RUNTIME   | RUNTIME    | —               |
| EL PIW          | INIT   | —         | RUNTIME    | —               |

- **FLA.RT** (runs on the MuC, executes from SPSRAM) is **primarily responsible for PCIe bring-up**. It loads/authenticates the **PCIe PHY FW ICCM and DCCM** images and copies them into the PHY memories (cold reset only), and runs the PCIe EP / PIW **INIT** path so the host PCIe link can be established — required before the host can stream any further images (the 100 ms PCIe-link target favors early bring-up).
- **PRIMEBOOT** and **MANAGEMENT** run the PCIe EP **RUNTIME** path; PIW **RUNTIME** lives in MANAGEMENT.
- Caveat from the page: PCIe init is **currently part of PRIMEBOOT**; depending on timing requirements it may be moved to FLA.RT. Treat the table as the current plan, not a frozen contract.

> The Init-vs-Runtime split is still evolving: after tape-out (TO) some HW init may shift between stages.

## Document Catalog

### Top-Level Documents (`~/docs/PCIe EP/`)

| File | Content |
|------|---------|
| `PCIe EP Specification Rev 2.0.0.docx` | Internal Element EP specification (overall EP design) |

### Controller Documents (`~/docs/PCIe EP/controller/`)

| File | Pages | Content |
|------|-------|---------|
| `DWC_pcie_ctl_ep_databook.pdf` | 952 | EP controller architecture, features, programming model, integration |
| `DWC_pcie_ctl_reference.pdf` | 18845 | Complete register reference — all config space and DBI registers |

### PHY Documents (`~/docs/PCIe EP/phy/`)

| File | Pages | Content |
|------|-------|---------|
| `dwc_pcie6phy_g2_tsmc5ff_x4ns_databook.pdf` | 200 | PHY architecture, features, PRBS/loopback, lane margining, signal integrity |
| `dwc_pcie6phy_g2_tsmc5ff_x4ns_reference.pdf` | 1991 | Complete PHY register reference (APB registers) |
| `dwc_pcie6_phy_pcs_wrapper_databook.pdf` | 206 | PCS wrapper architecture and registers |
| `dwc_PCIe6_SDK_appnote.pdf` | 128 | SDK usage, test patterns, validation flows |
| `dwc_pcie6_phy_firmware_appnote.pdf` | 60 | PHY firmware loading, calibration, training |
| `dwc_pcie6phy_corekit_user.pdf` | 50 | Integration, clocking, reset sequences |
| `dwc_pcie6_phy_ate_testbench_appnote.pdf` | 40 | ATE testing, production test patterns |
| `dwc_pcie6_phy_fusion_compiler_appnote.pdf` | 32 | Synthesis/P&R guidance |
| `dwc_pcie6_phy_gate_level_simulations_appnote.pdf` | 28 | Gate-level sim setup |
| `dwc_SerDes_pcb_packaging_design_guide.pdf` | 112 | PCB layout, SerDes routing, signal integrity |
| `dwc_serdes_phy_occ_distribution_routing_guidelines_appnote.pdf` | 10 | OCC clock distribution |
| `dwc_pcie6phy_g2_tsmc5ff_x4ns_config_reference_manual.xlsx` | — | PHY configuration spreadsheet |
| `readme_2.63a_cust4_pre1.txt` | — | Release notes for PHY v2.63a |

### PIW Documents (`~/docs/PCIe EP/PIW/`)

| File | Content |
|------|---------|
| `MSIX_GW_uArch.docx` | MSI-X Gateway micro-architecture spec (v1.0.0). Covers interrupt generation via MSIX_DOORBELL_OFF, validation (enable check, max vector index, security/function spoofing prevention), coalescing (per PF/VF timing-slot dedup: first sets INTR, second sets PEND, others dropped), AXI 16B interface from CTRL fabric, error handling for illegal accesses. |

## Source Code Repositories

| Repo | GitHub URL |
|------|------------|
| RTL | https://github.com/Elmnt-Internal/ip.pcie.rtl |
| Firmware | https://github.com/Elmnt-Internal/ip.pcie.sw.fw.ep |

**Local-first strategy**: These skills are global (user-level). The workspace may or may not contain the source code.
1. Check if FW driver code exists locally — common workspace paths:
   - `fw/ip/pcie/` or `ip/pcie/`
   - Also check the project manifest (e.g., `.star` files or `elmcli` manifests) for the actual deployment path
2. If found locally — read directly from the workspace (fastest, no network)
3. If NOT found locally — use GitHub search tools to query the remote repos above

## Knowledge Base (Expert Memory)

This skill keeps an **expert memory** of everything it has already researched and verified, so
repeat questions on the same subject are answered fast without re-reading the (very large)
controller/PHY docs, EP spec, PIW docs, or RTL.

- **Location:** `~/.copilot/skills/pcie-ep-ip/knowledge/`
- **Index:** `~/.copilot/skills/pcie-ep-ip/knowledge/INDEX.md` — catalog of recorded topics
- Detail files: `knowledge/<topic-slug>.md` — verified findings with full citations
  (seeded with `ds-term.md` and `phy-reset.md`, both verified from RTL)

The knowledge base is **always consulted first** (Step 0) and **always updated after**
non-trivial research (final step). This is what makes the skill grow into a true PCIe EP expert
over time.

## Procedure

### Step 0: Consult the Knowledge Base (do this first)

1. Read `~/.copilot/skills/pcie-ep-ip/knowledge/INDEX.md` and scan the topic table.
2. If the question overlaps a recorded topic, read that detail file. Also full-text search the
   `knowledge/` directory for the question's keywords:
   ```python
   import os, glob
   kb = os.path.expanduser("~/.copilot/skills/pcie-ep-ip/knowledge")
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
| Controller registers | `DWC_pcie_ctl_reference.pdf` | `DWC_pcie_ctl_ep_databook.pdf` |
| Controller architecture/features | `DWC_pcie_ctl_ep_databook.pdf` | |
| PHY registers (APB) | `dwc_pcie6phy_g2_tsmc5ff_x4ns_reference.pdf` | |
| PHY architecture, PRBS, loopback | `dwc_pcie6phy_g2_tsmc5ff_x4ns_databook.pdf` | |
| PCS wrapper | `dwc_pcie6_phy_pcs_wrapper_databook.pdf` | |
| PHY firmware/calibration | `dwc_pcie6_phy_firmware_appnote.pdf` | |
| Internal EP spec | `PCIe EP Specification Rev 2.0.0.docx` (top-level) | |
| MSIX gateway / interrupts | `PIW/MSIX_GW_uArch.docx` | `PCIe EP Specification Rev 2.0.0.docx` |
| PCB/signal integrity | `dwc_SerDes_pcb_packaging_design_guide.pdf` | |
| RTL implementation | GitHub repo | |

### Step 2: Search the Document

Use `pymupdf` (fitz) for PDF files, `python-docx` for .docx files.

#### PDF keyword search — scan for relevant pages:
```python
import fitz
doc = fitz.open("<path>")
for page_num in range(len(doc)):
    page = doc[page_num]
    text = page.get_text()
    if "<keyword>" in text.lower():
        print(f"Page {page_num}: {text[:200]}")
```

#### PDF targeted read — extract full page content:
```python
import fitz
doc = fitz.open("<path>")
for page_num in range(START, END):
    page = doc[page_num]
    print(f"--- Page {page_num} ---")
    print(page.get_text())
```

#### DOCX search:
```python
import docx
doc = docx.Document("<path>")
for i, para in enumerate(doc.paragraphs):
    if "<keyword>" in para.text.lower():
        print(f"Para {i}: {para.text[:200]}")
```

### Step 3: Extract and Answer

1. Search with broad keywords first, then narrow down
2. For register lookups: search the reference PDF by register name or offset
3. For architectural questions: start with the databook, then cross-reference the register manual
4. Always cite the source document and page number in your answer
5. If the docs don't cover it, check RTL via GitHub search tools

### Step 4: Record Verified Findings (do this last)

After answering a non-trivial question, persist what you verified so the skill remembers it:

1. Create or update `~/.copilot/skills/pcie-ep-ip/knowledge/<topic-slug>.md` using the template
   in `INDEX.md`. Keep it concise, durable, and reusable.
2. Add or refresh the topic's row in `INDEX.md` (newest at top) with tags, status, and date.
3. Always include citations (doc + page, RTL repo + file + symbol) so the finding can be
   re-verified and trusted.
4. If new research contradicts a prior entry, **correct** that entry rather than leaving stale
   information.

Skip recording only for trivial lookups already fully covered by an existing entry.

### Search Strategy Tips

- **Register by name**: Search reference PDF for the exact register name (e.g., `PORT_LINK_CTRL_OFF`, `TST_CFG_0`)
- **Register by offset**: Search for hex offset (e.g., `0x710`, `0x2038`)
- **Feature by keyword**: Search databook for feature name (e.g., `lane margining`, `EINJ`, `RAS DES`)
- **PHY PRBS/loopback**: Databook pages ~100-130 cover test modes. Key registers: `TST_CFG_0` (offset per-lane: `0x2000+j*0x400+0x038`)
- **For the 18845-page reference**: Always search by keyword first, never scan sequentially. Search for register name, field name, or capability name.

## Key Register Quick Reference

| Register | Offset | Doc | Purpose |
|----------|--------|-----|---------|
| `PORT_LINK_CTRL_OFF` | DBI base + 0x710 | Controller ref | Link control, loopback enable (bit 2) |
| `PRBS_LOOPBACK_TEST_REG_OFF` | DBI base + 0x5ac | Controller ref | Controller PRBS test |
| `PORT_FORCE_OFF` | DBI base + 0x708 | Controller ref | Force LTSSM state |
| `TST_CFG_0` | 0x2000+j*0x400+0x038 | PHY ref | PHY PRBS pattern generator (patgen_en bit 0, pattern_sel bits 6:2) |
| `GEN_CTRL` | varies | PHY ref | General PHY control |
| `PEP_CTRL_AUX_DS_TERM_CTRL` | PEP_AUX + 0x34 (PF), VF mirrors at 0x10..0x80 | Element EP | Downstream termination control. Reset=0x55. Bits: PF_UNCERR_MSTR_TERM_MASK(0x1), PF_SW_MSTR_TERM(0x2), PF_UNCERR_ELBI_TERM_MASK(0x4), PF_SW_ELBI_TERM(0x8). |

## Deep-Dive Findings (in the Knowledge Base)

Detailed, RTL-verified deep dives have been recorded in the knowledge base — read these files
before re-researching the same topics:

- **Downstream Termination (DS_TERM) datapath** — `knowledge/ds-term.md`
  (what each `PEP_CTRL_AUX_DS_TERM_CTRL` bit gates, SW vs RASDP triggers, ELBI one-way trapdoor,
  SEI notification, and the unrelated PIW AXI filters)
- **PHY reset signal chain** — `knowledge/phy-reset.md`
  (`phy_perst_n` vs `phy_rst_n`, PERST vs Hot reset, FW SRAM re-copy, PSOC reset-manager mapping)

## Important Notes

- The controller reference PDF is 18845 pages — ALWAYS use keyword search, never sequential scan
- PHY registers are per-lane: offset = base + lane*0x400 + register_offset
- The `.xlsx` config reference manual can be read with `openpyxl` if needed
- For questions combining controller + PHY behavior, cross-reference both databooks
