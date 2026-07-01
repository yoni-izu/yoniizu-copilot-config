# AXI Terminator STATUS regs are sticky — FW must write-clear after capture

- **Recorded:** 2026-06-29
- **Status:** Verified from RTL + generated register headers
- **Tags:** axi termination, axi_terminator, csr_axi_termination_pkg, sticky status, AR_TERM_OCCURED, AW_TERM_OCCURED, capture freeze, pep_ctrl, pep_ctrl_aux, elbi, mstr, allow_log, fw clear

## Scope / Question

Every AXI terminator instance in the design (PIW per-port, PEP_CTRL master, PEP_CTRL_AUX / ELBI,
plus the SU / D2D users of the same package) exposes a `*_TERM_CAP_STATUS` register with two
sticky bits: `AR_TERM_OCCURED` (bit 0) and `AW_TERM_OCCURED` (bit 4). Capture (addr H/L, id,
user, for both AR and AW halves) is frozen the moment the bit sets, and **stays frozen** until
FW writes the STATUS bit back.

A FW driver that reads the STATUS + capture words on an SEI and never writes back to clear them
freezes the capture: the first event populates AR/AW addr/id/user, the SEI line fires again on
every subsequent termination, but the captured fields all carry the **first event's** payload.

## Findings

- **RTL gates capture on `~*_TERM_OCCURED`.** `el_axi_terminator.v` writes the capture latches
  only while `allow_log_*` is asserted, which is `~status_*_term_occured_data_r`. Comment in
  RTL: *"Log only 1st error, till FW clear the STATUS bit."* Once set, further AXI terminations
  still pulse the SEI but do **not** overwrite the captured fields.
- **The STATUS bits do not self-clear.** They are W1C-style sticky regs in `csr_axi_termination_pkg`;
  FW must explicitly write back the bit to release the latch.
- **Same package is reused across IPs.** The STATUS layout + sticky semantics apply identically
  to:
  - PIW per-port AXI termination capture (each PIW port_regs base + `*_TERM_CAP_STATUS`)
  - PEP_CTRL master terminator: `MSTR_TERM_CAP_STATUS @ 0x1e4` (`pep_ctrl_regs.h`)
  - PEP_CTRL_AUX (ELBI) terminator: `ELBI_TERM_CAP_STATUS @ 0x15c` (`pep_ctrl_aux_regs.h`)
  - Any SU / D2D instance that pulls in `csr_axi_termination_pkg`
- **Correct clear pattern (compare to AXI-split):** the AXI-split worker in PIW (PR #272,
  `el_piw_irq_workers.c:104-107`) shows the right pattern — write 1 to the CLR reg, then write
  0 so future events aren't masked. AXI-term wants the equivalent: write the captured STATUS
  value (or the AR/AW bit you serviced) back to STATUS after the capture read.
- **Bug surface area:** any "read STATUS + capture and notify host" worker that omits the
  write-clear will produce a stream of host notifications all carrying the first event's
  payload — extremely misleading for post-mortem analysis. Detected in
  `dwc_ep_ctl_read_axi_term` during PR #272 review.

## Sources

- RTL pkg: `Elmnt-Internal/ip.infra.rtl` → `csr/rtl/csr_axi_termination_pkg.v` (STATUS layout,
  `AR_TERM_OCCURED` bit 0, `AW_TERM_OCCURED` bit 4)
- RTL: `Elmnt-Internal/ip.infra.rtl` → `axi/rtl/el_axi_terminator.v` (`allow_log_*` gating on
  `~status_*_term_occured_data_r`; "Log only 1st error" comment)
- Generated CSR headers (workspace):
  - `fw/build_artifacts/include/prototype/pep_ctrl_aux_regs.h` (`ELBI_TERM_CAP_STATUS @ 0x15c`)
  - `fw/build_artifacts/include/prototype/pep_ctrl_regs.h` (`MSTR_TERM_CAP_STATUS @ 0x1e4`)
- FW reference for correct clear pattern: `fw/ip/pcie/drivers/el_piw/el_piw_irq_workers.c:104-107`
  (`el_piw_irq_recover_axi_split`)
- Found-via: PR #272 (`Elmnt-Internal/ip.pcie.sw.fw.ep` @ `5b49a114`) review — bug in
  `dwc_ep_ctl_read_axi_term` (`fw/ip/pcie/drivers/snps_ep_ctl/dwc_ep_ctl.c:283`)
