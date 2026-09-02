# A2 (Atlantic2) — Architect HW Initiatives (Confluence Q&A)

- **Recorded:** 2026-08-30
- **Status:** Verified from Confluence (page 1362952569 "A2 HW Initiatives - Qs")
- **A2-vs-A1 delta:** Defines A2's new HW/FW scope surface — dominated by **scale-up (C2C)
  ordering + aggregation + GPU/INC interop**, PCIe EP additions (PTM, PME_TO_Ack, SL-per-engine),
  and a large NoC/FIU address-map + bug-fix list.
- **Tags:** initiatives, architect, confluence, c2c, scale-up, se, pcie, noc, fiu, inc, gpu,
  pcie compliant, ordering, aggregation, multicast, fec, ptm, address map, tp8, tp16

## Scope / Question
The architect's A2 HW-initiatives Q&A table — the authoritative list of A2 initiatives with
open questions, per IP team (SE/C2C, PCIe, NoC, Infra). User's note: "most likely no actual FW
work" — but MANY entries have explicit FW implications (see below); a few are marked HW-internal.

## Findings (A2)

### Scale-Engine / C2C / Scale-Up (IPSESW) — the bulk
- **PCIe-compliant ordering is a first-class A2 theme:**
  - IPSESW-281 **Producer/Consumer Ordering (PCIe compliant)** (config, TP# support)
  - IPSESW-265 **Ordering flows (Non-PCIe compliant)** — i.e. A2 supports BOTH compliant and
    non-compliant ordering modes (this is the "SU PCIe Compliant mode" the user flagged).
  - IPSESW-282 Aggregation buffer structures in C2C Tx for P/C ordering.
- **Write aggregation buffers:** IPSESW-279 (Wr Aggregation + Flag transactions), IPSESW-278
  (Wr Aggregation + CCE Operations), IPSESW-277 **4KB Crossing Handling**.
- **AG/UC (AllGather / Unicast?) ops:** IPSESW-276 (CCE mode), IPSESW-275 (INC mode),
  IPSESW-263 **Terminate AG/UC when MPS < 256B** — *FW reads the MPS register and configures the
  controller*.
- **Inter-tray / inter-sled comm:** IPSESW-280 (A2<->A2/GPU, BMC config per setup), IPSESW-274
  (A2<->**INC**), IPSESW-273 (A2 BAR space for inter-sled comm).
- **CCE/INC handling:** IPSESW-272/271 (CCE RS/AR in CCE mode ± explicit completion), IPSESW-270
  (CCE RS/AR in INC mode, cross-address-space), IPSESW-264 (EOC — End-of-Config — flows between
  INC/A2/Host), IPSESW-268 (CCE opcode & CCE/INC mode identification at C2C).
- **Multicast:** IPSESW-269 (multicast support), IPSESW-267 (update multicast from remote device
  = **GPU**).
- IPSESW-266 **Flag aggregation** — "supposed to be fully HW-internal without FW intervention".
- IPSESW-215 **Error containment — Isolation Mode & Drop Mode**.
- IPSESW-207 Address Manipulation (**DPA→NPA conversion**) [Done]; IPSESW-203 Collective op
  support [Done].
- IPSESW-146 **Add external FEC support** — 4 standard + 3 configurable custom modes; "can be
  also towards host" (ties to the overview's "Enhanced custom FEC"). [Triage]

### PCIe EP (IPPCIESW)
- IPPCIESW-552 Dedicated **SL (Security Level) config per engine/function** (fixed config from Alex).
- IPPCIESW-551 **INC BAR mapping**; IPPCIESW-464 **LMEM Address Virtualization** (static config).
- IPPCIESW-387 **Application control over PME_TO_Ack** — low-power states on IRQ; FW handles state
  moves + waits for completion.
- IPPCIESW-386 **Add PTM** (Precision Time Measurement) — needs SW work.

### NoC / FIU (IPNOCSW)
- IPNOCSW-138 **TP8 / TP16 mode support**; IPNOCSW-137 **Device Config addr space → 512 MB**;
  IPNOCSW-136 add **INC + Remote Device** address spaces to map; IPNOCSW-135 Device MEM addr-map
  decoding updates; IPNOCSW-119 region selection add **DieID** comparison (besides X/Y).
- IPNOCSW-134 FIU SLV watchpoint; IPNOCSW-128 FIU termination for CTRL fabric; IPNOCSW-118 Ack
  for reset notification; IPNOCSW-120 constant termination delay via REF_CLK; IPNOCSW-121 FIU
  address manip 16KB → **4KB granularity**; IPNOCSW-116/117 RL (regulator) count + dedicated dest.
- Many **bug fixes**: RR addr space 25:0→27:0, CfgR must cover SPI-flash + SW-trace(STM), ECT
  isolation missing, DBG-fabric termination on ISO, CfgR/RR separate enable bits, latency
  regulator per-range, transaction logger missing.

### Infra (IPINFRASW)
- IPINFRASW-1498 PLL: increase bitmap masking 64b → 256b.

## A2 vs A1 (Iridium) delta
- **New scale-up capabilities**: PCIe-compliant ordering mode, GPU interop (A2<->GPU),
  **INC** (in-network-computing switch/device) integration, multicast, external FEC modes.
- **PCIe EP additions**: PTM, PME_TO_Ack app control, per-engine SL config, INC BAR mapping.
- **NoC/address-map growth**: TP8/TP16, 512 MB device-config space, INC + remote-device address
  spaces, DieID-based region selection, finer (4KB) FIU address manipulation.
- These are mostly **new initiatives layered on the Iridium NoC/PCIe/C2C baseline**; several NoC
  entries are explicitly "bug fix" of Iridium behavior carried into A2.

## Sources
- Confluence page **1362952569** "A2 HW Initiatives - Qs"
  (space ~712020589feaff768144a78fb51f9ac2542856). Table of JIRA initiatives (IPSESW-2xx,
  IPPCIESW-3xx/4xx/5xx, IPNOCSW-11x-13x, IPINFRASW-1498) with per-item comments/owners.
