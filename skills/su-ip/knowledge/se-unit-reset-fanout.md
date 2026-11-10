# SE / C2C unit reset fan-out — does it reset the PHY and drop the link?

- **Recorded:** 2026-11-04
- **Status:** Verified from RTL (Iridium `a1_chip.v` + `ip.se.rtl` iridium branch + `ip.psoc.rtl` iridium branch) + DM databook + A1 FW; A2 `main` wiring checked at chip top + `se_pcie_wrap`
- **Tags:** unit reset, c2c reset, se reset, EL_RESET_COMP_SE, reset manager, async_noc_rst_n, async_c2c_rst_n, async_dm_rst_n, async_dmp_rst_n, phy_rst_n, phy_perst_n, power_up_rst_n, button_rst_n, perst, link down, cold reset, flr, re-init, reset mask, dmp, phy, a1, a2, shared phy

## Scope / Question
"Setting the C2C unit reset — does it reset the PHY and bring the link down?" (asked for the
FW's `EL_RESET_COMP_SE_<n>` reset-manager unit, which `el_se_reinit.c` arms in the FLR mask to
re-init a down port).

## Findings

### Short answer (A1 / Iridium)
**Yes.** There is **one** reset line per C2C port and it is wired to **all three macros** of the
port — the C2C macro, the DM (DWC PCIe dual-mode) controller **and** the DM-PHY macro (`dmp`).
Asserting `EL_RESET_COMP_SE_n` is a **cold reset of the whole port**: the controller sees
`power_up_rst_n`+`button_rst_n`, drives `phy_rst_n` (PIPE/PCS) and `phy_perst_n` (PMA) into the
PHY, every controller register (sticky included) returns to default, the LTSSM restarts from
Detect, and the link partner sees a link-down (Surprise Down if it is the USP/RP). The port
needs the **full** bring-up again (PHY incl. the PHY-FW load path, DM config, C2C config/LUTs).
There is **no** C2C-only reset: the C2C CSR block has no soft-reset field, and the reset manager
has no separate DM/PHY unit for the SU ports.

### Reset chain (A1, verified)
1. **FW** — `EL_RESET_COMP_SE_0..7 = 81..88` = bit index into the reset manager's
   `RST_TYPE_<t>_UNIT_RST_EN_{0..4}` words (`fw/proj_infra/include/el_reset_components.h`).
   `el_se_die_arm_flr_reset()` sets/clears it per port with
   `el_reset_config_comp_reset_mask_die(EL_RESET_TYPE_FLR, EL_RESET_COMP_SE_0 + p, die, arm)`
   (`fw/ip/se/drivers/scale_engine/el_se_reinit.c`).
2. **PSOC reset manager** — emits one `async_unit_rst_out_n[128:0]` line per unit
   (`psoc_boot_rst_top.v`, `RST_OUT_NUM = 129` in `rst_mng_pkg.v`). The PSOC cluster renames
   the vector as `{async_comp_eng_rst_n[38:0], async_noc_rst_n[63:0], async_psoc_rst_n,
   async_pci_if_rst_n[5:0], async_pcie_rst_n[1:0], async_pll_rst_n[15:0]}`
   (`psoc_boot/misc/rst_rename.pl`), so **unit 81+n = `async_noc_rst_n[56+n]`**.
3. **Die top** (`project.iridium.rtl` → `fullchip/rtl/a1_chip.v`, branch `iridium`):
   - `se_cls` (ports 0..6): `.async_c2c_rst_n (async_noc_rst_n[62:56])`,
     `.async_dm_rst_n (async_noc_rst_n[62:56])`, `.async_dmp_rst_n (async_noc_rst_n[62:56])`,
     `.async_psoc2dm_perst_n ({7{async_psoc2pcie_perst_n}})`,
     `.async_noc_rst_n (async_noc_rst_n[22:16])` (the per-port NoC segment = units 41..47).
   - Port 7 (`c2c_cls` inside `pcie_ep_cls`): `.async_c2c_rst_n/.async_dm_rst_n/.async_dmp_rst_n`
     all = `async_noc_rst_n[63]`, `.async_noc_rst_n (async_noc_rst_n[23])`.
   - PERST is **not** part of the unit reset: `async_psoc2pcie_perst_n` is the chip-level PCIe
     PERST shared with the EP (`pcie_ep_cls.async_psoc2pep_perst_n`).
