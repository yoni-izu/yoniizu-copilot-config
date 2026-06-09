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

## Procedure

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

## PHY Reset Signal Chain (Verified from RTL)

Two distinct PHY reset signals, both generated by Synopsys `DWC_pcie_clkrst.v` inside `pep_wrap.v`:

### Inputs to controller reset block (from PSOC via `pcie_ep_cls.v`):
- `perst_n` ← `async_psoc2pep_perst_n` — PERST from PSOC reset manager (or external GPIO pad)
- `power_up_rst_n` / `button_rst_n` ← `async_pep_rst_n` — cold/power-on reset from PSOC

### Outputs from controller to PPW:
| Signal | Asserted on | Drives in PPW |
|--------|------------|---------------|
| `phy_perst_n` | Cold reset, Warm reset, **PERST** | `phy_reset` (= `~phy_perst_n`), APB `presetn`, JTAG `trstn`, SRAM `rst_n` |
| `phy_rst_n` | Cold reset, Warm reset, **PERST**, **Hot reset** | Per-lane `pipe_laneX_reset_n` |

### Key facts:
- **PERST asserts both `phy_perst_n` and `phy_rst_n`** — full PHY reset including FW SRAM wipe
- **Hot reset only asserts `phy_rst_n`** — PIPE lane reset only, no PHY core/FW/SRAM impact
- FLR, platform, WD, hard, soft, debug resets do NOT reach the PHY at all
- After PERST, PIW `fw_sram_copy_handler` automatically re-copies FW from PIW SRAM → PHY SRAM (FSM in `piw_macro/rtl/fw_sram_copy_handler.v`)

### PSOC reset manager mapping (from `ip.psoc.rtl`):
- PSOC has 9 reset types → 129 unit reset outputs
- PCIe units = `async_pcie_rst_n[1:0]` (unit indices 16-17)
- `async_prstn` (PERST output) can be sourced from either unit_rst[16] or external GPIO pad (CSR selectable)
- Only **POR** and **warm reset** types assert PCIe unit resets by default
- Hot/FLR flow goes FROM PCIe TO PSOC (`async_pcie2psoc_hot_rst_n`, `async_pcie2psoc_flr_rst_n`), not the other way

## Important Notes

- The controller reference PDF is 18845 pages — ALWAYS use keyword search, never sequential scan
- PHY registers are per-lane: offset = base + lane*0x400 + register_offset
- The `.xlsx` config reference manual can be read with `openpyxl` if needed
- For questions combining controller + PHY behavior, cross-reference both databooks
