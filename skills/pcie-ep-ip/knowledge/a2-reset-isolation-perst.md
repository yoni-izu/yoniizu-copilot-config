# A2 reset, isolation, PERST and hot reset

- **Recorded:** 2026-11-09
- **Status:** Verified by Neo against A2 RTL/CSR (unknowns marked)
- **Tags:** a2, reset, isolation, rst_iso_en, rst_iso_timeout, iso watchdog, cfg_iso_sw_man_sl, perst, puc irq, hot reset mask, flr, wd_cnt_clr, bp_mode, drain, termination mask
- **Source:** A2 RTL/CSR read by Neo (`neo-ask --chip a2`), 2026-11-09 mirror: ip.pcie.rtl@77b45f1, ip.pcie.csr@6904415, project.atlantic2.rtl@875d851, project.atlantic2.csr@2a12a536, eip_pcie@1b48d8dc, ip.psoc.rtl@2bcf174c, ip.psoc.csr@40fb726, ip.infra.rtl@8dd7f12f, ip.infra.csr@e16cbf5. Raw answers: HSI A2 PCIe plan review (session files pcie_review/a/NN_*.md).

## Findings
**Isolation and reset**
- `RST_ISO_TIMEOUT` is at 0x40.
- FW must also handle `RST_ISO_EN`, the isolation watchdog and `CFG_ISO_SW_MAN_SL`.
- Also clear `PSOC_MISC_POR.PCIE_EP_ISO` (see `a2-ep-topology-addressing-isolation.md`).

**PERST and hot reset**
- PERST reaches FW as PUC interrupts 8 and 9 (`puc_intr_aggr.v`).
- Hot reset (`async_pep2psoc_hot_rst_n`) goes to the PSOC reset manager as a type-5 request. It is **masked by default**, so FW or boot must unmask it (`psoc_boot_rst_mng_top.v`).

**FLR, drain and termination**
- FLR needs `WD_CNT_CLR`.
- **Drain:** `BP_MODE` (ip.infra AXI_DRAIN) is 2 bits, reset 0.

  | Value | Behaviour |
  |---|---|
  | 0 | Disabled |
  | 1 | Timer interrupt; never terminates |
  | 2 | Terminate immediately |
  | 3 | Interrupt only (debug) |

  Offsets: CTRL 0x7C, MEM 0x78 (0xC00 through main), MNG 0x2CC.
- **Downstream (PEP) termination:** a per-function SW force bit (`PF_/VF_SW_MSTR_TERM`, `*_SW_ELBI_TERM`), plus RASDP error mode gated by `*_UNCERR_*_TERM_MASK`. **These masks reset to 1, so FW must clear them.**
- **Upstream (PIW) termination:** on SMLH link reset request, BME clear or viral, each with its own mask. A2 adds a per-channel enable.

**Not RTL-backed:** the plan's R1.3 behaviour.
