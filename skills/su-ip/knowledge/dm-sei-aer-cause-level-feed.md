# DM SEI AER cause bits: level-fed from sticky AER status (no edge detect) — counting dispatches ≠ counting errors

**Question:** Can FW count per-port AER errors from the DM_CTRL_AUX SEI cause interrupts
without clearing AER status? What re-asserts after W1C of the cause?

**Answer:** No. The per-type AER cause bits are level-fed from the controller's sticky AER
status outputs; W1C of the cause re-latches the next cycle while the status is set, and the
SEI re-asserts. Only clearing AER status (not FW's to do), masking the cause, or reset stops it.

## Facts (verified 2026-11-10..12, Neo mirror snapshot 2026-11-09 + DWC reference manual)

- **Chain:** AER status (RW1CS, PCIe cfg space) → controller level output `cfg_*_er_sts`
  (follows the sticky bit; databook T-10 p1045, reference p1996) → wrapper wires it straight
  to the AUX SEI cause set-input (`*_pulse_intr` port name is misleading; no edge detect;
  `ip.se.rtl dm_macro/rtl/dm_pcie_iip_device_wrap.v` ~1399–1427 @60ddeee6) → cause field
  written every cycle as `input | hold | sw-inject`, W1C clear wins one cycle only
  (`ip.infra.rtl csr/rtl/csr_field.v` ~65–73, no `W1C_HW_SET`) → registered SEI summary
  (`cause & ~mask`) re-asserts after a one-beat dip. Applies to all 18 AUX SEI cause bits,
  correctable and uncorrectable alike.
- **eFEC contrast:** the same wrapper deliberately rising-edge-detects `efec_intr`
  (~582–600). The AER inputs get no such stage — edge treatment exists in this path by design
  choice, not limitation.
- **`cfg_send_f_err`/`cfg_send_nf_err`** = AUX **SEI** cause bits 15/16; **`cfg_send_cor_err`**
  = AUX **SPI** cause bit 2 (not SEI). All wired directly, no edge detect
  (wrapper L1414/1420/1415; fields `csr_dm_ctrl_aux_fields.v` L625–633, L683–685; RAL
  `ral_block_DM_CTRL_AUX.sv` ~L2185/2191/2587). Reference manual (p1713–1714) describes them
  as "controller has sent an ERR_COR/ERR_NONFATAL/ERR_FATAL message" event indications,
  synchronous to `aux_clk_g`, per-function vectors `[(CX_NFUNC-1):0]` (wrapper truncates to
  one bit). **NOT explicitly "one-clock-cycle pulse"** — unlike every `radm_*` signal — so
  pulse-vs-level at the controller output is unverified (DWC black box). Also unverified:
  whether a new error of an already-latched-status type regenerates the message, and whether
  ERR message generation is active at all in A1 proprietary C2C mode (no RC present).
- **`radm_fatal_err`/`radm_nonfatal_err`** = DM CORE SEI cause bits 3/4; reference p1756:
  "One-clock-cycle pulse that indicates the controller **received** an ERR_FATAL message" —
  pulses, but they count the **link partner's** reported errors, not local ones.
- **FW counting implication (IPSESW-162, ip.se.sw.fw#287):** counting per-type SEI dispatches
  multiplies one latched error by the IRQ-tree retry limit (~20/walk, more on later walks).
  Viable per-occurrence local sources: send_f/send_nf (if pulse + enabled in C2C mode),
  send_cor (SPI class), RAS DES 8-bit HW event counters for correctable (workbook lists
  grp3 0x01–0x05 alternative). A2 HW per-port counters tracked in FCATLRTL-1397.
- **Ownership:** AER status is the error-handling agent's to clear, not device FW's. A2
  PCIe-compliant mode (third-party partners) hard-forbids FW clears; on A1 proprietary P2P
  links a FW clear is technically harmless but needs an explicit ownership ruling, and our
  own telemetry (su_surp_link_down, SuAerInfo) reads the sticky bits.

Recorded 2026-11-12 (re-review of ip.se.sw.fw#287 / ip.mng.sw.fw#509, IPSESW-162).