4. **`se_cls.v` / `c2c_cls.v`** (`ip.se.rtl` iridium): pure fan-out. Inside `c2c_cls`, the C2C
   macro takes `async_c2c_rst_n`; `async_dm_rst_n`, `async_dmp_rst_n` and PERST only
   **feed through** the C2C macro to `dm_macro` as `*_c2c2dm_ft` via `el_async_buf` with
   `.async_rst_n(1'b1)` (un-resettable buffers, `c2c_macro.v` "ASYNC FT" block) — the C2C reset
   cannot gate them. `c2c_macro_clk_rst.v` derives only the C2C clock-domain resets
   (`*_c2c_rlx_rst_n`, `*_c2c_sync_rst_n`, `pcil_core_rst_n`) from `async_c2c_rst_n`.
   The only non-datapath nets between C2C and DM are the reset/interrupt feed-throughs, the
   `radm_intf_*_halt` back-pressure and spares — **no `app_ltssm_enable`-type sideband**, so a
   C2C-only reset (if it existed) would not drop the link by itself.
5. **`dm_macro.v` / `dm_wrap.v`**: `async_dm_rst_n` ("Main PCIe Controller reset. Goes to
   power_up_rst_n & button_rst_n") → `el_async_buf` → `u_pcie_iip_device.power_up_rst_n`,
   `.button_rst_n`, `.test_rst_n`, `.test_phy_rst_n`; `.perst_n (async_psoc2dm_perst_n)`. The
   controller's clk/rst block outputs `phy_rst_n` / `phy_perst_n`. `async_dmp_rst_n` is a pure
   pass-through (`assign async_dmp_rst_n_dm2dmp_ft = async_dmp_rst_n_c2c2dm_ft`).
6. **`dmp_macro.v` → `dmp_wrap.v`** (PHY): `phy_rst_n` → `pipe_lane{0..3}_reset_n`,
   `pcs_scan_rst`; `phy_perst_n` → `phy_reset (~phy_perst_n)` (PMA), `phy0_presetn_i` (APB),
   `phy0_cpu_jtag_trstn_i`, `pipe_lane{0..3}_perst_n`; `async_dmp_rst_n` → `dmp_misc`
   (PHY-side CSR/APB glue, `el_async_buf` + sync).
7. **DM databook Table 2-10 "Generation of Individual Reset Signals for each Reset Event"**
   (`DWC_pcie_ctl_dm_databook.pdf` v6.42a pp.104-105): Cold Reset (power_up_rst_n) asserts
   **every** reset — `pwr_rst_n` (incl. sticky regs), `*_core_rst_n`, `*_sticky/non_sticky_rst_n`,
   `pipe_rst_n`, **`phy_rst_n` = Yes (PHY PIPE), `phy_perst_n` = Yes (PHY PMA)**. Figure 2-30
   (p.110) shows `button_rst_n`/`power_up_rst_n` → Reset Generation Module → `phy_rst_n`,
   `phy_perst_n` → PHY.

### HW default unit masks (A1 `psoc_rst_default_type_unit_pkg.v`, iridium)
SE_0..7 enable bits default to **1** for POR, WARM, PLATFORM, WD, **FLR**, HOT, HARD and **0**
for SOFT and DEBUG. The A1 FW overrides this policy: `el_enable_reset()` clears SE_0..7 for every
reset type at boot ("The SE units are kept out of every reset, so a C2C port normally survives a
FW reboot with its PHY and link"), and the SE re-arms them for **FLR only**, per the
`EL_SE_FLR_RESET_POLICY` Kconfig choice (2026-11-04: default `ALL_PORTS` = all 8 units armed at
SE init; `DOWN_PORTS` = only link-down ports, armed from the FLR ISR callback; see
`se-flr-reset-policy.md`). Preserved ports are fenced via `dwc_dm_ctl_set_preserved()` +
`el_phy_wrap_ctrl_set_preserved()`.

### A2 / Atlantic2 (`project.atlantic2.rtl` `main` → `a2_chip.v`, `ip.se.rtl` `main`)
- `async_c2c_rst_n[15:0]` is a named per-port vector; `.async_dm_rst_n` = the **same**
  `async_c2c_rst_n[7:0]` / `[15:8]` → the DM controller is still cold-reset together with its C2C.
- `async_dmp_rst_n` is **4-wide per 8 ports** (one PHY macro shared by a port pair) and driven by
  the **even** port only: `{async_c2c_rst_n[6],[4],[2],[0]}`. In `cluster/rtl/se_pcie_wrap.v`
  the shared PHY's `phy_rst_n`/`phy_perst_n` come from **dm0 only**; dm1's are
  `duplicated_unused_dm1_phy_rst_n/perst_n`.
- Consequence (needs RTL-owner confirmation before relying on it): resetting an **even** port
  resets the shared PHY → **both** links of the pair drop; resetting an **odd** port cold-resets
  its C2C + DM (its link drops) but leaves the shared PHY un-reset.

### Related
- `drop-mode-error-containment.md` — the BMC "scale-up reset" path ("configure the reset mask +
  enable c2c reset") uses this same unit reset.
- `dm-surprise-down-reporting.md` — what the partner sees when this port is reset.
- `oem-pcie-link-control.md` — the *controlled* link-down alternatives (Link Disable, LTSSM
  enable) that do **not** reset the controller/PHY.

## Sources
- RTL: `Elmnt-Internal/project.iridium.rtl` (branch `iridium`) → `fullchip/rtl/a1_chip.v`
  (`se_cls` port map: `async_c2c_rst_n`/`async_dm_rst_n`/`async_dmp_rst_n` = `async_noc_rst_n[62:56]`;
  `pcie_ep_cls` port 7 = `[63]`; `async_psoc2pcie_perst_n`).
- RTL: `Elmnt-Internal/ip.se.rtl` (branch `iridium`) → `cluster/rtl/se_cls.v`, `cluster/rtl/c2c_cls.v`,
  `c2c_macro/rtl/c2c_macro.v` ("ASYNC FT" `el_async_buf` block), `c2c_macro/rtl/c2c_macro_clk_rst.v`,
  `dm_macro/rtl/dm_macro.v` (port comment on `async_dm_rst_n`), `dm_macro/rtl/dm_wrap.v`
  (`u_pcie_iip_device.power_up_rst_n/button_rst_n/perst_n/phy_rst_n/phy_perst_n`),
  `dmp_macro/rtl/dmp_wrap.v` (`phy_reset`, `phy0_presetn_i`, `pipe_laneX_reset_n/perst_n`).
- RTL: `Elmnt-Internal/ip.se.rtl` (branch `main`, A2) → `cluster/rtl/se_cls.v` (`async_dmp_rst_n[3:0]`),
  `cluster/rtl/se_pcie_wrap.v` (`dm0_phy_rst_n` to the shared PHY, `duplicated_unused_dm1_*`).
- RTL: `Elmnt-Internal/project.atlantic2.rtl` (`main`) → `fullchip/rtl/a2_chip.v` (`se_cls` reset port map).
- RTL: `Elmnt-Internal/ip.psoc.rtl` (branch `iridium`) → `psoc_boot/rtl/psoc_boot_rst_top.v`
  (`async_unit_rst_out_n`), `psoc_boot/rtl/rst_mng_pkg.v` (`RST_OUT_NUM = 129`),
  `psoc_boot/misc/rst_rename.pl` (vector → `async_noc_rst_n[63:0]` etc.),
  `psoc_boot/rtl/psoc_rst_default_type_unit_pkg.v` (per-type default enables), `psoc_boot/misc/rst_types.txt`.
- Doc: `~/docs/C2C/DWC_pcie_ctl_dm_databook.pdf` v6.42a — Table 2-10 pp.104-105, Figure 2-30 p.110.
- FW (A1 `a1_power_on_002`): `fw/proj_infra/include/el_reset_components.h` (`EL_RESET_COMP_SE_0 = 81`),
  `fw/ip/se/drivers/scale_engine/el_se_reinit.c` (`el_se_die_arm_flr_reset`, file header),
  `fw/ip/se/drivers/scale_engine/el_se_orch.c` (`el_se_orch_phyfw_load_region`).
