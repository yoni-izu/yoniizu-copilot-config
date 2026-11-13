# Lane Repair vs. Width Degrade — what the AW IP offers and what the FW does with it

- **Recorded:** 2026-11-04
- **Status:** Verified from UCIe 1.1 + AW MMR + internal spec + FW (ip.d2d.sw.fw `main` @2bf9a03 and
  `a1_power_on_branch` @5de03f0, both 2026-11-01). No hardware validation.
- **Tags:** lane repair, repair, redundant lane, width degrade, x8, x16, standard package, advanced
  package, mbinit.repairmb, mbinit.repairclk, mbinit.repairval, mbtrain.repair, phyretrain, runtime
  link test control, 0x100, mb_data_repair, clk_trk_vld_sb_repair, width_deg, err_log0, lane_rev,
  mmpl_cfg, tx_lane_map, no_degrade, ltsm-state-mask, scratch_reg5, 0x1cdff07, speed degrade,
  cmn-top-speed-degrade, trainerror, retrain, lane map table 4-9

## Scope / Question
"AW IP supports lane repair — do we have any mechanism in place to apply it?"

## Findings

### 1. Lane repair is an *Advanced Package* feature; Element D2D is *Standard Package*
- UCIe 1.1 §4.3: repair = remapping faulty lanes onto redundant bumps. Resources exist **only in the
  Advanced Package** bump map: 2 redundant data lanes per 32-lane group (4 per x64), 1 redundant
  clock/track pair, 1 redundant valid, 1 redundant sideband clk+data pair. "For the Standard Package
  option, N=16 (x16) and **no extra pins for repair are provided**" (UCIe txt L1453-1455).
- Standard Package resilience is **width degrade** (UCIe §4.3.7, L4893-4899): the x16 module runs as
  **x8** on logical lanes 0-7 *or* 8-15, excluding the faulty half; disabled TX go Hi-Z, RX disabled.
  Lane-map encodings (Table 4-9): `00b` degrade not possible, `01b` lanes 0-7, `10b` lanes 8-15,
  `11b` all 16.
- Element's endpoints are **2 × x16 modules** (AW "1xS32" = Standard, 32 lanes; FW
  `D2D_EML_EMR_LANE_COUNT 16u`, `active-modules` default 0x3). Internal spec RAS section (P1238-1243):
  "**Standard package: No support for repair lanes; Mainband speed and width degradation.**
  Advanced package: Mainband lane repair; Sideband redundant data+clock." Yield section P329:
  no pin redundancy for standard package (C4 bump yield is high), only the "degraded" half-module
  mode. Spec "Opens" P1312 still lists *"Does the lane repair imply moving the LTSM into
  PHYRETRAIN?"* as an unanswered vendor question.
- → **Lane repair proper is not applicable to our silicon.** The only equivalent is x16→x8 width
  degrade (plus speed degrade as the fallback).

### 2. Where repair / degrade happens in the UCIe LTSM (executed by the AW MCU "LTSM" FW)
- **MBINIT.REPAIRCLK / REPAIRVAL** — clock/track and valid repair (Advanced only).
- **MBINIT.REPAIRMB** (UCIe §4.5.3.3.6, L5943-6025) — Advanced: per-lane "Per Lane ID" test at
  lowest rate, apply repair, re-verify; Standard: same functional check, then
  `{apply degrade req}` with a Table 4-9 lane map; both sides re-run the check if width changed;
  `00b` → TRAINERROR.
- **MBTRAIN.REPAIR** (§4.5.3.4.13, L6460-6500) — entered from MBTRAIN.LINKSPEED or PHYRETRAIN.
  Advanced: apply repair; Standard: width degrade to x8 if all failing lanes sit in one half.
  LINKSPEED rule (L6365-6382): if lanes can be repaired/degraded → REPAIR, else → **speed degrade**.
- So any repair/degrade is negotiated by the two PHY MCUs over sideband; host SW can only
  (a) let the LTSM run those states, (b) request a runtime pass via the register below + Retrain.

