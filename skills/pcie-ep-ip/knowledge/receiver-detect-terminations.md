# PCIe "PHY doesn't see terminations" — receiver detection (LTSSM Detect) debug

- **Recorded:** 2026-09-16
- **Status:** Verified from generated CSR headers + FW driver (Iridium `four` ws)
- **Tags:** receiver detection, terminations, ltssm detect, rx termination, term_en,
  ana_afe_term_ctrl_th, sigdet, sigdet_lf_out, idle_detect, fw_rx_valid, pmd_lane_stat,
  cpu_run_ack, dbg_sig, smlh_ltssm_state, rx detect, rxdet, impedance cal, no receiver,
  stuck in detect, linkup debug, el_regmap_pcie6

## Scope / Question
"My PCIe PHY doesn't see terminations — what info can I pull from the PHY?"
Companion to `linkup-debug-phy-status-regs.md` (RX-quality registers) and `ber-cdr-counters.md`.

## Key facts

**"Seeing terminations" = LTSSM Detect / receiver detection.** The TX drives the lane and senses
the RC time-constant of the far-end RX 50 Ω termination. No detection ⇒ link never leaves
**Detect**. Two directions with different root causes:
- EP **TX can't detect host/tester RX** → far end unpowered / not terminated / TX-detect cfg wrong.
- Host/tester **can't see EP RX termination** → EP RX termination not enabled or impedance-cal
  never completed.

**There is NO single "receiver-detected" MMIO status bit** in the Element in-house SerDes regmap
`el_regmap_pcie6`. The receiver-detect verdict is a **PIPE/MAC** result → inferred from the MAC's
LTSSM state; PHY registers only *corroborate*. (Same limitation noted for CDR-lock/sig-detect in
`linkup-debug-phy-status-regs.md`.)

## What to pull (triage order, a1/Iridium abs addresses)

1. **MAC/LTSSM — really stuck in Detect?** `PEP_CTRL_CORE_DBG_SIG` @ **0x100000000440044** (RO):
   `SMLH_LTSSM_STATE` b[8:3], `RDLH_LINK_UP` b2, `SMLH_LINK_UP` b10. Detect ⇒ no receiver sensed.
   (This is what `dwc_ep_ctl_sv_read_ltssm()` reads.)
2. **PHY FW alive?** `PPW_CTRL_CPU_IND.CPU_RUN_ACK` @ **0x100000000488010** b[1:0]. If the PHY µC
   isn't running the detect sequence never executes.
3. **Per-lane PHY RX AFE** (regmap `el_regmap_pcie6`; macro0 base `0x100000000480000`, macro1
   `0x1000000004c0000`; `EL_RX` lane stride **0x800**):
   - `EL_RX_RX_n_STATUS_34` off **0x1c8** — `ADC_OFFSET/GAIN_CAL_DONE/ERROR`; ERROR ⇒ AFE bring-up
     failed.
   - `EL_RX_RX_n_STATUS_25` off **0x260** — `RX_ADAPT_FOM_DONE`/`RX_ADAPT_FOM` (only past Detect).
   - Analog **signal-detect** `RX*_ANA_AFE_SIGDET_LF_OUT_O` and `IDLE_DETECT_0` (0x98) exposed only
     via the `PIN_OVRDEN/OVRDVAL` override read path — no standalone live status bit.
4. **RX termination state** (key when far end can't see EP RX), per-lane `el_regmap_pcie6`:
   - `EL_RX_RX_n_PIN_OVRDEN_3 / PIN_OVRDVAL_0 :: *_RX0_TERM_EN_I` — verify RX term-enable not
     force-OFF by a stale override.
   - `ANA_AFE_TERM_CTRL_TH` (RX_VP `ANA_OVRDVAL_10`, msk 0x3fff8000) + `ANA_AFE_TERM_RST` —
     termination impedance-cal threshold/reset; confirms 50 Ω cal ran.
5. **Per-lane PHY-FW RxValid** (regmap `EL_PMD`; `PMD_LANE_n` stride **0x800**, lane0 base 0x3800):
   - `EL_PMD_PMD_LANE_n_FW_CSR_0 :: FW_RX_VALID` (lane0 off **0x3c60**, bit 25).
   - `EL_PMD_PMD_PMD_LANE_STAT` off **0x34ac** — `PMD_LANE_CNT` b[2:0] (active lanes).

**Not useful here:** `DFT_5/DFT_6` BER = PRBS/loopback test-mode only (see `ber-cdr-counters.md`).

## Bottom line
Definitive receiver-seen signal = **LTSSM state in `DBG_SIG`** (Detect = no). From the PHY, pull
`CPU_RUN_ACK`, per-lane `STATUS_34` (AFE cal errors), `TERM_EN_I` override + `ANA_AFE_TERM_CTRL_TH`
(own RX termination enabled/cal'd), and `FW_RX_VALID` — these corroborate; no direct
receiver-detect register exists.

## Sources
- CSR: `sim/build_artifacts/include/prototype/el_regmap_pcie6_regs.h`
  (`EL_RX_RX_n_STATUS_25/34`, `PIN_OVRDEN_3/OVRDVAL_0 *_TERM_EN_I`, `ANA_AFE_TERM_CTRL_TH`,
  `SIGDET_LF_OUT_O`, `IDLE_DETECT_0`, `EL_PMD_PMD_LANE_n_FW_CSR_0.FW_RX_VALID`, `PMD_LANE_STAT`)
- CSR: `pep_ctrl_regs.h` (`PEP_CTRL_CORE_DBG_SIG`), `ppw_ctrl_regs.h` (`CPU_IND.CPU_RUN_ACK`)
- Bases: `build_artifacts/include/a1/project/project_bases.h`
- FW: `ip.pcie.sw.fw.ep` → `drivers/snps_ep_ctl/dwc_ep_ctl_sv.c` (`dwc_ep_ctl_sv_read_ltssm`)
- Related KB: `linkup-debug-phy-status-regs.md`, `ber-cdr-counters.md`
