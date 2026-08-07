# EP PHY = dual x4 macro behind one x8 IP + opt-in write-broadcast ("hiding") vs SU standalone x4

- **Recorded:** 2026-08-05
- **Status:** Verified from RTL (`Elmnt-Internal/ip.pcie.rtl`)
- **Tags:** ep phy, dual macro, x8, x4x4, phy_num_macros, apb broadcast, ahb broadcast, cpu_run_req
  broadcast, cfg_phy_apb_wr_broadcast, ppw_wrap, ppw_misc, phy0 phy1, lanes 0-3 4-7, scale up,
  se, c2c, dwc_pcie6_x4_ns, unified pcs, per-macro apb, loopback, prbs, per-lane, hiding trick

## Scope / Question
Is the PCIe EP "one PHY or two" from FW's perspective? Claim: EP has two PHY instances (8 lanes)
but the RTL "hides" this from FW; scale-up doesn't. Verify against RTL; does it affect a per-lane
loopback/PRBS driver that addresses lanes 0-7?

## Findings

### EP = two x4 PMD macros inside one Synopsys x8 IP
- `ppw_wrap` instantiates a single Synopsys `dwc_pcie6_upcs_x4_ns_x8_x4x4_pipe` with
  `PHY_NUM_MACROS = 2` (`pcie_ep_cls_pkg`). Three sub-blocks: **unified PCS** spanning all 8 lanes
  (`upcs_pcs_x8_x4x4`), **phy0** (`pmd_top_x4`, lanes 0-3), **phy1** (`pmd_top_x4`, lanes 4-7).
- "Unified PCS, **separate PMDs**. The PMD blocks are completely independent — each has its own
  PLLs, CPU, APB, AHB, JTAG, and SRAM interfaces." (PPW µArch §5.8 note 2). So the per-lane
  RX/TX/PMD/RX_VP register blocks live in **per-macro APB spaces**.
- Address map (PPW µArch §6.2): `PPW_PHY0_CTRL_BASE_ADDR` (32KB APB) and `PPW_PHY1_CTRL_BASE_ADDR`
  (32KB APB) are **distinct**; likewise `PPW_PHY0_CCM_BASE_ADDR` / `PPW_PHY1_CCM_BASE_ADDR` (128KB
  each). FW **can** address each macro independently. DTS mirrors this: `pcie_phy0` @ PPW_0 PHY_CTRL,
  `pcie_phy1` @ PPW_1 PHY_CTRL (two `snps,pcie6-phy` nodes, 4 lanes each).

### The "hiding" trick = opt-in, WRITE-ONLY broadcast (bring-up convenience), not address collapse
Three independent CSR-gated broadcast paths in `ppw_wrap`/`ppw_misc`:
- **APB write broadcast** (`cfg_phy_apb_wr_broadcast`): a PHY0-APB **write** is replicated to both
  macros — `phy_paddr_i = bc ? {2{pcip_phy_apb_paddr[0]}} : pcip_phy_apb_paddr`. Gated on
  `pwrite[0] & pselx[0] & penable[0]` → **only fires for writes that select PHY0**, and only
  when the CSR bit is set. Reads are always per-macro (broadcast is write-only; prdata/pready come
  from the addressed macro / phy0).
- **AHB write broadcast** (`cfg_phy_ahb_wr_broadcast`): same for CCM writes — used to load PHY FW
  into both ICCM/DCCM with one write stream.
- **CPU run broadcast** (`PPW_CTRL.CPU_RUN_REQ.req_broadcast`): `phy_cpu_run_req =
  {2{cfg_cpu_run_req_broadcast}} | cfg_cpu_run_req` → starts both PHY CPUs at once. FW init uses
  `phy0.PPW_CTRL.CPU_RUN_REQ.REQ_BROADCAST.set(1)`.
- Also note: EP's single `PPW_CTRL` lives in EP_PPW_0; **both** DTS PHY nodes point
  `ppw_ctrl_regs` at that one block (CPU_RUN_REQ broadcasts to both PHY CPUs). PHY_CTRL (the APB
  register space this loopback/PRBS feature uses) is **not** shared — it is per-macro.

So the "trick" is a bring-up convenience (configure/load/start both macros with one write), **not**
a collapse of the two macros into a single addressable PHY. FW retains independent per-macro APB
access, which the eye-margin tool and the loopback/PRBS driver rely on.

### Scale-up (SE/C2C) has no such trick
Each SE PHY is a **standalone x4 macro** (`dwc_pcie6_x4_ns`, single `phy0`, no x8 wrapper, no
`phy1`, no APB/AHB/CPU broadcast). DTS: 8 separate nodes `se_phy_p0..p7`, each its own
`DMP_PHY_CTRL` base, 4 lanes → 32 lanes total, each independently addressed.

## Impact on a per-lane loopback/PRBS driver (verified correct)
- Loopback/PRBS is **per-lane** and needs **independent** control + **per-lane read-back** of the
  BER counter → it must use the **independent per-macro instances**, NOT the write-only broadcast.
- The FW models EP as two device instances (`pcie_phy0`/`pcie_phy1`, 4 lanes each); a flat lane
  index 0-7 maps `inst = lane / 4`, `phy_lane = lane % 4`. Lanes 4-7 correctly target `pcie_phy1`'s
  PHY_CTRL base. Same model as the existing eye-margin tool (`MAX_TOTAL_LANES = 8`,
  `LANES_PER_INSTANCE = 4`). SU handled identically (each node an independent 4-lane instance).
- **Precondition/caveat:** if a prior bring-up left `cfg_phy_apb_wr_broadcast` **enabled**, a
  write that selects PHY0 (lanes 0-3) would also hit PHY1 — wrong for per-lane loopback. That
  broadcast enable is a PPW **CSR** bit (not in the PHY device's PHY_CTRL regmap), normally cleared
  after FW load. Treat "APB write broadcast disabled" as a precondition for per-lane loopback/PRBS.

## Sources
- RTL: `Elmnt-Internal/ip.pcie.rtl`
  - `ppw_macro/rtl/ppw_wrap.v` (APB broadcast L1173-1185, AHB broadcast L1146-1159,
    `PHY_NUM_MACROS` APB/AHB/CPU per-macro buses, `dwc_pcie6..x8_x4x4` instance)
  - `ppw_macro/rtl/ppw_misc.v` (per-macro APB/AHB ports, broadcast cfg outputs)
  - `ppw_macro/misc/PPW_MACRO_Micro_Architecture.md` §1.2 (`PHY_NUM_MACROS=2`), §3.4 dual-PHY lane
    mgmt, §3.9 CPU run broadcast, §4.2 AXI decode, §5.5/5.8 unified-PCS/separate-PMD, §6.2 addr map
- DTS: `fw/proj_infra/soc/dts/a1_hsi.dtsi` (`pcie_phy0/1` shared `ppw_ctrl_regs`, distinct
  `phy_ctrl_regs`; `se_phy_p0..p7`)
- FW: `fw/ip/pcie/ep/docs/pcie-init-sequence.md` (`REQ_BROADCAST`), eye-margin tool (8 lanes / 2 inst)
- Related: `phy-instance-count.md`