### 3. AW GammaCORE32 registers (PHY_D2D block == UCIe PHY register block, UCIe offset − 0x1000)
| AW register (`aw_phy_d2d_reg_regs.h`) | Off | UCIe | Content |
|---|---|---|---|
| `REG_PHY_D2D_RUNTIME_LINK_TEST_CNTRL_REG1` | 0x100 | 7.5.3.36 @0x1100 | b0 `valid_repair`, b1 `clk_repair` (Adv only, no effect on Std), b2-5 `m0..m3_lane_repair`, b6 `start` ("write before hitting Link Retrain"), b7 `inj_stuck_at_fault`, [14:8] `m0_lane_repair_id`, [21:15] m1, [28:22] m2, [31:29]+REG2[3:0] m3. **"For Standard Package this bit will trigger a width degrade for logical Module N, if possible and relevant."** (MMR p211-215) |
| `REG_PHY_D2D_RUNTIME_LINK_TEST_CNTRL_REG2` | 0x104 | | m3 repair id tail |
| `REG_PHY_D2D_RUNTIME_LINK_STATUS_REG2` | 0x108 | 7.5.3.37 | b0 `busy` (set on Start, cleared when HW attempted the request) |
| `REG_PHY_D2D_M{n}_MB_DATA_REPAIR_REG1/2` | 0x10C/0x110 (+module stride) | 7.5.3.38 @0x110C | RO repair address per TRD_P[0..3]/RRD_P[0..3]: `FFh` no repair, `F0h` unrepairable, else repaired TD_P/RD_P lane |
| `REG_PHY_D2D_M{n}_CLK_TRK_VLD_SB_REPAIR` | 0x134 | 7.5.3.39 @0x1134 | RO clk/track/valid/SB repair addresses |
| `REG_PHY_D2D_M{n}_ERR_LOG0` | 0x80 | 7.5.3.34 | [7:0] `state_n` (LTSM state), b8 `lane_rev`, **b9 `width_deg` "1b = module width degraded, Standard package only"**, previous states |
| `REG_PHY_D2D_M{n}_TRAIN_SETUP4` | 0x50 | 7.5.3.29 | [3:0] `repair_lane_mask` (mask redundant lanes in RX compare) |
| imp-spec `reg_phy_tx_mmpl_cfg` | 0x3A8 | — | [1:0]/[3:2]/[5:4]/[7:6] `tx_lane_map_m0..3` for width degrade: `01` lower half, `10` upper half, `11` all (reset 0x003900FF → all), b8 `tx_derate_en` (quarter lanes), [12:9] `tx_mod_en` (MMR p688) |
| imp-spec `..._ERR_LOG0_W` etc. | | — | RW shadows incl. `m{n}_width_deg_w` (MMR p~14068) |
| `REG_GLBL_CMN_TOP_INT1` | | — | b5 `speed_degrade`, b6 `speed_change` (1-bit W1C events). **No width-degrade interrupt**; width is read from `ERR_LOG0.width_deg` or DVSEC1 `UCIE_LINK_STATUS` link-width field |

### 4. What the Element FW does today (ip.d2d.sw.fw, 2026-11-01)
- **Nothing touches the repair/degrade registers.** `grep` of `drivers/` + `include/` for
  `RUNTIME_LINK_TEST_CNTRL`, `MB_DATA_REPAIR`, `CLK_TRK_VLD_SB_REPAIR`, `WIDTH_DEG`, `mmpl_cfg`,
  `lane_map`: zero hits. `ERR_LOG0_W` is read only for `STATE_N/N1/N2` (`aw_d2d.c:1935-2002`,
  `el_d2d_wrapper.c:709-742`, `d2d_shell.c:478-481`); `lane_rev`/`width_deg` are masked out.
  The generated header `build_artifacts/include/v1/prototype/aw_phy_d2d_reg_regs.h` already carries
  every symbol (`..._RUNTIME_LINK_TEST_CNTRL_REG1 0x100`, `__M0_LANE_REPAIR_MSK 0x4`, `__START_MSK
  0x40`, `__M0_LANE_REPAIR_ID_MSK 0x7f00`, `M0_MB_DATA_REPAIR_REG1 0x10c`, `M0_ERR_LOG0__M0_WIDTH_DEG_MSK
  0x200`, …), so an implementation needs no CSR work.
