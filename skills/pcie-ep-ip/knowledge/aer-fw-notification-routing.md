# AER local-FW notification routing — PIW/PEP wrapper SEI, not a controller interrupt

- **Recorded:** 2026-07-19
- **Status:** Verified from doc + generated register headers
- **Tags:** aer, advanced error reporting, sei, iip_aux, pep_ctrl_aux, cfg_err_sts, error reporting status signals, sideband, application error reporting interface, interrupt routing, local notification, muc, uncorr_err_status, header log, dbi, cdm, w1c

## Scope / Question
Why are PCIe AER errors delivered to firmware as **PIW** (wrapper) interrupts (the IIP_AUX SEI cause bits) rather than as controller-side interrupts, given that AER is a PCIe-standard capability?

## Findings
Two separate things must be distinguished:

1. **The AER capability itself (the standard part) is controller-side.** The architected AER
   registers — `UNCORR_ERR_STATUS_OFF`, `CORR_ERR_STATUS_OFF`, `UNCORR_ERR_SEV_OFF`,
   `ADV_ERR_CAP_CTRL` (first-error-ptr), `HDR_LOG_0..3`, `TLP_PREFIX_LOG_1..4` — live in the DWC
   controller config space and are reached via DBI/CDM. FW reads/decodes/W1C-clears these
   (this is what the AER handler does after being woken).

2. **Notifying the *local* endpoint firmware (the on-die MuC) that an AER event occurred is NOT
   part of the PCIe standard.** Standard AER signaling targets the **host / upstream** —
   ERR_COR / ERR_NONFATAL / ERR_FATAL messages and/or MSI/MSI-X into the root complex's AER
   handler. PCIe defines no mechanism for an endpoint's own microcontroller to be interrupted
   when its AER status sets. So the DWC controller has **no MuC-facing AER interrupt line**.

3. **DWC exposes the AER conditions as application sideband status signals** (databook Table T-8,
   "Error Reporting Status Signals"): `cfg_uncor_internal_err_sts`, `cfg_corrected_internal_err_sts`,
   `cfg_rcvr_overflow_err_sts`, `cfg_fc_protocol_err_sts`, `cfg_mlf_tlp_err_sts`,
   `cfg_surprise_down_er_sts`, `cfg_dl_protocol_err_sts`, `cfg_ecrc_err_sts`,
   `cfg_bad_tlp_err_sts`, `cfg_bad_dllp_err_sts`, `cfg_rcvr_err_sts`,
   `cfg_replay_number_rollover_err_sts`, `cfg_replay_timer_timeout_err_sts`, `cfg_advisory_nf_sts`,
   `cfg_hdr_log_overflow_sts`. The databook states these let "the application detect errors
   **without accessing the controller error reporting registers**." Each maps 1:1 to an AER
   register field (e.g. `cfg_ecrc_err_sts` → `ECRC_ERR_STATUS` in `UNCORR_ERR_STATUS_OFF`).

4. **The Element PIW/PEP wrapper is the block that turns those sideband signals into an MuC
   interrupt.** The wrapper CSR block `PEP_CTRL_AUX` (Element, not Synopsys — CSR source
   `pcie_csr/yml/pep_macro/PEP_CTRL_AUX.yml`) latches the controller's `cfg_*_err_sts` outputs
   into `PEP_CTRL_AUX_INTERRUPT_INTR_SEI_CAUSE` (offset 0x11c) as the `CFG_*_ERR_STS` bits, and
   raises a **SEI (System Error Interrupt)** into the PIW SEI IRQ tree → MuC. In the FW SEI tree
   this is the **IIP_AUX** node, whose DTS `device = <&ep_ctl0>` (so the walker hands the DWC
   EP-ctl device to the handler).

**Net:** the split is intentional and correct — *notification* is the wrapper's job (integration
sideband → SEI; the controller has no local interrupt for this), and *detail/decode* is the
controller's standard AER capability (DBI/CDM). So "AER is standard → it should be a controller
interrupt" conflates the standard AER *capability* (controller, host-facing) with *endpoint-local
notification* (not standardized; only exposed by DWC as sideband, hence routed through PIW).

## Sources
- Doc: `DWC_pcie_ctl_ep_databook.pdf` p.862 (Appendix L, "Application Error Reporting Interface" p.874) and p.932 Table T-8 "Error Reporting Status Signals" (`cfg_*_err_sts` → AER `*_STATUS` fields; "detect errors without accessing the controller error reporting registers").
- Regs: `prototype/pep_ctrl_aux_regs.h` — `PEP_CTRL_AUX_INTERRUPT_INTR_SEI_CAUSE` @ 0x11c, `__CFG_*_ERR_STS_MSK` bits; block CSR source `pcie_csr/yml/pep_macro/PEP_CTRL_AUX.yml` (Element wrapper).
- FW: `ip.pcie.sw.fw.ep` → `drivers/el_piw/el_piw_sei_tree.c` (`iip_aux_sei` cause desc, `el_piw_irq_recover_aer`); `drivers/snps_ep_ctl/dwc_ep_ctl_aer.c` (reads AER cap via DBI/CDM); DTS `proj_infra/soc/dts/a1_hsi.dtsi` `piw0_sei_iip_aux.device = <&ep_ctl0>`.
