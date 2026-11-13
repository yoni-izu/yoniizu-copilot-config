# Equalization Bypass to Highest Rate — SU link speed path (G1 → Gmax, not G1 → G3 → G4 → G5 → G6)

- **Recorded:** 2026-11-04
- **Status:** Verified from DM databook + DM register reference + A1 CSR model + A1 FW driver
- **Tags:** equalization, eq bypass, eq_bypass_highest_rate, no_eq_needed, pl32g_control_reg,
  pl32g_capability_reg, intermediate speeds, speed change, link training, ltssm, gen3, gen4,
  gen5, gen6, perform_eq, redo eq, fast link training, gen3_equalization_disable, target link
  speed, el_se_port_enable_eq_bypass, dwc_dm_set_eq_bypass_highest_rate, a1

## Scope / Question

"Are we bypassing equalization for intermediate speeds — G1 → G3 → G4 → G5 → G6, or G1 → G6?"

## Findings

1. **The SU link bypasses intermediate-rate EQ — by HW default, not by FW action.** The LTSSM
   trains at 2.5 GT/s, then jumps straight to the **highest common data rate** and performs
   equalization **only there**. Gen3/Gen4 (and Gen5 when the target is Gen6) are *not*
   equalized. Path = **G1 → Gmax**.
   - A1 power-on ASIC image (`zephyr_apps/mng/boards/el_arm_a1_asic.conf`): target **and**
     max link speed are capped to **Gen5** → **G1 → G5**.
   - Kconfig default (`EL_SE_PCIE_TARGET_LINK_SPEED=6`, `EL_SE_PCIE_MAX_LINK_SPEED=0` = keep
     HW): **G1 → G6**.

2. **Why — the controller's reset defaults (PL32G cap, DBI `0x1a8`/`0x1ac`, identical on USP
   and DSP, also identical on the PCIe EP controller):**
   | Register | Field | A1 CSR-model reset | Meaning |
   |---|---|---|---|
   | `PL32G_CAPABILITY_REG` (0x1a8), `__RST 0x101` | bit0 `EQ_BYPASS_HIGHEST_RATE_SUPPORT` | **1** | Port advertises the bypass (Synopsys: "Must be 1b for Ports that support 32.0 GT/s or higher") |
   | | bit1 `NO_EQ_NEEDED_SUPPORT` | **0** | "No Equalization Needed" is *not* supported → EQ **is** performed at the highest rate (not skipped altogether) |
   | | bit8 `MOD_TS_PCIE_SUPPORT` | 1 | Modified TS usage mode 0 |
   | `PL32G_CONTROL_REG` (0x1ac), `__RST 0x0` | bit0 `EQ_BYPASS_HIGHEST_RATE_DISABLE` | **0** | "When Clear, this Port indicates during Link Training that it wishes to train to the highest common link data rate and skip equalization of intermediate data rates" |
   | | bit1 `NO_EQ_NEEDED_DISABLE` | 0 | irrelevant while SUPPORT=0 |
   Both link partners of a C2C link are our DM controllers with these same defaults, so the
   bypass is always mutually negotiated.

3. **FW never writes either register.** The only writer, `dm_set_eq_bypass_highest_rate()`
   (`drivers/snps_dm_ctl/dwc_dm_ctl.c`, API `dwc_dm_set_eq_bypass_highest_rate()`), is reached
   only through `el_se_port_enable_eq_bypass()` in `drivers/scale_engine/el_se.c`, which is
   `__maybe_unused` and **absent from `el_se_init_sequence[]`** (introduced by commit
   `815d2b5` "[SW-0] se: power-on correctness fixes…", renamed in `e839cfa`). No shell/SV path
   calls it either.
   - **Driver comment is wrong:** `dwc_dm_ctl_api.h` ("the field resets to 1 (bypass
     disabled)") and `dwc_dm_ctl.c` ("1 (reset default) forces per-rate EQ") contradict both
     the CSR model (`__RST 0x0`) and the Synopsys reference ("Value After Reset: 0x0"). Calling
     the helper with `enable=true` would be a no-op; `enable=false` is the only way to get the
     per-rate G1→G3→G4→G5→G6 path.