- **The repair states are not even run.** `ltsm-state-mask` (= "states the MCU firmware runs",
  binding `el,d2d-manager.yaml`) is written to `CMN_MCU_SCRATCH_REG5` by
  `el_d2d_mgr_step_ltsm_set_state_mask()` (`el_d2d_manager.c:2358`; `aw_d2d.c:1804-1818`). Production
  A1 uses `EL_AW_D2D_LTSM_STATE_MASK_DEFAULT` (`proj_infra/soc/dts/a1/a1_d2d_ltsm_vfields.dtsi:25`)
  = `EL_AW_D2D_LTSM_STATE_MASK_MB` = **0x1CDFF07**, the AlphaWave reference value
  (`dts/el-d2d-dt.h:96-112`). Bits **not** set → skipped: `MBINIT.CAL(3)`, **`MBINIT.REPAIRCLK(4)`,
  `MBINIT.REPAIRVAL(5)`**, `MBINIT.REVERSALMB(6)`, **`MBINIT.REPAIRMB(7)`**, `MBTRAIN.RXDESKEW(0x11)`,
  **`MBTRAIN.REPAIR(0x14)`**, `PHYRETRAIN(0x15)` (dropped on AW advice with FW 1015), `L1_L2(0x19)`.
  The SB-only path uses `EL_AW_D2D_LTSM_STATE_MASK_SB` (`el_d2d_manager.c:2225`).
- **Degrade is explicitly forbidden.** `Cfg_Data.NO_DEGRADE` ("1 prevents width/speed degrade",
  `include/aw_d2d_cfg_data.h:46`, AresCore `cfg_data.hpp` FW 0A76FEB5) is set to **1** in production
  DT (`a1_d2d_ltsm_cfg.dtsi:55 no-degrade = <0x1>`) and additionally **forced to 1** whenever
  `datarate-mbps != 0` (dynamic-rate path, `el_d2d_manager.c:1627-1632`).
- **Net behaviour on a faulty lane:** no repair, no width degrade, no speed degrade → the LTSM lands in
  `TRAINERROR`; with `sticky-train-error = <0x0>` (`a1_d2d_ltsm_vfields.dtsi:30`) the MCU FW resets
  and re-trains on its own, and the manager merely counts the retrain
  (`el_d2d_mgr_count_retrains`, `el_d2d_manager.c:1955-1968, 2062-2101`). A link that never trains
  simply never reaches `ST_ACTIVE` within the bounded poll.
- **Runtime telemetry:** `cmn-top-speed-degrade` → `LOG | COUNT`, severity WRN, no RAS notify
  (`el_d2d_sei_tree.c:455-457, 484-486`); `cmn-top-speed-change` → LOG/INFO. The `handle_bw_change`
  sub-handler described in `docs/d2d-sei-interrupts.md` (reads DVSEC1 `UCIE_LINK_CAP`/`UCIE_LINK_STATUS`
  speed+width) **does not exist in code** — the doc is ahead of/behind the implementation. The health
  baseline table `d2d_health_chks[]` (`el_d2d_manager.c:370-404`) has no width or repair check.
  Only the SV shell/tests decode `LSTATUS_LINK_WIDTH_EN` for display (`el_d2d_shell_sv.c:559-689`,
  `tests/test_sv.c:1936-2011`).

### 5. What "applying it" would take (if ever wanted)
1. **Training-time width degrade (LTSM-driven):** set `no-degrade = <0>` *and* add
   `EL_AW_D2D_ST_MBINIT_REPAIRMB | EL_AW_D2D_ST_MBTRAIN_REPAIR` to `ltsm-state-mask`; confirm with
   AlphaWave that the Eland LTSM FW implements the Standard-Package degrade handshake in those states
   (the reference mask skips them, and P1312 is still open). Dynamic-rate mode (`datarate-mbps != 0`)
   forces `NO_DEGRADE=1`, so it is incompatible.
