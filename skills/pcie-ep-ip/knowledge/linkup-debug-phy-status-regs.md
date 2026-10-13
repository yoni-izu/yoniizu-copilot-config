# PCIe Linkup Debug — PHY / MAC status registers (Iridium/a1, Element PHY)

- **Recorded:** 2026-09-07
- **Status:** Verified from generated CSR headers + FW driver (Iridium `one` ws)
- **Tags:** linkup debug, phy rx status, rx_adapt_fom, fom_done, adc gain cal, adc offset cal,
  cdr lock, dbg_sig, smlh_ltssm_state, rdlh_link_up, smlh_link_up, cpu_run_ack, ppw_ctrl,
  el_regmap_pcie6, el_rx status, dft_5 dft_6 ber, eye_scan, pep_ctrl_core, per-lane stride,
  phy_ctrl base, rx status registers, linkup indications

## Scope / Question
"Debug PCIe linkup from the PHY perspective — which registers to read for RX status?"
Companion to controller-side `LINK_CONTROL_LINK_STATUS_REG` + the wrapper "DBG_SIG".

## Key facts

**PHY is the Element in-house SerDes**, regmap `el_regmap_pcie6` (groups `EL_CM/EL_PMD/EL_RX/EL_TX`),
NOT the Synopsys raw-register set. It is memory-mapped via the per-macro `PHY_CTRL` base
(no APB indirection needed for these offsets).

### Bases (a1 / Iridium, `project_bases.h`)
- PEP core (MAC): `MNG_PCIE_EP_CLS_PEP_MNG_PEP_CTRL_0` = `0x100000000440000` (regmap `pep_ctrl`)
- PHY macro0 (lanes 0–3): `EP_PPW_0_PHY_CTRL_0` = `0x100000000480000` (regmap `el_regmap_pcie6`)
- PHY macro1 (lanes 4–7): `EP_PPW_1_PHY_CTRL_0` = `0x1000000004c0000`
- PPW_CTRL (SINGLE per die, controls BOTH macros): `EP_PPW_0_PPW_CTRL_0` = `0x100000000488000`
  (there is no `PPW_1_PPW_CTRL` — matches "one PPW_CTRL/die packs both macros").

### Per-lane stride inside a macro's PHY_CTRL space
`EL_RX_RX_<n>` lane blocks stride **0x800**: RX_0=0x0, RX_1=0x800, RX_2=0x1000, RX_3=0x1800.
(RX_0..RX_3 = the 4 lanes of that macro; macro0=phys lanes 0–3, macro1=phys lanes 4–7.)

### MAC / LTSSM (the "SIG" register FW already uses)
`PEP_CTRL_PEP_CTRL_CORE_DBG_SIG` — offset **0x44** in `pep_ctrl`, RO (SW_ACCESS 0x0).
Abs = **0x100000000440044**. This is what `dwc_ep_ctl_sv_read_ltssm()` reads
(`drivers/snps_ep_ctl/dwc_ep_ctl_sv.c`). Fields:
- `RADM_Q_NOT_EMPTY` b0, `RADM_QOVERFLOW` b1
- `RDLH_LINK_UP` b2 (DLL up)
- `SMLH_LTSSM_STATE` b[8:3] (6-bit LTSSM; codes in `dwc_ep_ctl.h` `ltssm_state_t`, L0=0x11)
- `SMLH_LTSSM_STATE_RCVRY_EQ` b9, `SMLH_LINK_UP` b10 (MAC/PHY layer up), `RADM_XFER_PENDING` b11

### PHY-side RX live status (per lane, in `el_regmap_pcie6`, all RO)
Most valuable for linkup/RX health:
- `EL_RX_RX_0_STATUS_25` off **0x260** — `RX_ADAPT_FOM_DONE` b0, `RX_ADAPT_FOM` b[24:1]
  → RX eye figure-of-merit + adaptation-complete flag. **Primary RX-quality indicator.**
- `EL_RX_RX_0_STATUS_34` off **0x1c8** — AFE calibration health:
  `ADC_OFFSET_CAL_DONE` b14, `ADC_OFFSET_CAL_ERROR` b15, `ADC_GAIN_CAL_DONE` b30,
  `ADC_GAIN_CAL_ERROR` b31 (+ offset/gain codes). Cal ERROR set ⇒ RX AFE bring-up failed.
- `EL_RX_RX_0_STATUS_2` off **0x17c** — `ADC_GAIN_CAL_FSM`, `GAIN_CODE_ACC` (gain-cal FSM state)
- `EL_RX_RX_0_STATUS_36` off **0x1cc** — `CTLE_DLEV`, `CTLE_N_MATCH`
- `EL_RX_RX_0_EYE_SCAN_0..3` off **0x23c/0x240/0x244/0x248** — eye-scan results
- `EL_RX_RX_0_DFT_5/DFT_6` off **0x120/0x124** — BER `bit_error_count` (**PRBS/loopback test mode only**,
  see `ber-cdr-counters.md`; not valid in mission mode)

**Note:** this curated RO set has **no single "CDR-locked" / "signal-detect" bit** exposed as MMIO —
`CDR_LOCK_DETECT_*` are config (RW) knobs, not a live lock readback. Closest live RX-lock proxy is
`RX_ADAPT_FOM_DONE` + a plausible `RX_ADAPT_FOM`. Signal-detect / PIPE RxValid live in PHY-FW state.

### PHY firmware health (per die, both macros)
`PPW_CTRL_CPU_IND.CPU_RUN_ACK` off **0x10** b[1:0]. Abs = **0x100000000488010**.
If the PHY µC isn't acked-running, the PHY never trains → link stuck in Detect/Polling.

## Worked absolute addresses (macro0, PHY base 0x100000000480000, stride 0x800)
| Lane | STATUS_25 (FOM) | STATUS_34 (AFE cal) |
|------|-----------------|---------------------|
| 0 | 0x100000000480260 | 0x1000000004801c8 |
| 1 | 0x100000000480a60 | 0x1000000004809c8 |
| 2 | 0x100000000481260 | 0x1000000004811c8 |
| 3 | 0x100000000481a60 | 0x1000000004819c8 |
Macro1 (lanes 4–7): same offsets on base 0x1000000004c0000 (0x4c0260, 0x4c0a60, …).

## Triage flow
1. `DBG_SIG` (0x…440044): `SMLH_LTSSM_STATE` — where is training stuck?
   Detect→no receiver; Polling→no bit lock/TS; Config/Recovery→EQ/rate issue; 0x11=L0 good.
   Check `RDLH_LINK_UP`/`SMLH_LINK_UP`.
2. `CPU_RUN_ACK` (0x…488010): PHY FW actually running?
3. Per-lane `STATUS_34`: any `ADC_*_CAL_ERROR`? cal `*_DONE` set?
4. Per-lane `STATUS_25`: `RX_ADAPT_FOM_DONE=1` and a healthy `RX_ADAPT_FOM`? Low/zero FOM ⇒ bad eye.
5. Test-mode margin only: `DFT_5/DFT_6` BER under PRBS/loopback.

## Sources
- CSR: `build_artifacts/include/prototype/{pep_ctrl,ppw_ctrl,el_regmap_pcie6}_regs.h`
- Bases: `build_artifacts/include/a1/project/project_bases.h`
- FW: `ip.pcie.sw.fw.ep` → `drivers/snps_ep_ctl/dwc_ep_ctl_sv.c` (`dwc_ep_ctl_sv_read_ltssm`),
  `ep/include/dwc_ep_ctl.h` (`ltssm_state_t`)