4. **Consequence after a bypass (databook p.834):** the link can run only at **Gen1, Gen2 and
   the highest rate**. To later use Gen3/Gen4 the **DSP** must redo EQ for them:
   `PL32G_CONTROL_REG.EQ_BYPASS_HIGHEST_RATE_DISABLE=1`, `NO_EQ_NEEDED_DISABLE=1`,
   `LINK_CONTROL3_REG.PERFORM_EQ=1`, `LINK_CONTROL2.TARGET_LINK_SPEED>Gen2`,
   `LINK_CONTROL.RETRAIN_LINK=1`. **`ip/se` has no `PERFORM_EQ`/`LINK_CONTROL3` handling** —
   `set se speed <N>` + `set se retrain` and the SV speed sweeps to an intermediate rate rely on
   the controller alone (gap to keep in mind when a downshift to Gen3/Gen4 misbehaves).
   If the USP receives EQ TS2s for a rate in Recovery.RcvrCfg it uses those presets as the
   initial presets for that rate's EQ.

5. **Related knobs:**
   - `el_se_port_eq_rate_cfg` (`CONFIG_EL_SE_PCIE_EQ_RATE_CFG=y`) programs the Gen3..Gen6 rate
     shadows (`GEN3_EQ_CONTROL_OFF.GEN3_EQ_PSET_REQ_VEC`, `FOM_INC_INITIAL_EVAL`,
     `RXEQ_RGRDLESS_RXTS`); with the bypass only the highest-rate shadow is exercised at
     initial training.
   - **Palladium only:** `CONFIG_DWC_DM_FAST_LINK_TRAINING` (`default y if EL_PALLADIUM`,
     `depends on EL_PALLADIUM`) → `eq_disable()` sets
     `GEN3_RELATED_OFF.GEN3_EQUALIZATION_DISABLE` = equalization **off at every rate** + fast
     link mode + scaled timers. Not active on ASIC.

## Sources

- Historical secondary vendor source (unavailable in verified Git catalog): `DWC_pcie_ctl_dm_databook.pdf` (6.42a-lca01) p.834 (PDF index 833) — "Gen 5
  and above": EQ Bypass to Highest Data Rate + No Equalization Needed, redo-EQ procedure.
- Historical secondary vendor source (unavailable in verified Git catalog): `DWC_pcie_ctl_reference.pdf` pp.2436–2441 (PDF idx 2435–2440) —
  `PL32G_CAPABILITY_REG` (Table 3-138) / `PL32G_CONTROL_REG` (Table 3-139) field semantics and
  reset values.
- CSR model (A1 v1, `3_0_11_3p0_1389`): `build_artifacts/include/ips/se/v1/.../prototype/`
  `dm_usp_dwc_pcie_usp_regs.h` (0x1a8/0x1ac field defs), `dm_usp_dwc_pcie_usp_regs_attr.h`
  (`PL32G_CAPABILITY_REG__RST 0x101`, `PL32G_CONTROL_REG__RST 0x0`), same in
  `dm_dsp_dwc_pcie_dsp_regs_attr.h`; EP: `ips/pcie/v1/.../dwc_pcie_usp_regs_attr.h` same values.
- FW (`ip.se.sw.fw`, A1 power-on): `drivers/scale_engine/el_se.c` (`el_se_port_enable_eq_bypass`
  `__maybe_unused`, `el_se_init_sequence[]`), `drivers/snps_dm_ctl/dwc_dm_ctl.c`
  (`dm_set_eq_bypass_highest_rate`, `eq_disable`, `dm_fast_link_training_cfg`),
  `include/dwc_dm_ctl_api.h`, `drivers/scale_engine/Kconfig`, `drivers/snps_dm_ctl/Kconfig`,
  `zephyr_apps/mng/boards/el_arm_a1_asic.conf` (Gen5 target + max).
