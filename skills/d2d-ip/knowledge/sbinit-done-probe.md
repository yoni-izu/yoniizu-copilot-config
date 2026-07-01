# SBINIT-done probe: CMN_TOP_INT2.SPARE_CFG0_INT2 vs INTR1_SUMMARY.CMN_TOP_INT1

- **Recorded:** 2026-06-29
- **Status:** Verified from spec + generated register header (aw_global_reg_regs.h)
- **Tags:** sbinit, sb init, sideband init, cmn_top_int2, spare_cfg0, cmn_top_int1, intr1_summary, rom marker, boot probe, link bring-up

## Scope / Question
How FW probes whether the ROM completed UCIe.SB initialization (SBINIT), and why the
persistent spare bit is the correct register vs the interrupt-summary bit.

## Findings
- **SBINIT** = UCIe Sideband Initialization: SB interface trained/repaired at 800 MHz before
  MBINIT. ROM performs SBINIT before the kernel runs.
- **Old probe (fragile):** `REG_GLOBAL_INTR1_SUMMARY (0x64) . CMN_TOP_INT1 (bit26, msk 0x4000000)`
  is a live interrupt **summary** roll-up of the CMN_TOP_INT1 register. The runtime D2D ISR
  reads INTR1_SUMMARY and dispatches CMN_TOP_INT1 → `el_d2d_wrapper_handle_cmn_top_int`, so it is
  serviced/cleared at runtime — unreliable as a persistent "done" probe.
- **New probe (correct):** `CMN_TOP_INT2 (0xd4) . SPARE_CFG0_INT2 (bit16, msk 0x10000)`.
  CMN_TOP_INT2 is the second common-top interrupt register; bits 16-31 are SPARE_CFG0..15 —
  software scratch fields not tied to a HW source, so they persist and aren't touched by the
  INT1 ISR. Repurposing SPARE_CFG0 as a sticky ROM→FW "SBINIT complete" marker is the right
  register family. Probe = read CMN_TOP_INT2 per wrapper, success iff bit16 set.
- **Residual contract:** depends on ROM setting SPARE_CFG0_INT2 and masking it so it raises no
  spurious INT2; that handshake is a ROM/FW convention, not in the D2D spec.

## Sources
- Spec: `D2D Specification Rev 2.0.0.docx` paras 360-377 (SBINIT sequence)
- Header: `aw_global_reg_regs.h` — `CMN_TOP_INT2=0xd4`, `SPARE_CFG0_INT2_MSK=0x10000/SFT=16`;
  `INTR1_SUMMARY=0x64`, `CMN_TOP_INT1_MSK=0x4000000/SFT=26`
- FW: `ip.d2d.sw.fw` → `drivers/d2d_wrapper_interrupts.c` (INT1 ISR), `el_d2d_wrapper.c::el_d2d_wrapper_read_sbinit_status`
