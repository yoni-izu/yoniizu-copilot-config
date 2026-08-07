# AER notification when host-facing error-message sending is disabled

- **Recorded:** 2026-08-06
- **Status:** Verified from doc
- **Tags:** aer, message sending disabled, reporting enable, cfg_send_cor_err, cfg_send_nf_err, cfg_send_f_err, sent error, cfg_err_sts, receive tlp bypass, tlp bypass, device control, corr_err_report_en, non_fatal_err_report_en, fatal_err_report_en, err reporting enable, serr enable, aer mask, status register, iip_aux, sei, local notification, poisoned tlp, ur, completion timeout, radm aggregate

## Scope / Question
The EP has AER interrupts of two flavors (see `send-err-signals.md`): (1) the egress
"message sent" pulses `cfg_send_cor_err/nf_err/f_err` and (2) the local status path
`cfg_*_err_sts` → PIW IIP_AUX SEI. **If the host-facing error-message-sending mechanism is
disabled, do FW still get notified?**

## Findings

### The "sent error" interrupts (flavor 1) go silent when messaging is disabled
`cfg_send_cor_err/nf_err/f_err` are defined `[EP]: "Controller has **sent** a message towards
the Root Complex ..."`. They pulse on the *actual emission* of `ERR_COR`/`ERR_NONFATAL`/
`ERR_FATAL` upstream. Message emission is gated by the Device Control / AER reporting-enable
bits (and SERR# enable / AER masks):
- `PCIE_CAP_CORR_ERR_REPORT_EN`, `PCIE_CAP_NON_FATAL_ERR_REPORT_EN`,
  `PCIE_CAP_FATAL_ERR_REPORT_EN`, `PCIE_CAP_UNSUPPORT_REQ_REP_EN`,
  `CORR_ERR_REPORTING_EN`, `NON_FATAL_ERR_REPORTING_EN`, `FATAL_ERR_REPORTING_EN`.

If those are cleared (databook Table T-7 "…Configuration to Suppress Internal Errors"), **no
message is sent → `cfg_send_*_err` never pulses → the flavor-1 SPI/SEI interrupts never fire.**
So FW must NOT rely on the "sent error" hook as its primary AER notification.

### The local status path (flavor 2) is independent of message enablement — it still fires
The `cfg_*_err_sts` sideband (databook Table T-8, "Error Reporting Status Signals") is a
**separate** mechanism from message generation. Databook p.932 states the controller
"communicates all PCIe protocol errors … to the application through error reporting status
signals. The application can use these signals to **detect errors without accessing the
controller error reporting registers**." These map 1:1 to the AER **status** register fields
(`UNCORR_ERR_STATUS_OFF` / `CORR_ERR_STATUS_OFF`), which latch on **detection** regardless of
reporting-enable/mask (mask only gates *reporting*, not status-bit setting).

The **Receive TLP Bypass** feature is the existence proof: its whole design is to *suppress*
host-facing error-message generation via Table T-7 **while still informing the application** of
the same errors through the Table T-8 `cfg_*_err_sts` signals. In Element, those signals raise
the **PIW IIP_AUX SEI** (`PEP_CTRL_AUX INTR_SEI_CAUSE`, offset 0x11c) → MuC. **So the local FW
notification survives even with all host-facing AER messaging disabled.** This is the path FW
should key on.

### Corner case — types with NO `cfg_*_err_sts` become invisible if messaging is off
The requester/completion uncorrectable types (poisoned TLP, completion timeout, completer
abort, unexpected completion, unsupported request) have **no** `cfg_*_err_sts` sideband (see
`aer-handling-completeness.md`). Their only local hooks would be the severity aggregates
(`RADM_FATAL_ERR`/`RADM_NONFATAL_ERR`, unwired by FW today) or polling the AER status regs.
Their flavor-1 `cfg_send_*_err` also disappears when messaging is disabled. Net: with host
messaging off, **these types raise no FW interrupt at all** — only an AER-status poll catches
them.

### Practical takeaway
- Rely on the **IIP_AUX `cfg_*_err_sts` SEI** for local AER notification — it is decoupled from
  host message reporting-enables/masks and keeps working when host-facing signaling is off.
- Do **not** rely on `cfg_send_*_err` (flavor 1) — it is silenced whenever the reporting-enable
  bits gate message emission.
- For full uncorrectable coverage when messaging is disabled, either wire the RADM severity
  aggregates or poll `UNCORR/CORR_ERR_STATUS_OFF`.

## Sources
- Doc: `DWC_pcie_ctl_reference.pdf` (v6.42a-lca01) Table 2-136 pp.1712-1714
  (`cfg_send_*_err` = "Controller has **sent** a message"); Table 2-145 pp.1754-1756
  (`radm_correctable_err/nonfatal_err/fatal_err` are message-**reception** signals,
  `Exists: !(CC_DEVICE_TYPE==CC_EP)` — absent on EP).
- Doc: `DWC_pcie_ctl_ep_databook.pdf` Appendix T "TLP Bypass" — Table T-7 p.932
  (reporting-enable bits set to 0 to "suppress internal error message generation"),
  Table T-8 pp.932-934 (`cfg_*_err_sts` → AER status register fields; "detect errors without
  accessing the controller error reporting registers").
- Related KB: `send-err-signals.md`, `aer-fw-notification-routing.md`,
  `aer-handling-completeness.md`.
