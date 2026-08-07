# PCIe AER — FW's job is counting (for RAS/manageability), not clearing

- **Recorded:** 2026-07-27
- **Status:** Verified from spec + FW
- **Tags:** aer, pcie ep, error counters, pciecorrectableerrorcount, pciefatalerrorcount, pcienonfatalerrorcount, warning threshold, critical threshold, critical chip status, ras history, pdr initialization, telemetry attributes, pcieaerinfopart1, radm_fatal_err, radm_nonfatal_err, ras des, sei cause, per port, fw responsibility

## Scope / Question
What is FW's actual responsibility for PCIe EP AER errors? Should FW maintain its own AER
error counters, or is that redundant with the controller's RAS DES event counters?

## Findings

### FW's AER role = observe, log, **count**, report — never clear
The host owns the AER capability registers (see
`pcie-ep-ip/knowledge/aer-host-ownership-no-fw-clear.md`). FW's contribution to AER is the
**OOB/manageability** view that the host-side AER path cannot provide: per-port event counts,
threshold comparison, and persistence into RAS history.

### The counters are directly-required manageability attributes
Device Manageability Specification, PCIe EP attribute set (per link `L<x>`):

| Attribute | Notes |
|---|---|
| `PcieCorrectableErrorCount` | + `PcieCorrectableErrorCountWarningThreshold`, `PcieCorrectableErrorsAboveLimit` |
| `PcieFatalErrorCount` | + `PcieFatalErrorCountWarningThreshold`, `PcieFatalErrorCountCriticalThreshold` |
| `PcieNonFatalErrorCount` | + `PcieNonFatalErrorCountWarningThreshold`, `PcieNonFatalErrorCountCriticalThreshold` |
| `PcieUncorrectableInternalErrorCount` | |
| `PcieUnsupportedRequestCount` | |
| `PcieAerInfoPart1` / `Part2` | "Captures PCIe AER register **or log** data, part 1, per link" — a *pull*, so read live registers at pull time |

DLL/PHY-layer siblings also exist (`PcieBadTlpErrorCount`, `PcieBadDllpErrorCount`,
`PcieReceiverErrorCount`, `PcieReplayNumberRolloverCount`, `PcieRetryTlpCount`,
`PcieNakSent/ReceivedCount`, `PcieLcrcErrorCountL`) — those map cleanly onto RAS DES Group #2/#1.

The counts are **not** cosmetic: `PcieFatalErrorCountCriticalThreshold` is "threshold for PCIe
uncorrectable fatal error count for setting **Critical Chip Status** indication". Over-counting
therefore mis-declares a healthy device critical.

### They are persisted RAS history, not free-running volatile counters
Device RAS initialization restores, per the spec: "PCIe EP ports: Number of non-fatal
uncorrectable errors per port / Number of fatal uncorrectable errors per port", and "All
counters listed here are initialized based on their initialization PDR." So the counters must
be per-port, survive reset, and be seedable from the MC (flashless). RAS DES counters are
volatile controller state and cannot serve this.

### RAS DES cannot replace these counters
RAS DES Event Counter Groups #2/#3 count *raw per-type* events (Bad TLP, Bad DLLP, replay
rollover/timeout; poisoned TLP, ECRC, completion timeout, UR, completer abort). It has **no
severity classification** — there is no fatal / non-fatal aggregate counter. The spec's
required `PcieFatalErrorCount` / `PcieNonFatalErrorCount` need the AER severity register
(`UNCORR_ERR_SEV_OFF`) applied to the uncorrectable event — that classification is FW's
value-add. Use RAS DES for the raw per-type counts, FW for severity-classified + persisted ones.

### Correct derivation: count from the FW-owned SEI cause, not the host-owned AER status
Deriving counts by re-reading the sticky RW1C AER status register only works if FW clears it
(which it must not) — otherwise every later interrupt re-counts the same still-set bits and the
counter inflates without bound, straight into the critical-status threshold.

The event source FW *does* own is the wrapper SEI cause register, which is edge-latched and
already W1C'd per-bit by the `el_irq_tree` walker, which dispatches **per asserted bit**. Count
there. For the severity split, the exact-fit triggers are IIP_CORE
`PEP_CTRL_CORE ... INTR_SEI_CAUSE__RADM_FATAL_ERR` / `RADM_NONFATAL_ERR` — the controller's
uncorrectable fatal / non-fatal aggregates, which map 1:1 onto `PcieFatalErrorCount` /
`PcieNonFatalErrorCount` and also close the IIP_AUX per-type coverage gap (the 15 `CFG_*_ERR_STS`
bits miss poisoned TLP, completion timeout, completer abort, unexpected completion, UR).
Correctable is well covered by the IIP_AUX correctable `CFG_*` bits.

## Sources
- Spec: `Device Manageability Specification.docx` — PCIe EP telemetry/attribute table
  (`PcieCorrectableErrorCount`, `PcieFatalErrorCount`, `PcieNonFatalErrorCount`,
  `PcieUncorrectableInternalErrorCount`, `PcieUnsupportedRequestCount`, `PcieAerInfoPart1/2`,
  `*WarningThreshold` / `*CriticalThreshold`); Device RAS initialization section
  (per-port fatal / non-fatal uncorrectable counts, "initialized based on their initialization PDR").
- Doc: `DWC_pcie_ctl_ep_databook.pdf` §29.3.3 RAS DES event counter groups #1/#2/#3.
- FW: `ip.pcie.sw.fw.ep` → `drivers/snps_ep_ctl/dwc_ep_ctl_aer.c` (`aer_update_counters`),
  `drivers/el_piw/el_piw_sei_tree.c` (`iip_core_sei_ctx` does not wire RADM_FATAL/NONFATAL);
  `infra/drivers/el_irq_tree/el_irq_tree.c` (per-bit dispatch + post-order W1C).
- Related KB: `pcie-ep-ip/knowledge/aer-host-ownership-no-fw-clear.md`,
  `pcie-ep-ip/knowledge/aer-handling-completeness.md`,
  `pcie-ep-ip/knowledge/rasdes-vs-telemetry-metrics.md`.
