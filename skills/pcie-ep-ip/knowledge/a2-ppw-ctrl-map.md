# A2 PPW_CTRL register map and PHY "done" handshake

- **Recorded:** 2026-11-12
- **Status:** Verified from RTL (Neo, a2 r02/r03); the PHY done bit is Partial
- **Tags:** a2, ppw, ppw_ctrl, phy_ctrl, phy_status, pll lock, refclk detect, sris, common refclk, upcs, pmd, iccm, dccm, fw_csr_8, cm_fw_csr_0

## Findings
- **PPW_CTRL is at PPW+0x50000.** It is not "3 registers". It contains:
  - `MISC_CTRL` 0x0, `PHY_CTRL` 0x4, `CFG_OB_CLK_DIV` 0x8.
  - `PHY_STATUS` 0x40: PLL0/PLL1 lock and refclk detect.
  - `SPECIAL` 0xE00.
- **PPW sub-windows:** UPCS 0x0, PMD 0x10000, ICCM 0x20000, DCCM 0x40000.
- **`PHY_CTRL` reset traps.** `USE_CTRL_COMMON_REFCLK` (bit 3), `USE_CTRL_SRIS` (bit 4) and `LANE_SRIS` (0xF) all reset to **1**. FW must program the refclk mode explicitly. SRNS selection was not confirmed.
- **The A1 `CPU_RUN_REQ`/`CPU_IND` are gone.** The PHY CPU is started by FW through `PHY0_CPU_CTRL` 0xCC bit 2 (RUN_REQ) and acknowledged at `ACK_STS` 0xF8 bit 18. The copy-FSM run request is unconnected (`piw_macro.v:1178`). See `a2-phy-cpu-start-and-copy-fsm.md`.
- **PHY boot "done" is written by the PHY FW, not by HW.** There are two candidates:
  - PMD `FW_CSR_8` bit 2 (`FW_STATIC_CFG_DONE`).
  - CM `FW_CSR_0` bit 0.

  Which one FW should poll is undecided; confirm with Synopsys or the PHY FW release notes.
- **PPW raises no interrupts on A2.** PHY memory ECC must be polled.

## Sources
- RTL via Neo: `ip.pcie.rtl` ppw_macro / PPW CSR (a2 mirror, HEAD 77b45f10); `piw_macro.v:1178`.
