# EP PHY IDDQ / test_powerdown control (per-die)

- **Recorded:** 2026-06-24
- **Status:** Verified from RTL + generated register headers
- **Tags:** iddq, test_powerdown, ppw_ctrl, phy_ctrl, reset default, die2, die3, firmware ownership

## Scope / Question
Whether EP PHY IDDQ behavior for higher dies is already implemented, what register controls it, defaults, and whether FW should set it.

## Findings
- The EP PHY test/IDDQ-like control is exposed as `TEST_POWERDOWN` in PPW control register `PHY_CTRL`.
  - Field mask/shift: `PPW_CTRL_PHY_CTRL__TEST_POWERDOWN_MSK = 0x40000000`, shift `0x1e`.
- Reset default for `PHY_CTRL` is `0x185`.
  - Therefore `TEST_POWERDOWN` (bit 30) defaults to **0** after reset.
- The same PPW control block exists per die (D1/D2/D3 all have distinct `...EP_PPW_0_PPW_CTRL_0` base addresses), so control is available per-die and can be programmed independently by addressing the relevant die base.
- Remote RTL path confirms wiring: PPW CSR field `cfg_test_powerdown` is routed to PHY pin `phy0_test_powerdown` through `cfg_test_powerdown_mx` in PPW wrapper logic.
- Local FW code in `fw/ip/pcie` does not currently show explicit programming of `TEST_POWERDOWN`; absent a strap/hard-tie policy elsewhere, reset defaults leave it deasserted.

## Sources
- Local generated reg defs:
  - `fw/build_artifacts/include/prototype/ppw_ctrl_regs.h` (`PPW_CTRL_PHY_CTRL__TEST_POWERDOWN_*`)
  - `fw/build_artifacts/include/prototype/ppw_ctrl_regs_attr.h` (`PPW_CTRL_PHY_CTRL__RST = 0x185`)
- Local per-die address map:
  - `fw/build_artifacts/include/prototype/fw/arm/project_bases_fw.h` (`D1/D2/D3_MNG_PCIE_EP_CLS_EP_PPW_0_PPW_CTRL_0`)
- Local architecture doc:
  - `fw/ip/pcie/docs/pcie-cluster-architecture.md` (PHY CPU control via `ppw_ctrl_base`)
- Remote RTL (`Elmnt-Internal/ip.pcie.rtl`):
  - `ppw_macro/rtl/ppw_misc.v` (`phy_ctrl_test_powerdown_data_r -> cfg_test_powerdown`)
  - `ppw_macro/rtl/ppw_wrap.v` (`cfg_test_powerdown_mx -> phy0_test_powerdown`)
