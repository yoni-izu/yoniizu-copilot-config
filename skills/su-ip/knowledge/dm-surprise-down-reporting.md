# DM controller Surprise Down — reporting, status and (no) counter

**Question:** Does the DWC PCIe DM controller have a Surprise Down event counter?

**Answer:** No. Surprise Down is exposed only as a capability bit, a sticky AER status bit,
and level/status output signals. No RAS DES event counter (or any other HW counter) counts it.

## Facts
- **No RAS DES event.** The full event catalogue (groups 0–7, plus CXL group 8) has no Surprise
  Down or link-down event. Closest proxies: Group 5 ev 0x00 "L0 to Recovery Entry", Group 1
  Detect EI Infer, Group 3 ev 0x00 FC Timeout. Databook §33.3.3.3, Tables 33-21..33-29, p810–814.
  Driver table `ip/se/drivers/snps_dm_ctl/dwc_dm_ras_des.c` matches it (no surprise-down entry).
- **Capability:** LINK_CAP `PCIE_CAP_SURPRISE_DOWN_ERR_REP_CAP` (bit 19, mask 0x80000, offset
  0x7C) — reset 0, RO to SW, writable only with DBI_RO_WR_EN. The hardware only exists when
  `SURPRISE_LINK_DOWN_SUPPORTED` is set. Reference p142, p2158–2160, shadow p2245.
- **Status:** AER UNCORR_ERR_STATUS `SURPRISE_DOWN_ERR_STATUS` (offset 0x104, mask 0x20) is
  sticky RW1CS: it latches until W1C or a cold/aux reset. There are also MASK and SVRITY bits.
  Reference p2271–2286. The DSP and USP layouts are identical.
- **Signals:** `surprise_down_err` (SII interrupt, exists when SURPRISE_LINK_DOWN_SUPPORTED;
  reference p1739) and `cfg_surprise_down_er_sts` (always exists; reference p1996; databook
  Table T-10, p1045).
- **SoC wiring:** `cfg_surprise_down_er_sts` is bit
  `DM_CTRL_AUX_INTERRUPT_INTR_SEI_CAUSE__CFG_SURPRISE_DOWN_ER_STS_MSK` of the DM_CTRL_AUX SEI.
  FW binds it in `ip/se/drivers/c2c/el_c2c_cause_descs.c` (`c2c_sei_dm_aux_bindings`) with
  `C2C_ACT_LOG_ERR` only. `C2C_ACT_COUNT` is an unimplemented TODO (IPSESW-238) in
  `el_c2c_irq_handlers.c`.

## Implication for FW
The only ways to get an occurrence count are:
- a FW counter driven from the AUX SEI cause, or
- reading the AER bit, counting it, and then W1C-clearing it.

Read-only polling of the AER bit gives a latched "has happened since reset" flag, not a count.

Recorded 2026-11-03 (PR review of ip.se.sw.fw#270 / ip.mng.sw.fw#490, IPMNGSW-689).
