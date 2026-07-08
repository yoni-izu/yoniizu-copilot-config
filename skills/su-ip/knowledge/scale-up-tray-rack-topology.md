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
