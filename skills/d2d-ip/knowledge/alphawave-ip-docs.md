# AlphaWave UCIe D2D IP — Vendor Doc Set (AresCORE PHY / GammaCORE Controller / GammaCORE32 Subsystem)

- **Recorded:** 2026-08-23
- **Status:** Verified from vendor docs (front matter + TOC)
- **Tags:** alphawave, alphawave semi, arescore, gammacore, gammacore32, eland, eland_s, ucie phy, d2d phy, d2d controller, subsystem, mmr, dvsec, cmn mcu, 1xs32, vendor ip, el aw d2d, uisrb, sideband mailbox

## Scope / Question
What are the AlphaWave IP docs added to `~/docs/D2D/`, what does each cover, and how do they
map to the Element D2D/UCIe stack and FW (EL AW D2D driver family, boot flow).

## Findings

AlphaWave (Alphawave Semi) is the vendor for Element's licensed D2D/UCIe silicon IP. The stack
is three layers; docs are named after the product + `Eland`/`Eland_S` config and an `N64B1Tn5`
(N5 / 5nm) process/config tag:

1. **AresCORE™ — UCIe D2D PHY IP** (the physical layer / SerDes)
   - File: `arco_arescore_ucie_datasheet_n5_base_0_8_7_element.pdf` (108 pp, ~6.1MB)
   - "AresCORE™ UCIe™ Die-to-Die (D2D) PHY IP — 5nm Base Datasheet", v**0.1.0_0.8.7**, Sep 2024,
     "CONFIDENTIAL for Element Labs under NDA".
   - Covers: overview, features, supported standards, IP/TX/RX/common-lane configurability,
     electricals. This is the **PHY** — its firmware is the **"UCIe PHY FW image"** the D2D
     driver loads/authenticates into PHY memories during PRIMEBOOT (cold reset only).

2. **GammaCORE™ — UCIe D2D Controller IP** (protocol/link layer)
   - File: `Eland_gammaN64B1Tn5_ss_a_controller_100_051.pdf` (95 pp, ~2.9MB)
   - "GammaCORE™ UCIe™ Die-to-Die Controller IP — Base Datasheet (Eland_S Variant)",
     v**1.0.0_0.5.1**, Oct 17 2025.
   - Covers: streaming protocol (packing/multiplexing, TX/RX buffers, flit generation/
     termination, end-to-end credit-based flow control), AXI4, FDI.

3. **GammaCORE32™ — 1×S32 Subsystem for UCIe** (packaged controller + PHY, 32 lanes)
   - Datasheet: `Eland_gammaN64B1Tn5_ss_a_datasheet_100_053.pdf` (33 pp, ~1.2MB)
     — "GammaCORE32™ 1XS32 Subsystem for UCIe — 5nm Datasheet (Eland_S Variant)",
     v**1.0.0_0.5.3**, Oct 17 2025. Covers subsystem interfaces: APB, FDI, UCIe standard
     package, iJTAG, misc signals.
   - Register descriptions (MMR): `Eland_gammaN64B1Tn5_ss_a_mmr_100_053_Registers_Descriptons.pdf`
     (~3.1MB) + `.txt` (~3.5MB searchable extraction). "GammaCORE32™ 1xS32 Subsystem for UCIe —
     Register Descriptions (Eland Variant)", v**1.0.0_0.5.3**, Oct 17 2025.
   - MMR register blocks (from TOC): **DVSEC1_REG** (PCIe Express Extended Cap header, Designated
     Vendor-Specific headers, UCIe Link DVSEC — Capability/Control/Status, Link Event & Error
     Notification Control, Register Locators 0–3 low/high, Sideband Mailbox Index/Data/Control&Status,
     Requester ID, Associated Port Numbers), **DVSEC2_REG** (UCIe Switch Register Block "UiSRB" base
     address registers), **GLOBAL_REG** (CMN MCU Control, Hart ID, Boot Address, Device Address,
     Data-mem, IRQ Control-1/-2, …).

### Authority / search guidance
- For **register offsets/bitfields** → search the MMR `.txt` (page-delimited), cite the PDF page.
- For **PHY electricals/SerDes/lane config** → AresCORE datasheet.
- For **controller streaming/flit/credit/FDI/AXI** → GammaCORE controller datasheet.
- For **subsystem interfaces (APB/FDI/UCIe pkg/iJTAG)** → GammaCORE32 subsystem datasheet.
- For **Element integration** (wrapper, manageability bridge, multi-die topology, boot-flow
  responsibilities) → internal `D2D Specification Rev 2.0.0.docx` remains authoritative.
- These vendor docs map to the FW **EL AW D2D** driver family (AW = AlphaWave).

## Sources
- `~/docs/D2D/arco_arescore_ucie_datasheet_n5_base_0_8_7_element.pdf` — p1 (title), p3 (TOC)
- `~/docs/D2D/Eland_gammaN64B1Tn5_ss_a_controller_100_051.pdf` — p1 (title), p3 (TOC)
- `~/docs/D2D/Eland_gammaN64B1Tn5_ss_a_datasheet_100_053.pdf` — p1 (title), p3 (TOC)
- `~/docs/D2D/Eland_gammaN64B1Tn5_ss_a_mmr_100_053_Registers_Descriptons.txt` (+ `.pdf`) — p1–2 (title + TOC)
