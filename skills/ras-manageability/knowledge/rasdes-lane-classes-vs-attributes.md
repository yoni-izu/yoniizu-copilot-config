# RAS DES lane classes vs manageability attributes — every consumed counter is common-lane

- **Recorded:** 2026-08-07
- **Status:** Verified from spec (recorded attribute set) + FW + controller databook
- **Tags:** ras des, per lane, common lane, event counter group, lane select, telemetry
  attributes, pdr, per link, pciebadtlperrorcount, pciebaddllperrorcount, pcielcrcerrorcount,
  pcieretrytlpcount, pciereceivererrorcount, api design, middleware, group 0, group 4

## Scope / Question
RAS DES event counters are split into per-lane groups and common-lane groups. Which of them
actually back Device Manageability attributes, and what does that imply for the shape of the FW
counter-read API (single getter with a `lane` argument vs. split per-lane / common getters)?

## Findings

### Lane classes in the controller
- **Per-lane groups: #0 and #4 only.**
  G0 = Layer1 errors (EBUF overflow/underrun, decode, running disparity, SKP-OS parity,
  sync-header, RX-valid deassertion, retimer parity, margin CRC parity).
  G4 = Layer1 non-error (EBUF SKP add / SKP del).
- **Common-lane: groups #1, #2, #3, #5, #6, #7, #8.**
- The lane index reaches hardware via `EVENT_COUNTER_CONTROL_REG.EVENT_COUNTER_LANE_SELECT`;
  it is ignored by common-lane groups.

### Every manageability attribute sourced from RAS DES is common-lane
The Device Manageability Spec PCIe EP attribute set is indexed **per link (`L<x>`), never per
lane**. Mapping the DLL/PHY-layer attributes onto the counter groups:

| Attribute (per link) | Counter | Group | Per-lane? |
|---|---|---|---|
| `PcieBadTlpErrorCount` | Bad TLP | 2 | no |
| `PcieBadDllpErrorCount` | Bad DLLP | 2 | no |
| `PcieLcrcErrorCount` | LCRC error | 2 | no |
| `PcieReplayNumberRolloverCount` | Replay number rollover | 2 | no |
| `PcieRetryTlpCount` | Retry TLP | 2 | no |
| `PcieNakSentCount` / `PcieNakReceivedCount` | TX / RX Nak DLLP | 2 | no |
| `PcieReceiverErrorCount` | Receiver error | 1 | no |

The TLP-count metrics (`OutboundReadTLPCount`, `OutboundWriteTLPCount`,
`Outbound/InboundCompletionTLPCount`) are Group #7 — also common-lane
(see `pcie-ep-ip/knowledge/rasdes-vs-telemetry-metrics.md`).

**Groups 0 and 4 back zero manageability attributes.** Per-lane counters serve PHY/link
diagnostics only: bring-up, margining, silicon debug, SV shell dumps.

### API-design consequence
A single `read_counter(dev, counter, lane, count)` makes `lane` meaningless on 100% of the
manageability path — the largest consumer — where it can only be ignored or passed wrong. The
two classes are two consumers with different access patterns, not two shapes of one:

- **Common-lane** → OOB manageability: per-link PDR attributes, MC-polled, FW-aggregated,
  threshold-compared into Critical Chip Status. Scalar result, no lane.
- **Per-lane (G0/G4)** → diagnostics. Vector result over lanes; callers already loop.

Any counter sweep must branch on lane class anyway (a per-lane counter yields 8 values, a common
one yields 1), so the discriminator leaks to callers regardless. Splitting the API matches how
the counters are actually consumed.

**Design note:** splitting only the *functions* while both still take one shared counter enum
buys nothing — it relocates the runtime check. The compile-time guarantee requires two enum
types (`..._common_counter` / `..._lane_counter`). Both can still be drawn from the same packed
`{group, event}` selector space, so the register-encoding helpers are unchanged; a per-lane
marker bit in the enum value then becomes redundant with the type.

### Related: continuity
These counters are sticky across warm reset by design — see
`pcie-ep-ip/knowledge/rasdes-counter-reset-persistence.md`. FW must not clear them on bring-up if
they feed monotonic per-link attributes.

### Counter widths / saturation — by design, do not "fix" in FW
Counter widths differ by group (per-lane groups are narrow). This is a deliberate HW design
choice, not a defect: the actionable signal from these error counters is order of magnitude —
"a couple" vs "pinned at MAX" is enough to classify which issue is present and how severe it is.
FW should **not** add rollover tracking, wide shadow accumulators, or high-rate polling to chase
overflow; that is cost with no diagnostic value. Saturation at MAX is a valid terminal state
meaning "many".

## Sources
- Spec: `Device Manageability Specification.docx` — PCIe EP telemetry/attribute table
  (per-link `L<x>` indexing; DLL/PHY-layer count attributes).
- Doc: `DWC_pcie_ctl_ep_databook.pdf` §29.3.3 — event counter groups #0–#8, lane-select field.
- FW: `ip.pcie.sw.fw.ep` → `include/dwc_ep_ras_des.h` (packed selector, per-lane marker,
  `enum pcie_ras_des_counter`), `drivers/snps_ep_ctl/dwc_ep_ras_des.c`
  (`dwc_ep_ctl_read_ras_des_counter`, `dwc_ep_ctl_ec_read`, `dwc_ep_ctl_ec_is_valid`),
  `drivers/snps_ep_ctl/dwc_ep_ctl_shell_sv.c` (per-lane vs common dump paths).
- Related KB: `pcie-ep-ip/knowledge/rasdes-vs-telemetry-metrics.md`,
  `ras-manageability/knowledge/pcie-aer-fw-counters.md`,
  `pcie-ep-ip/knowledge/rasdes-counter-reset-persistence.md`.