2. **SW-initiated runtime degrade:** write `RUNTIME_LINK_TEST_CNTRL_REG1` (`m{n}_lane_repair` [+
   `m{n}_lane_repair_id`], then `start`), set the DVSEC1 UCIe Link Control **Retrain** bit, poll
   `RUNTIME_LINK_STATUS_REG2.busy`, then read `ERR_LOG0.width_deg` / `UCIE_LINK_STATUS` width.
   Requires PHYRETRAIN/MBTRAIN.REPAIR to be in the state mask.
3. **Observability first (cheap, no behaviour change):** decode `width_deg`/`lane_rev` from the
   `ERR_LOG0_W` reads already performed, add a `WIDTH` row to `d2d_health_chks[]`, and implement the
   documented `handle_bw_change` for `speed_degrade`/`speed_change`.
4. **System question before any of the above:** a half-width (x8) or speed-degraded link changes
   per-link bandwidth; the NoC/CCE routing and the vertical-vs-horizontal bandwidth asymmetry
   (`iridium-d2d-instance-counts.md`) must tolerate it — Architecture decision, not a driver one.

### 6. A2 (Atlantic2) — nothing changes on this topic (verified 2026-11-04)
- **Same package class at the bump level.** `ip.d2d.rtl` `main` (Atlantic) instantiates the **N3**
  AW subsystem `eland_gammaN64B1Tn3_ss_e_subsystem_mod` (PHY wrap `aresSN36n3_f_ucie_phy_wrap`;
  emulation side macro `eland_gammaE64B1Tn3_ss_d_…_em`). Its bump interface is **2 modules
  (`bu_m0_`, `bu_m1_`) × `txio0..15`/`rxio0..15`** + `ttrk/tvld/txcksb/txdatasb/wdqs_t/c` (and RX
  mirrors) — **no `trd/rrd/rdck/rdvld/rdsb` redundant bumps** (grep count 0). The A1 frozen branch
  `origin/iridium` has the identical inventory with the **N5** parts (`eland_gammaN64B1Tn5_ss_a`,
  PHY `aresSE36n5_a_ucie_phy_wrap`). The "S" in `aresS…` = Standard package. So A2 D2D is still
  **UCIe Standard Package, x16 per module, 2 modules** → lane repair physically impossible, width
  degrade (x16→x8) remains the only lane-fault mechanism, speed degrade the fallback.
- **A2 overview / architect:** "12× UCIe 2.0 Standard Package, 768 GB/s"; architect Maxim Leshin
  (2026-08-30): D2D kept **as-is**, FW updated only if the new AW IP needs a different config
  (`atlantic2-prj/knowledge/a2-d2d.md`). UCIe **2.0** does not add redundant lanes to the Standard
  Package module — and in any case the N3 bump map above is the physical truth. (Only the UCIe 1.1
  text is held locally; UCIe 2.0 not re-verified from the spec itself.)
- **Registers:** generated A2 (`v2/0_0_0_0p0_0000`) `aw_phy_d2d_reg_regs.h` is `#define`-identical to
  the A1 `v1` header (incl. `RUNTIME_LINK_TEST_CNTRL_REG1`, `MB_DATA_REPAIR`, `WIDTH_DEG`). The later
  A2 drop `v2/0_8_4_0p8_238` in the a1_power_on artifacts does not carry AW headers at all (92
  files, no `aw_*`/d2d). No N3 AW datasheet/MMR was verified in the approved Git catalog; vendor-source gap.
