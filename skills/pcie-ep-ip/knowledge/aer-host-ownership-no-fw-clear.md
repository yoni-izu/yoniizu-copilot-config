# AER status registers are host-owned — FW must not W1C them (and W1C pops the header-log queue)

- **Recorded:** 2026-07-27
- **Status:** Verified from doc + generated register headers
- **Tags:** aer, uncorr_err_status_off, corr_err_status_off, w1c, rw1c, header log, hdr_log, first error pointer, multiple header logging, host ownership, err_cor, err_nonfatal, err_fatal, root complex, aer driver, need to know, sei mask, pep_ctrl_aux, intr_sei_mask, el_irq_tree, cause reasserting, ownership

## Scope / Question
Local FW gets an IIP_AUX SEI when an AER condition sets. May FW read *and clear* (W1C) the
controller's AER status registers to stop the SEI cause re-asserting? What breaks if it does?

## Findings

### AER is a host-facing capability — the status registers are the host's record
The architected AER registers live in the controller's **config space** (reached by FW via
DBI/CDM, but by the host via normal config reads). The PCIe error model is:
1. Controller detects the error, sets the AER status bit, latches the TLP header into the
   Header Log, and **sends ERR_COR / ERR_NONFATAL / ERR_FATAL upstream** to the root complex.
2. The **host's AER driver** services that message by reading `UNCORR_ERR_STATUS_OFF` /
   `CORR_ERR_STATUS_OFF` (+ severity, First Error Pointer, Header Log) to classify the error,
   and only then RW1C-clears the status.

So the status/header-log state is the **host's** to read and clear. Endpoint FW is a
*need-to-know* observer: reading is fine, clearing is not.

### W1C on the status register also clears/advances the AER header-log queue
Databook p.158, Table 3-6 (shared VF header log full behavior) states the header log
"can be cleared or updated to the next stored header log, if one exists, **by writing to the bit
of the Uncorrectable Status Error Register pointed to by the First Error Pointer**."

That write *is* the header-log pop mechanism for AER Multiple Header Logging (supported on this
controller, §3.4.4.2). So an FW W1C does not merely erase status bits — it **discards the logged
TLP header** the host was going to read. Related databook note (p.157-158): "If your software
does not read the header log registers fast enough, you can miss the new header log information
when the queue is full."

### FW always wins the race against the host
The IIP_AUX SEI reaches the MuC and the ISR runs within microseconds of detection. Host AER
interrupt latency (MSI → RC AER driver → config reads) is orders of magnitude longer. An FW
W1C in the SEI handler therefore clears the status essentially **every time before the host
looks**, so the host sees an ERR message followed by an all-zero status register — an
unclassifiable error report, with the header log gone too.

### Stopping the SEI cause does NOT require clearing AER status
- The Element wrapper cause register `PEP_CTRL_AUX_INTERRUPT_INTR_SEI_CAUSE` @ 0x11c is itself
  W1C (`__SW_ACCESS 0x3`, vs `0x2` = RW, `0x0` = RO), and the generic `el_irq_tree` walker
  already performs **post-order W1C with a retry budget** on every cause register it walks
  (`w1c_and_retry()` in `infra/drivers/el_irq_tree/el_irq_tree.c`).
- If `cfg_*_err_sts` level-follows the AER status field (Table T-8 maps each signal 1:1 onto an
  AER `*_STATUS` field), the wrapper cause can keep re-asserting until the AER bit clears. The
  correct lever is then **`PEP_CTRL_AUX_INTERRUPT_INTR_SEI_MASK` @ 0x120** (RW) — mask those
  cause bits — or don't wire them at all. Note the walker's retry budget means a stuck cause
  degrades to a `LOG_WRN` ("kept reasserting after N retries"), not an interrupt storm.

**Net rule:** FW may **read** AER status / severity / First Error Pointer / Header Log for local
telemetry, counters and crash-dump context. FW must **not write** `UNCORR_ERR_STATUS_OFF` /
`CORR_ERR_STATUS_OFF` (nor otherwise pop the header log) on a system where the host owns AER.
Silence a chatty AER-derived SEI with the wrapper's SEI mask instead.

### Corollary — a FW-side "snapshot" shadow is a symptom of the clear, not a feature
Capture-before-clear snapshots exist only because the clear destroys the HW state. Without the
clear the registers remain readable, so a management/OOB pull should read them **live**. A RAM
shadow is strictly worse than the HW it mirrors: the controller has a real header-log *queue*
(Multiple Header Logging, §3.4.4.2) vs a single shadow entry; a `if (!valid)` first-entry latch
with no re-arm caller freezes on the first error after boot; there is no timestamp; and it
desynchronizes from the host once the host does its own RW1C.

Likewise, FW per-type AER counters derived from re-reading the sticky status bits are only
coherent if FW clears them — otherwise every later interrupt re-counts the same set bits.
**This does not mean FW should drop the counters** — FW-side AER event counts are a hard
manageability requirement (`PcieCorrectableErrorCount`, `PcieFatalErrorCount`,
`PcieNonFatalErrorCount` + their warning/critical thresholds; see
`ras-manageability/knowledge/pcie-aer-fw-counters.md`), and RAS DES cannot supply them
(no severity classification, volatile, not persisted into RAS history).

What must change is the **derivation**: count from the **wrapper SEI cause bitmap**, which is
FW-owned, edge-latched and already W1C'd per-bit by the `el_irq_tree` walker — not from
re-reading the host-owned sticky AER status register. Read AER status / severity / header log
only as *context* for the log line. Per-type raw counts that RAS DES already provides
(Group #3: poisoned TLP, ECRC, completion timeout, UR, completer abort; Group #2: Bad TLP,
Bad DLLP, replay rollover/timeout) are better taken from RAS DES than recomputed in FW.
If a shadow really must survive a host clear, use a timestamped bounded ring updated on every
error, never a sticky first-ever single entry.

## Sources
- Doc: `DWC_pcie_ctl_ep_databook.pdf` p.157 §3.4.4.1 (on error: sets AER status, generates error
  MSG), p.157-158 §3.4.4.2 "AER Multiple Header Logging" + Table 3-6 (status write = header-log
  clear/advance), p.932-933 Table T-8 (`cfg_*_err_sts` ↔ AER `*_STATUS` field 1:1 mapping).
- Regs: `prototype/pep_ctrl_aux_regs.h` — `INTERRUPT_INTR_SEI_CAUSE` @0x11c (`SW_ACCESS 0x3`),
  `INTERRUPT_INTR_SEI_MASK` @0x120 (`SW_ACCESS 0x2`), `INTERRUPT_INTR_SEI_MASKED_INTR` @0x128.
- FW: `infra/drivers/el_irq_tree/el_irq_tree.c` (`w1c_and_retry`, post-order W1C);
  `ip.pcie.sw.fw.ep` → `drivers/snps_ep_ctl/dwc_ep_ctl_aer.c` (`dwc_ep_ctl_report_aer_errors`
  W1C of both status regs — flagged in review of PR #305).
- Related KB: `aer-fw-notification-routing.md`, `aer-handling-completeness.md`.
