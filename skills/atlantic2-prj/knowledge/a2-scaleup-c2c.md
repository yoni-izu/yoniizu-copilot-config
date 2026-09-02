# A2 (Atlantic2) — Scale-Up / C2C Sub-System (PCIe-Compliant + Dual-Mode)

- **Recorded:** 2026-08-30
- **Status:** Verified from spec (Overview + PCIe C2C Rev 2.1.1 vs A1 PCIe C2C.docx)
- **A2-vs-A1 delta:** **2nd-gen** C2C; adds a **PCIe-Compliant Mode** on a **Dual-Mode (RP/EP)
  PCIe controller** to interoperate with **third-party PCIe devices (GPUs)**; up to **Gen7**.
- **RTL branch/tag:** RTL diff TODO.
- **Tags:** su, scale-up, c2c, chip-to-chip, pcie compliant, dual-mode, dm controller, rp, ep,
  gen7, flit, dragonfly, gpu, inc, ordering, aggregation, tp8, tp16, failover

## Scope / Question
What changes for scale-up/C2C in A2 vs A1 — especially the "PCIe Compliant mode" the user flagged.

## Findings (A2)
- **A2 scale-up is based on PCIe Gen7**: 16 × 2 lanes @ 128 Gbps per die. Topologies: **TP8**
  (7×4 intra-tray P2P + 1×4 outer-tray via PCIe scale-up switch) and **TP16** (15×2 + 1×2)
  (A2 Overview). Also PCIe **Rail** and **Dragonfly** connectivity topologies (C2C spec §4).
- **This is the 2nd generation C2C** scale-up interconnect (C2C spec §4 "Generation Comparison",
  Table 4-1). The A2 C2C spec (`Rev 2.1.1`, 1844 paras) is ~2.5× the A1 spec (`PCIe C2C.docx`,
  747 paras).
- **Layered PCIe architecture** (4 layers): Application (AXI memory semantics) → Transaction
  (cfg/mem/msg/error) → Data Link (flow control, ack, replay, integrity) → Physical (electrical,
  ordered sets, LTSSM). **C2C bridge maps AXI (AR/AW/W/R/B) → PCIe TLPs** with PCIe-compliant
  transaction semantics.
- **PCIe-Compliant Mode (the headline A2 feature)** — "a PCIe-compliant variant of the A2 C2C
  interconnect to enable **interoperability with third-party PCIe devices**" (§8, para 235):
  - Implemented via a **Dual-Mode (DM) PCIe controller**, **statically configurable as Root Port
    (RP) or Endpoint (EP)** per port.
  - Supports both deployment models: **Switched topology** (controller = EP behind a PCIe switch)
    and **Switchless / P2P topology** (one device = RP, peer = EP).
  - Each PCIe interface up to **Gen7 (128 GT/s)**; supports **FLIT (FM) + Non-FLIT (NFM)** modes;
    complies with **PCI Express Base Spec Rev 7.0**; low latency up to **100 ns/dir**.
  - Internal fabric carries **unordered** traffic (does not enforce PCIe ordering internally);
    ordering enforced only at the **C2C boundary** where PCIe-visible correctness / AXI semantics
    require it: **Non-Posted Request ordering (flush)** + **Posted-Write ordering (W→R dep)**.
  - **Ordering models** (§7.1.7): PCIe-compliant vs non-compliant; **Aggregation Buffer**
    (§7.1.4); **GPU Producer/Consumer support** (§7.1.9).
- **RAS**: PCIe ECRC (end-to-end), LCRC (link), AER, **SECDED ECC** on internal memories, NoC
  ctrl/data checksum validation before propagating over C2C, **poison propagation**, **viral**
  handling, **automatic lane failover** (dynamic width reduction), **multiple concurrent links**
  for rack-scale resiliency.
- **Power/area**: PHY 4-5 pJ/bit; Gen7 ×16 ≈ 20 W; scalable via **L0p** without traffic
  interruption; links train at max width, modulate width by demand in FLIT mode.

## A2 vs A1 (Iridium) delta
- **New**: PCIe-Compliant Mode + Dual-Mode RP/EP controller → GPU / third-party-PCIe interop;
  Gen7 (128 GT/s); FLIT+NFM; Dragonfly topology; explicit ordering models (compliant vs
  non-compliant), aggregation buffers, GPU producer/consumer, multi-link resiliency + lane
  failover. A1 C2C was EP-oriented, single generation.
- Ties to architect SE/C2C initiatives (a2-architect-initiatives.md): IPSESW-281 (PCIe-compliant
  P/C ordering), IPSESW-265 (non-compliant ordering), IPSESW-278/279 (Wr aggregation), IPSESW-263
  (terminate AG/UC when MPS<256B), IPSESW-269/267 (multicast / GPU), IPSESW-146 (external FEC).

## Sources
- Doc: `~/docs/A2/Atlantic2 Overview.docx` (Scale-Up 213-226; feature set 132-139).
- Doc: `~/docs/C2C/PCIe C2C Rev 2.1.1.docx` (A2) — §4 topology/generation (177-210), §5 layers
  (185-205), §8 PCIe-Compliant Mode (235-245), RAS (247-258), power (261-267). A1 baseline:
  `~/docs/C2C/PCIe C2C.docx` (747 paras).
- Controller: Synopsys DWC PCIe **DM** (dual-mode) controller — see `su-ip` skill.
- **RTL (confirmed)**: `ip.se.rtl` `iridium...main` — DM Gen7 macro `element_labs_dm16sx2_gen7_axi4_subsys`
  (DWC 6.60a), **EFEC integ**, **C2C read/write aggregators**, LTSSM logger, DMP **n3/3nm PCIe7
  PHY** + bifurcation. See a2-rtl-analysis.md.