- **FW policy for A2 is not yet defined:** `proj_infra/soc/dts/a2/` has **no D2D LTSM cfg/vfields
  files** (A1's `a1_d2d_ltsm_cfg.dtsi` / `a1_d2d_ltsm_vfields.dtsi` have no a2 counterpart). When
  they are created, they will inherit `no-degrade = <1>` and the reference state mask unless the
  degrade policy is decided deliberately. Open item to settle with the N3 AW drop: does the N3 Eland
  LTSM FW's reference `LTSM_SKIP_STATE` still omit `MBINIT.REPAIRMB`/`MBTRAIN.REPAIR`, and does it
  implement the Standard-Package `apply degrade` handshake if enabled.

## Sources
- RTL A2: `~/projects/iridium/rtl/ip/d2d` `main` @8fe3b95 — `d2d/rtl/eland_gammaN64B1Tn3_ss_e_subsystem_mod.v`
  (ports L46-…, 229 ports), `d2d_mid_phy_macro.v`, `d2d_side_phy_macro_emul.v:427,518`.
  RTL A1: `origin/iridium:d2d/rtl/eland_gammaE64B1Tn5_ss_a_subsystem_mod.v`.
- A2 FW headers: `~/projects/iridium/a1_power_on/fw/build_artifacts/include/v2/0_0_0_0p0_0000/prototype/aw_phy_d2d_reg_regs.h`
  (diff vs `v1/prototype/…` empty); `v2/0_8_4_0p8_238/prototype/` (no AW headers).
- A2 project context: `~/.copilot/skills/atlantic2-prj/knowledge/a2-d2d.md`, `a2-overview.md:38`.
- Historical secondary UCIe 1.1 extraction (exact approved Git original unavailable): L1444-1458 (package repair pins), §4.3 L4212-4264, §4.3.7
  L4893-4899, MBINIT.REPAIRMB L5943-6025 + Table 4-9 L6020-6024, LINKSPEED L6365-6382,
  MBTRAIN.REPAIR L6460-6500, PHY register TOC L355-367, 7.5.3.36 L12851+.
- AW MMR (`Eland_gammaN64B1Tn5_ss_a_mmr_100_053_Registers_Descriptons.txt`/`.pdf`): p187 (TRAIN_SETUP4),
  p199 (ERR_LOG0 width_deg), p211-216 (RUNTIME_LINK_TEST_CNTRL_REG1/2, LINK_STATUS_REG2,
  MB_DATA_REPAIR), §5.52-5.55 (CLK_TRK_VLD_SB_REPAIR), p688 (reg_phy_tx_mmpl_cfg), §7.51-7.62 `_w` shadows.
- Internal spec `D2D Specification Rev 2.0.0.docx`: P227, P327-330 (Yield), P332-344 (Redundancy
  Re-Mapping), P1236-1243 (RAS features), P1312 (Opens), Table 2 row 7 (Repair: N/A for Standard).
- FW `ip.d2d.sw.fw` (`~/projects/iridium/one/fw/ip/d2d`, main @2bf9a03): `include/aw_d2d_cfg_data.h:46`,
  `dts/el-d2d-dt.h:38-119`, `dts/bindings/el,d2d-manager.yaml` (`no-degrade`, `ltsm-state-mask`,
  `sticky-train-error`), `drivers/fw/el_d2d_manager.c:370-404, 1580-1640, 1955-1968, 2062-2101, 2225,
  2358, 2673`, `drivers/fw/aw_d2d.c:1804-1818, 1911-2002`, `drivers/fw/el_d2d_sei_tree.c:455-500`,
  `drivers/fw/el_d2d_wrapper.c:677-742`, `docs/d2d-sei-interrupts.md:178-197`; DTS
  `proj_infra/soc/dts/a1/a1_d2d_ltsm_cfg.dtsi:54-55`, `a1_d2d_ltsm_vfields.dtsi:25,30`; generated header
  `~/projects/iridium/a1_power_on/fw/build_artifacts/include/v1/prototype/aw_phy_d2d_reg_regs.h:346-893`.
- RTL `ip.d2d.rtl` main (`~/projects/iridium/rtl/ip/d2d`): no "repair" logic in the Element wrapper;
  the AW subsystem (`eland_gammaN64B1Tn3_ss_*_subsystem_mod.v`) owns it.
