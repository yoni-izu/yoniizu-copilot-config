# Scale-Up Tray / Rack Interconnect Topology (Iridium) + Switch Bottleneck Behavior

- **Recorded:** 2026-07-06
- **Status:** Verified from doc (Iridium NoC Specification.docx)
- **Tags:** tray, rack, scale-up, c2c, topology, point-to-point, p2p, all-to-all, switch, rail, oversubscription, bottleneck, backpressure, flow control, redundancy, npa, gen6 x4

## Scope / Question
How are chips/dies interconnected on a tray vs across a rack for scale-up (C2C)? Where is
there a real switch, and can that switch drop/delay traffic and become a bottleneck (TX ≫ RX)?
(Triggered by a discussion: "Chip0 <-> pcie switch <-> Chip50 — can TX be much bigger than RX?
if I fold the switch, all die0 in a rack, can the switch drop/delay and be the bottleneck?")

## Findings

### Two distinct scale-up regimes — only ONE has a switch
1. **Within a tray → direct point-to-point C2C, NO switch.**
   - Each die has **7 scale-up C2C links**, each **PCIe Gen6 x4 = 32 GB/s per direction**,
     wired directly to the **same-index die** in the other 7 chips of the tray → an
     **all-to-all P2P mesh** among the 8 chips (for a given die index).
   - Total **28 scale-up ports per device** (7 ports × 4 dies).
   - Intra-tray routing = P2P scale-up port directly to the destination device; then D2D
     inside that device to reach the destination die. No shared switch, no central funnel.
   - 8 chips/tray is PoR; a 4-chips/tray variant maps two x4 ports per device instead.

2. **Between trays (rack) → real PCIe scale-up switch (switch trays).**
   - **All dies of the same index connect to the same scale-up switch** via Gen6 x4
     (this is exactly the "fold all die-0 into a switch" mental model — it's the rail topology).
   - A switch tray interconnects up to **64 devices (16×4), x4 lanes/device**. Embedded CPU
     assigns **NPA (Network Physical Address)** routing regions (NPA ≠ Device Address).
   - Cross-tray = **two hops**: (1) through the scale-up switch to the destination tray, then
     (2) P2P scale-up within that tray to the destination device.
   - Rail topology scales beyond 64 devices: 2-rail=128, 4-rail=256, single 80×4 switch rail=512.
     Address bits select entry index + rail.

### Port C2C[7] → switch wiring per rail count (added 2026-11-03)
- Each die has 8 C2C ports: **C2C[0..6]** = intra-tray P2P to the same die index of the 7 other
  chips; **C2C[7]** = the single switch-facing port (NoC §4.1, paras 227–235).
- A **switch is keyed by (rail, die index)**. A rail is a chip-group; it owns **4 switches, one per
  die plane** (the 3+1 redundancy set). Explicit 2-rail PoR wiring (paras 242–249):
  `SwitchTray[d].Switch[r] ↔ Tray[15..0].Chip[rail r chips].Die[d]` → 4 switch trays × 2 = 8 switches.
- **8-rail** (para 531: "each switch interconnects a single rail", rail = addr[40:38] = chip index)
  is the same rule with one chip per rail (derived; the spec doesn't enumerate the wiring):
  `Switch(rail c, plane d) ↔ Tray[*].Chip[c].Die[d].C2C[7]`.
  - Per tray, the 32 port-7s go to **32 different switches** (8 rails × 4 die planes), so each
    switch sees **exactly one port per tray**. Fan-in per switch = number of trays
    (16 for a 16-tray rack, consistent with A1 `main` `num-switch-peers=16`). Up to 64 trays on a
    64-port x4 switch gives 512 devices.
  - Switch count = 32 (vs 8 for 2-rail, 16 for 4-rail). The 4 plane switches of rail c back each
    other up (3+1 hash redistribution, paras 405–419).
  - The NoC spec 8-rail nLUT index uses [43:41]. A1 FW `same_index` instead folds [40:38] and indexes
    with [46:41] (see `c2c-same-index-topology.md`).
- **A2 terminology:** A2 Scale-up doc calls **each switch a rail**. The full-scale baseline gives
  one rail per (Chip, Die), with up to 64 rails (16 chips × 4). The same physical arrangement is
  "32 rails" in A2 language. Don't confuse this with A1 "8 rails".

### Switch bottleneck / drop behavior (answers the TX≫RX question)
- **Bottleneck / delay — YES on the inter-tray switch path.** The switch is a many-to-one
  aggregation point; a destination's ingress is a single x4 (32 GB/s). When egress saturates,
  the switch fills buffers and **backpressures via credit-based flow control → senders stall**.
  Lossless: this is *delay*, not loss. Offered TX ≫ delivered RX in aggregate, but delivered
  TX == RX (flow control throttles injection).
- **Drop — only on errors or deliberate containment, NOT plain congestion.** PCIe/C2C has no
  congestion drop. Real drops: TLP errors (UR, ECRC, poison, malformed) or **C2C Drop/Isolation
  Mode** (viral / link-down / WD timeout — see `drop-mode-error-containment.md`; single link
  error → BMC resets the entire tray).
- **Intra-tray has no switch** → no central bottleneck; only pairwise contention if a third
  chip targets the same peer.

### Redundancy (affects available BW → bottleneck severity)
- Rack scale-up uses a **3+1 redundant switch** scheme (per rail; spec also states 8 switches /
  2 rails at rack level). Fully functional = all 4 switches up; degraded = 3 of 4.
- On a defective/binned switch, HW **redistributes that die's traffic across the 3 remaining
  scale-up ports** and reroutes to a redundant C2C port (re-maps Device Address → NPA). Net:
  a failed switch *reduces* scale-up BW and worsens the bottleneck.
- Also **redundant compute tray** ((15+1)/rack): FW reprograms Device-Address→NPA mapping so the
  app address is preserved but routed to the redundant tray.

### Bandwidth reference
- 1 C2C link = PCIe Gen6 **x4 = 32 GB/s/direction**.
- Intra-tray: x16 aggregate per A1 within the tray; 7 x16 total (7 x4 links per die × 4 dies).
- Collective ops layered: 4 dies in a chip → 8 chips in a tray → 16 trays (CCE.L1/L2/L3).

## Sources
- Doc: `Iridium NoC Specification.docx` — §4.1.1 Chips/Tray (p.17), §4.1.2 Rack Scale-up (p.18),
  §7 Scale-Up Connectivity (p.43), §7.2 Switch Redundancy (p.49), §5.2 Redundancy (p.38).
  Key paras: 267 (two-hop), 288 (Gen6 x4 = 32GB/s), 385 (7 ports/die, 28/device), 405–416
  (3+1 rail, redistribution), 521–531/556 (PCIe switch, 64 devices, NPA, rails), 675–711
  (P2P intra-tray, all-to-all, 7 links/die), 766–768 (switch redundancy reroute).
- Related KB: `drop-mode-error-containment.md` (drop/isolation containment, BMC tray reset).
