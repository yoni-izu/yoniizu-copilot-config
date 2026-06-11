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

## Document Catalog

### DM Controller Documents (`~/docs/C2C/`)

| File | Pages | Content |
|------|-------|---------|
| `DWC_pcie_ctl_dm_databook.pdf` | 1064 | DM controller architecture, features, RC+EP mode programming, integration |
| `DWC_pcie_ctl_reference.pdf` | 18845 | Complete register reference — all config space and DBI registers (DM variant) |
| `DWC_pcie_ctl_user.pdf` | 356 | Integration guide, clocking, reset, bring-up sequences |
| `DWC_pcie_ctl_relnotes.pdf` | 38 | Release notes, known issues, errata |
| `PCIe C2C Rev 2.0.0.docx` | — | Internal Element C2C specification |

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

**Local-first strategy**: These skills are global (user-level). The workspace may or may not contain the source code.
1. Check if FW driver code exists locally — common workspace paths:
   - `fw/ip/se/` or `ip/se/`
   - Also check the project manifest (e.g., `.star` files or `elmcli` manifests) for the actual deployment path
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
