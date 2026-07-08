# RAS DES coverage vs Element PCIe telemetry metrics

- **Recorded:** 2026-07-05
- **Status:** Verified from doc
- **Tags:** ras des, event counter, time based analysis, tba, telemetry, metrics, throughput, tlp count, tlp bytes, credit exhaustion, tag unavailability, drops, group 7, group 1, pciemetrics, portmetrics, pcieerrors, bandwidth

## Scope / Question
Can the DWC PCIe controller **RAS DES counters** supply the Element
`PCIeMetrics.* / PCIeErrors.* / PortMetrics.*` telemetry set (per-direction TLP
counts & bytes, credit-exhaustion drops, tag-unavailability drops, TX/RX bytes,
TX/RX errors, training-sequence errors)?

## Findings

### What RAS DES (v6.42a-lca01) actually provides — databook §29.3.3
- **Event Counter Group #7** (32-bit RAM, Layer3 non-error): per-TLP-**type COUNTS**.
  - TX: 0x00 Mem Write, 0x01 Mem Read, 0x04 IO Write, 0x05 IO Read,
    0x06 Cpl w/o data, 0x07 Cpl w data, 0x08 Msg, 0x09 Atomic, 0x0A w/Prefix.
  - RX: 0x0B Mem Write, 0x0C Mem Read, 0x0D Cfg Write, 0x0E Cfg Read,
    0x0F IO Write, 0x10 IO Read, 0x11 Cpl w/o data, 0x12 Cpl w data, 0x13 Msg, 0x14 Atomic.
- **Event Counter Groups #0–#3** — physical/DLL/TL **error** events:
  G0 (per-lane): EBUF ovf/underrun, decode, disparity, SKP parity, sync-hdr, RX-valid-deassert.
  G1 (common): receiver error, framing error, deskew error, N_FTS timeout, RX recovery req.
  G2: **Bad TLP**, LCRC error, Bad DLLP, replay rollover, replay timeout, RX/TX Nak DLLP, retry TLP.
  G3: **FC timeout**, poisoned TLP, ECRC, UR, completer abort, completion timeout.
- **Time-Based Analysis (TBA)**: only **aggregate** throughput + LTSSM residency.
  - TBA G1 0x00 = **TX PCIe TLP data payload**, 0x01 = **RX PCIe TLP data payload**
    (units of 16 bytes, windowed measurement — NOT a free-running total, payload-only).
  - TBA G0 = % time in TX/RX L0s, L0, L1/L1.1/L1.2, Config/Recovery, L2.
- **NOT present in RAS DES:** any credit-exhaustion counter, any tag-availability
  counter, any **per-TLP-type BYTE** counter, any lumped TS-error / RX-error / TX-error total.

### Directionality for the EP
Outbound = device-initiated = **TX** (DMA read/write, completions returned to host for
host's requests to device BARs). Inbound completions = device's own DMA reads returning = **RX**.

### Metric-by-metric verdict
| Metric | RAS DES? | Source / gap |
|---|---|---|
| OutboundReadTLPCount | YES | G7 0x01 TX Mem Read |
| OutboundWriteTLPCount | YES | G7 0x00 TX Mem Write |
| OutboundCompletionTLPCount | YES | G7 0x06+0x07 |
| InboundCompletionTLPCount | YES | G7 0x11+0x12 |
| Outbound/Inbound *…TLPBytes* (per type) | NO | RAS gives counts OR aggregate TBA bytes, never per-type bytes |
| TXBytes / RXBytes | PARTIAL | TBA G1 0x00/0x01 aggregate payload throughput (windowed, payload-only) |
| CompletionCreditExhaustionDrops | NO | no credit counter in RAS DES |
| NPCreditExhaustionDrops | NO | no credit counter in RAS DES |
| TagUnavailabilityDrops | NO | no tag counter in RAS DES |
| TrainingSequenceErrorCount | NO/PARTIAL | closest = G0 sync-hdr / G1 framing/receiver/deskew |
| RXErrors | PARTIAL | granular only (G0/G1/G2); no lumped total |
| TXErrors | NO/PARTIAL | only G2 TX-Nak/replay-timeout/rollover/TX-nullified |

### Conclusion
The `…CreditExhaustionDrops`, `TagUnavailabilityDrops`, and per-type `…Bytes`
metrics are **Element-internal (PIW / application-datapath) telemetry**, not sourced
from Synopsys RAS DES. RAS DES covers the four TLP *count* metrics and approximates
TX/RX aggregate bytes; everything else needs a custom HW datapath monitor or
FW-maintained counters.

### Cannot estimate bytes as TLP_count × MPS
Multiplying a Group#7 TLP count by MPS gives only a loose **upper bound**, and is wrong for reads:
- MPS is a ceiling; TLPs range 1 DW … MPS. 4 KB-boundary rule + RCB force partial first/last TLPs.
- **Read requests carry NO payload** — data returns as completions, so OutboundReadTLPCount×MPS = 0 real bytes.
- Read **completions are bounded by RCB** (typ. 64/128 B), usually < MPS ⇒ InboundCompletionTLPCount×MPS overestimates.
- Effective MPS = min() of both ends; DMA may use less.
- Correct byte source = TBA G1 0x00/0x01 (real payload bytes, 16-B units) or FW/datapath per-type byte counters.
  This is why `…TLPBytes` metrics exist separately from `…TLPCount`.

## Sources
- Doc: `DWC_pcie_ctl_ep_databook.pdf` §29.3.3 Statistics — Time-Based Analysis p.712-716
  (Tables 29-16/29-17), Event Counters p.716-722 (Tables 29-21…29-28, esp. Group #7).
- Doc: same, §29.3.2 Error Injection p.710-712 (context).
