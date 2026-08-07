# PHY loopback modes + BERT/PRBS enablement (register sequences, preconditions, quirks)

- **Recorded:** 2026-08-05
- **Status:** Verified from doc (PHY databook) + cross-checked against Element regmap header
- **Tags:** loopback, near-end serial, far-end parallel, near-end parallel, rx_to_tx_lpbk_en,
  tx_to_rx_lpbk_en, afe_lpbk_en, prbs, bert, pattern generator, pattern matcher, patgen_en,
  pattern_sel, ber_en, ber_ref_timer, bit_error_count, read_mode_en, dft, tst_cfg, tx dcc recal,
  p1 p2, ate override, ep phy, scale up, se, c2c

## Scope / Question
What loopback modes does the DWC PCIe6 PHY support, and what register sequence / preconditions /
quirks apply to enabling them plus the built-in BERT PRBS generator+checker? (For an EP+SU
shared PHY loopback/PRBS FW API.)

## Findings

### Same PHY IP serves EP and scale-up
EP PHYs (`pcie_phy0/1`) and SE/C2C scale-up PHYs (`se_phy_p0..p7`) are all the **same** Synopsys
`dwc_pcie6phy_g2_tsmc5ff_x4ns` x4 macro, all bound to compatible `snps,pcie6-phy` (driver
`snps_pcie6_phy`). So one op added to the vendor-agnostic `el_pcie_phy` API + the driver serves
both automatically — the consumer just supplies the right `struct device *` (from
`dwc_pcie6_phy_get_devices()` or the SE/C2C node's `phy-dev` phandle). See `phy-instance-count.md`.

### Three on-die loopback modes (databook §5.1.1 p.94-96, §5.2.1 p.114-116). Independent per-lane; only ONE mode per lane at a time.
1. **Near-End Serial Loopback** ("TX-to-RX serial / pre-driver", §5.1.1.1 / §5.2.1.1). TX serial
   output → RX serial input, same lane, **max 32 Gbps NRZ**. Loop point is *after* TX pre-driver,
   *before* TX driver — exercises all enabled PCS+PMA TX/RX blocks. Enable via analog AFE override
   block: `RXS::ana_ovrdval_2` + `ana_ovrden_0` (`afe_lpbk_en=1`, `afe_main_en=0`, `lb_mux_ctrl=1`,
   `ibias_lb_en=1`, `ibias_lb_cont=0b100`, `vcm_opamp_en=0`), `TXS::ana_ovrdval_22` + `ana_ovrden_3`
   (`scope_lb_en=1`, `drv_x1_sw_lb_en=1`, `drv_unit_en=1`, `drv_sw_lb_en=0`, `lb_att_code=0b1000`),
   `TXS::tst_cfg_20` (`tx_data_lb_msb2lsb_en=1`, `tx_data_lb_invert_msb=0`). **64-bit mode NOT
   supported.** When serial LB is on, no external signal may drive RX (internal+external paths
   interfere).
2. **Far-End Parallel Loopback** ("RX-to-TX parallel", §5.1.1.2 / §5.2.1.2). RX SERDES parallel
   output → TX SERDES parallel input, same lane (received recovered data retransmitted). Enable:
   `PMD_LANE[N]::LANE_CFG[2]::rx_to_tx_lpbk_en=1`, assert `txN_data_en_i`+`rxN_data_en_i`,
   **`txN_rate_i=0`** (TX uses post-divided RX clock). Steps: enable lane PLL → RXS → TXS (TXS/RXS
   same symbol width). **Must be enabled/disabled ONLY in P1 or P2.** Refclk must come from the
   SAME source as the far-end transmitter's refclk; nonzero-ppm needs prior clock forwarding
   ("PMD Clock Forwarding" p.45). **TX DCC recal quirk:** on entry FW re-calibrates TX DCC
   (~**100 µs**, LB data valid only after); on exit FW restores prior DCC code (~**50 µs**, no new
   PHY requests until elapsed).
3. **Near-End Parallel Loopback** (§5.1.1.3). TX parallel input clk/data at PMD input →
   RX parallel output. Enable: `PMD_LANE[N]::PMD_LN_OVRDEN[0]::ovrd_en_lnX_tx_to_rx_lpbk_en_i=1`,
   `PMD_LANE[N]::PMD_LN_OVRDVAL[3]::lnX_tx_to_rx_lpbk_en_i=1`, plus `RXS[N]::PIN_OVRDEN[0]` sigdet
   overrides. **Must be enabled/disabled ONLY in P1 or P2.**

Also supported: **external (off-die) serial loopback**.

### Common loopback preconditions (databook Table 5-13, p.114-115)
`phy_reset_i` asserted then de-asserted; refclk supplied; MPLLA/B set to target baud rate; per-lane
`txN_reset_i`/`rxN_reset_i` de-asserted; `txN_pstate_i`/`rxN_pstate_i` = P0; rate/width set to baud.

### BERT PRBS generator + matcher (databook §5.2.2 p.116-118)
Two independently-programmed modules per lane (gen and matcher controls are independent).
- **Generator:** `TXS[N]::TST_CFG[0]::patgen_en=1` and `pattern_sel[4:0]` (5-bit): LFSR7=0,
  LFSR15=0x4, LFSR23=0x5, LFSR31=0x6, PCIe-LFSR23=0x9, 52UI-jitter=0x19, custom=0x16
  (`TXS_DAC_TEST_PATTERN`), data-linearity=0x18. `TST_CFG[0]::gen_err=1` inserts a single LSB error.
  To use PRBS gen as serializer source: `patgen_en=1` **and** `LANE_CFG[2]::rx_to_tx_lpbk_en=0`.
- **Matcher:** `RXS[N]::DFT[1]::pattern_sel[3:0]` (**4-bit — different encoding from TX!**):
  LFSR7=0, LFSR15=0x4, LFSR23=0x5, LFSR31=0x6, 52UI=0xB, 256b-custom=0xC, PCIe-LFSR23=0xF.
  Sequence: power RX to P0 (rate/width) & send pattern → assert `rxN_data_en_i`, wait `rxN_valid_o`
  → set `DFT[3:2]::ber_ref_timer[39:0]` (data_clk cycles; each = 128 bits; **0 = free-run** until
  `ber_en` cleared) → set `DFT[1]::ber_en=1` (**toggle twice** for reliable sync) → assert
  `DFT[1]::read_mode_en` then read status: `DFT[3]::ber_seed_verif_status` (locked),
  `DFT[6,20]::ber_ref_timer_snap[39:0]` (cycles left), `DFT[3]::ber_en_int` (still counting),
  and result `DFT[6:5]::bit_error_count[39:0]` (40-bit, saturates `0xFF_FFFF_FFFF`).
  BER = bit_error_count / (128 × ber_ref_timer).
- **Loopback caveat (p.114 note):** when the BERT generator is used in internal/external cable
  loopback, apply the ATE Testbench App-Note Appendix A "ATE Override Control Pin List" settings,
  otherwise the local Protocol PCS triggers RX Data Recovery and **disrupts the RX serial data seen
  by the BERT checker** (mission-mode PCS and BERT checker are mutually exclusive). See
  `ber-cdr-counters.md`.

### Register fields all present in Element regmap (`el_regmap_pcie6_regs.h`, per-lane instances)
`EL_RX_RX_{0..}_DFT_1__{BER_EN,READ_MODE_EN,PATTERN_SEL,BER_SEED_VERIF_BYPASS_EN}`,
`DFT_2/3__BER_REF_TIMER`, `DFT_3__{BER_SEED_VERIF_STATUS,BER_EN_INT}`, `DFT_5/6__BIT_ERROR_COUNT`,
`DFT_6/20__BER_REF_TIMER_SNAP`, `EL_PMD_PMD_LANE_*__…RX_TO_TX_LPBK_EN` (LANE_CFG2),
`PMD_LN_OVRDEN0__OVRD_EN_LNX_TX_TO_RX_LPBK_EN_I`, `PMD_LN_OVRDVAL3__LNX_TX_TO_RX_LPBK_EN_I`,
`EL_RX_RX_VP_*_ANA_OVRDEN_0__OVRD_EN_ANA_RX_ANA_AFE_LPBK_EN` / `ANA_OVRDVAL_8__ANA_RX_ANA_AFE_LPBK_EN`,
`EL_TX_*_TST_CFG0__PATGEN_EN`. (Note the SNPS SDK header uses a parallel `RX__DFT1__…`,
`TX__TST_CFG0__…`, `PMD_LANE__LANE_CFG2__…` naming; the driver uses the `EL_REGMAP_PCIE6_*` names.)
There is also a separate per-lane **compare-pattern** BERT (`PMD_LANE::CP_PM_*`,
`TX_COMP_PAT_CFG0::reg_cp_patgen_en`) — distinct from the RX-DFT matcher described above.

## Sources
- Doc: `phy/dwc_pcie6phy_g2_tsmc5ff_x4ns_databook.pdf`
  §5.1.1 PMD Loopback Modes p.94-96 (Fig 5-1, near-end serial reg list, far-end/near-end parallel),
  §5.2.1 Loopback Functions p.114-116 (Table 5-13 preconditions, Fig 5-17/5-18, TX DCC recal),
  §5.2.2 BERT p.116-118 (Table 5-14 gen patterns, Table 5-15 matcher patterns, matcher sequence)
- Regmap: `fw/build_artifacts/include/prototype/el_regmap_pcie6_regs.h` (field macros above)
- Related: `ber-cdr-counters.md` (matcher is test-mode-only), `phy-instance-count.md` (EP+SU share IP)
