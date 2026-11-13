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

## Round-2 additions (2026-11-12, Neo r05; ip.pcie.rtl 77b45f1, ip.pcie.csr 6904415, a2.rtl 875d851)
- **RST_ISO_TIMEOUT** (PEP_CTRL_AUX 0x40, VAL[15:0], reset 0x64) counts **1 µs ticks** (threshold = VAL+1, default ≈100 µs). It bounds only the terminate window; a separate per-port watchdog bounds the whole flow (ISO_WD_CNT SEI causes). RST_ISO_CTRL @AUX 0x3c. CFG_ISO_SW_MAN_SL @PEP_CTRL+0x0 (5 b, reset 0x1F). Five FIU_ISO instances @PEP_CTRL +0xC8/+0x118/+0x168/+0x1B8/+0x208. New AR/AW_ISO_INDICATION SPI causes.
- **PERST to PSoC:** PUC soc_critical_irq 8 = rising edge (deassert), 9 = falling edge (assert). The RTL comments are inverted. CPU IRQ mapping (probably 168/169) is unverified. Each die has its own PERST; the package tie-off is unverified.
- **HW PHY CCM copy at PERST deassert:** gated by ICCM+DCCM INIT_DONE (or BOOT_FROM_ROM) and PERST high. Order: FAST_CFG RMW → ECC_EN RMW → DCCM → ICCM. PRST_SRAM_COPY_CFG @PIW_MAIN_CFG 0x10 (b0 BOOT_FROM_ROM, b1 CONT_ON_PRST_ASSERT, b2 BROADCAST_CHICKEN = repeat for PHY1). SRAM_COPY_DONE @0x2c[3:0]. Sizes per PHY: DCCM 64 KB, ICCM 128 KB. **FW must pre-load the images and set INIT_DONE before the first deassert.** HW copy latency is unspecified (20 ms warm-reset budget unproven). r05 says HW raises the PHY run request after an unaborted ICCM copy, which conflicts with r02 (FW run-req via PHY0_CPU_CTRL 0xCC b2). This is open.
- **Hot reset:** LINK_DN_CTRL @PEP_CTRL_CORE 0x50: b0 DELAY_LINK_DN_RST=0, b1 DELAY_LINK_TRAINING=1, b2 EXT_HOT_RST_IND_EN=0, [9:3] EXT_HOT_RST_IND_CNT=0x40. APP_LTSSM_EN @0x40. 4 SPI causes (SMLH/LINK_REQ_RST rise/fall). PSoC sees it as PUC critical IRQ 12, SPI 54, reset type 5. **PSoC masks hot/FLR/warm resets by default (reboot flag off); FW must unmask.** **The D2D GPIO hot-reset output is unconnected, so there is no cross-die HW propagation.**
- **FLR:** FLR_CTRL b0 PF_HANDLE_MODE (0 = HW→PSoC auto-complete, 1 = FW writes PF_DONE b2). VF path is FW-only (VF_DONE b3, NVFUNC=1). FLR_WD @0x4 reset 0x4C0000 ≈ 99.6 ms @50 MHz; WD_ENABLE reset 1, auto-completes on expiry; WD_CNT_CLR b4. SPI causes FLR_PF, FLR_PF_WD, FLR_VF, FLR_VF_WD.
- **Drain/BP_MODE:** CTRL 0x7c, MNG 0x2cc, MEM 0xc00, reset 0 (off). 1 = timer IRQ only, 2 = terminate immediately, 3 = debug. **Drain response defaults to SLVERR (0x2)**; DECERR is the default only for PEP MSTR/MNG_TERM_CFG. ELBI_TERM_CFG_RESP @AUX 0x1b4.
- **Early notify:** PEP notify fans out to 5 FIUs (ack = AND); PIW has its own notify/ack. **PSoC's pcie/pll early-notify acks are tied to 1 in a2_chip**, so PSoC does not wait. The pep_wrap TODO and app_mask_sticky_rst=0 tie-off remain.
