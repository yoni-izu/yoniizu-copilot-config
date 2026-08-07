# DM Controller Link Width Configuration (LINK_CAPABLE + NUM_OF_LANES)

- **Recorded:** 2026-08-05
- **Status:** Verified from doc (DWC DM Controller Databook v6.42a)
- **Tags:** link width, lanes, link_capable, num_of_lanes, port_link_ctrl_off,
  gen2_ctrl_off, max_link_width, link_capabilities_reg, lane resizing, x4,
  dm controller, dwc, ltssm, detect, polling, se, scale engine

## Scope / Question
To force/restrict the DM (dual-mode) PCIe controller link width (e.g. force x4),
is writing `PORT_LINK_CTRL_OFF.LINK_CAPABLE` required, or is
`GEN2_CTRL_OFF.NUM_OF_LANES` alone enough?

## Findings
- **Both writes are the documented procedure.** Databook §3.2.1 "Lane Resizing"
  (p.138): to restrict the maximum width —
  1. Program `LINK_CAPABLE` field of `PORT_LINK_CTRL_OFF`.
  2. Program `NUM_OF_LANES` field of `GEN2_CTRL_OFF`.
- The two fields gate **different LTSSM phases** (Appendix K "How to Turn Off
  Unused Lanes", p.956, example x4 core → x1 PHY):
  - `LINK_CAPABLE` (`PORT_LINK_CTRL_OFF`) — used by the LTSSM **in Detect**
    (how many lanes to detect).
  - `NUM_OF_LANES` (`GEN2_CTRL_OFF`) — number of lanes to check when exiting
    **L2.Idle or Polling.Active**.
- **`LINK_CAPABLE` encoding:** x1=`0x1`, x2=`0x3`, x4=`0x7`, x8=`0xf`, x16=`0x1f`
  (i.e. `2*width - 1`). Confirmed by p.956 (x4=6h7, x1=6h1). Field mask on our
  gen (ips/se/v1) = `0x3f0000` (PORT_LINK_CTRL_OFF bits [21:16]).
- **`NUM_OF_LANES`** = actual lane count (1,2,4,8,16). Field mask `0x1f00`
  (GEN2_CTRL_OFF bits [12:8]). `GEN2_CTRL_OFF` reset = `0x300304c8` →
  NUM_OF_LANES resets to **4 (x4)** on our SE controllers.
- **Reset defaults already = x4 for an x4-synthesized core:** p.956 shows a
  4-lane controller resets `LINK_CAPABLE=0x7` and `NUM_OF_LANES=0x4`. So on a
  natively-x4 SE DM controller, forcing x4 just re-asserts the reset values
  (harmless / defensive), it does not change trained width.
- **Third register only needed when capping BELOW the hardware max:** to reduce
  a wider core (e.g. x8→x4) you must ALSO reprogram
  `LINK_CAPABILITIES_REG.PCIE_CAP_MAX_LINK_WIDTH` (p.956) so the RP advertises
  the reduced Max Link Width. Not required when forcing to the native width.
- Controller supports x1/x2/x4/x8/x16; **x12 is not supported** (p.138).
- Post-linkup, negotiated width is read from the PCIe Link Status register
  (`PCIE_CAP_NEGO_LINK_WIDTH`), surfaced by `dwc_dm_ctl_get_link_status()`
  (`status->width`).

## FW mapping (ip.se.sw.fw)
- `dwc_dm_set_target_link_width()` (dwc_dm_ctl.c) writes both LINK_CAPABLE and
  NUM_OF_LANES on the role's DBI (dsp for RP, usp for EP) — matches the p.138
  2-step procedure. It does NOT touch PCIE_CAP_MAX_LINK_WIDTH (fine for
  force-native-x4; would be needed to truly cap a wider core).
- SE init step `el_se_step_set_link_width` runs before LTSSM enable, forcing
  `CONFIG_EL_SE_PCIE_TARGET_LINK_WIDTH` (default 4). Mirrors the target-link-
  speed step.

## Sources
- Doc: `DWC_pcie_ctl_dm_databook.pdf` p.138 (§3.2.1 Lane Resizing), p.956
  (App K "How to Turn Off Unused Lanes"), pp.868-871 (lane reversal figures use
  GEN2_CTRL_OFF.NUM_OF_LANES).
- Regs: `ips/se/v1/.../dm_dsp_dwc_pcie_dsp_regs.h`
  (`..._PORT_LINK_CTRL_OFF__LINK_CAPABLE_MSK=0x3f0000`,
  `..._GEN2_CTRL_OFF__NUM_OF_LANES_MSK=0x1f00`, `GEN2_CTRL_OFF__RST=0x300304c8`).
- RTL/FW: `ip.se.sw.fw` → `drivers/snps_dm_ctl/dwc_dm_ctl.c`
  (`dm_set_target_link_width`), `drivers/scale_engine/el_se.c`
  (`el_se_step_set_link_width`).
