# AER handling completeness — per-type IIP_AUX triggers vs severity aggregates (RADM_FATAL/NONFATAL)

- **Recorded:** 2026-07-19
- **Status:** Verified from doc + generated register headers
- **Tags:** aer, general error handling, fatal, non-fatal, correctable, severity, radm_fatal_err, radm_nonfatal_err, radm_cpl_timeout, iip_core, iip_aux, pep_ctrl_core, pep_ctrl_aux, cfg_err_sts, sei cause, trigger coverage, poisoned tlp, completion timeout, completer abort, unsupported request, app_err_bus, receive tlp bypass, error reporting status signals

## Scope / Question
Does wiring the IIP_AUX SEI `CFG_*_ERR_STS` bits give *complete* AER handling, or is the
canonical "fatal → walk all fatal status bits / non-fatal → walk all non-fatal bits" model
missing? Is there a severity-grouped controller trigger a general handler should use instead?

## Findings

### The IIP_AUX CFG_*_ERR_STS set is a partial, per-type subset — not the full AER set
The 15 `PEP_CTRL_AUX_INTERRUPT_INTR_SEI_CAUSE__CFG_*_ERR_STS` bits are the DWC controller's
**"Receive TLP Bypass Error Reporting"** status signals (databook Table T-8): the receive
datapath / DLL / protocol errors it detects while passing a TLP through as good. They cover:
DL protocol, surprise-down, FC protocol, malformed TLP, receiver overflow, ECRC, uncorr/corrected
internal, and the correctable set (bad TLP, bad DLLP, receiver err, replay rollover/timeout,
advisory-NF, hdr-log-overflow).

They do **NOT** include the **requester/completion uncorrectable** AER types:
poisoned TLP, completion timeout, completer abort, unexpected completion, unsupported request,
TLP-prefix-blocked. Those are detected in the transaction layer (and several are `app_err_bus`
*inputs* the application drives into AER — databook Table L-7), so they have no `cfg_*_err_sts`
sideband and thus **no IIP_AUX trigger**. An AER event of one of those types, if it is the only
pending error, sets the AER status register but never raises an IIP_AUX SEI → the per-type
handler never runs for it (it only gets swept up if some other CFG_* error later fires the SEI
and the handler's full-status read happens to catch it). Ironic tell: the FW counters count
`poisoned_tlp` and `completion_timeout` — two types with no IIP_AUX trigger.

### The severity-grouped "general handler" trigger exists — RADM_FATAL_ERR / RADM_NONFATAL_ERR
The controller exposes aggregate **severity** error outputs, captured by Element in the
**PEP_CTRL_CORE (IIP_CORE)** SEI cause register:
- `PEP_CTRL_PEP_CTRL_CORE_INTERRUPT_INTR_SEI_CAUSE__RADM_FATAL_ERR_MSK`
- `PEP_CTRL_PEP_CTRL_CORE_INTERRUPT_INTR_SEI_CAUSE__RADM_NONFATAL_ERR_MSK`
- `...__RADM_CPL_TIMEOUT_MSK`, `...__RADM_QOVERFLOW_MSK`

`radm_fatal_err` / `radm_nonfatal_err` are the DWC receive-datapath aggregate outputs that assert
when *any* uncorrectable error classified (per the AER severity register) as fatal / non-fatal is
detected — the same detection that drives the outbound ERR_FATAL / ERR_NONFATAL messages
(databook p.222 Table 3-34; RAS chapter p.667). These are exactly the canonical "one fatal / one
non-fatal interrupt, then FW reads the full AER status + severity and walks all set bits" trigger.

### Current FW gap
FW's `iip_core_sei_ctx` handles only `RTLH_FC_OVERFLOW`, `RPLH_FLIT_*` and `RADM_CPL_TIMEOUT`
(LOG-only) — it does **not** wire `RADM_FATAL_ERR` / `RADM_NONFATAL_ERR` at all. So the
severity aggregates that would give complete uncorrectable-AER coverage are present in HW but
unhandled. The AER handler function itself is already general (reads full uncorr+corr status,
classifies via the severity register, walks every bit); only the *trigger* is the partial
IIP_AUX per-type subset.

### For truly general handling, pick one
1. Key the AER recover worker on **IIP_CORE `RADM_FATAL_ERR` + `RADM_NONFATAL_ERR`** (uncorrectable
   severity aggregates) — plus keep the IIP_AUX correctable per-type bits (correctable is well
   covered there). This is the closest match to the host-side AER model.
2. Or **poll** the AER uncorr/corr status registers (severity-classify + walk all bits),
   decoupling from sideband trigger coverage.
3. Routing every AER-relevant per-type SEI bit is fragile — the requester/completion errors have
   no per-type SEI bit, so per-type routing can never be complete.

Note: severity-grouped COR/NONFATAL/FATAL *messaging* is outbound to the host (standard PCIe);
the local-FW equivalent is these RADM aggregate SEI causes, not a message.

## Sources
- Doc: `DWC_pcie_ctl_ep_databook.pdf` p.222 Table 3-34 (COR/NONFATAL/FATAL message generation),
  Appendix L.5 "Application Error Reporting Interface" p.873-875 (Table T-8 `cfg_*_err_sts` receive-
  bypass set; Table L-7 `app_err_bus`→AER-uncorr mapping incl. poisoned/cpl-timeout/cpl-abort/UR),
  RAS ch.29 p.667+.
- Regs: `prototype/pep_ctrl_aux_regs.h` (`INTERRUPT_INTR_SEI_CAUSE__CFG_*_ERR_STS`),
  `pep_ctrl_core` SEI cause (`...CORE_INTERRUPT_INTR_SEI_CAUSE__RADM_FATAL_ERR/RADM_NONFATAL_ERR/RADM_CPL_TIMEOUT/RADM_QOVERFLOW`).
- FW: `ip.pcie.sw.fw.ep` → `drivers/el_piw/el_piw_sei_tree.c` (`iip_aux_sei_ctx` wires 15 CFG_* bits
  to `el_piw_irq_recover_aer`; `iip_core_sei_ctx` does NOT wire RADM_FATAL/NONFATAL);
  `drivers/snps_ep_ctl/dwc_ep_ctl_aer.c` (`dwc_ep_ctl_report_aer_errors` reads full AER status).
- Related KB: `aer-fw-notification-routing.md`, `rasdes-vs-telemetry-metrics.md`.
