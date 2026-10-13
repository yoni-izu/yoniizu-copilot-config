# SW-triggered PHY FW SRAM copy (PPW_SW_SRAM_COPY_*) — including BOOT_FROM_ROM mode

- **Recorded:** 2026-09-08
- **Status:** Verified from RTL + CSR headers + FW driver
- **Tags:** ppw_sw_sram_copy, sw sram copy, boot_from_rom, phy fw copy, ccm_type,
  iccm, dccm, phyfw_cp_to_phy, el_piw, fw_sram_copy_handler, piw_main_cfg,
  sram_copy_fsm, prst_sram_copy, phy rom, sram copy done, copy base addr, iridium a1

## Scope / Question
How the **SW-triggered** PHY-FW copy engine works vs the PRST-triggered one, and
specifically what a SW copy that sources from the **PHY internal ROM**
(`BOOT_FROM_ROM=1`) must program. Needed to convert a ROM PCIe bring-up debug
sequence from `PPW_PRST_SRAM_COPY_CFG.BOOT_FROM_ROM` (PRST-driven) to the SW
method while still copying from ROM.

## Findings
Register block = **PIW_MAIN_CFG** (Iridium/a1 PIW_MAIN_0 base `0x100000000408000`,
regmap `pcie v2/0_0_0`). Offsets:
- `PPW_SW_SRAM_COPY_START` 0x0 — `VAL` bit0 = start trigger.
- `PPW_SW_SRAM_COPY_DONE` 0x4 — `VAL` bit0 (single bit) = SW copy complete.
- `PPW_SW_SRAM_COPY_CFG` 0x8 — `SIZE`[14:0] (0x7fff), `PHY_IDX`[15], `OFFSET`[29:16]
  (0x3fff0000), **`CCM_TYPE`[30] (0x40000000)**, **`BOOT_FROM_ROM`[31] (0x80000000)**.
  Reset = 0x4000 (SIZE default = full depth). `CCM_TYPE`: **0 = DCCM, 1 = ICCM**.
- `PPW_PRST_SRAM_COPY_CFG` 0xc — `BOOT_FROM_ROM` bit0, `CONT_ON_PRST_ASSERT` bit1,
  `BROADCAST_CHICKEN` bit2 (the PRST/HW-autonomous path).
- `PPW_DCCM_SRAM_COPY_BASE_ADDR_0/_1` 0x10/0x14 — **per-PHY** dest base
  `{phy1, phy0}`, each a 32-bit MNG-local address (NOT lo/hi of one 64-bit
  addr). `_0`=phy0, `_1`=phy1 (phy1 used only for a 2-PHY broadcast copy).
- `PPW_ICCM_SRAM_COPY_BASE_ADDR_0/_1` 0x18/0x1c — same, per-PHY `{phy1, phy0}`.
- `PPW_PRST_SRAM_COPY_DONE` 0x28 — `VAL`[3:0]; single PRST trigger copies BOTH
  CCMs → 0x3 = both done (contrast: SW done is 1 bit, one CCM per trigger).

**SW copy is per-CCM-type, one trigger each.** RTL FSM: on
`csr_cfg_sram_sw_copy_start`, `ccm_type ? SW_ICCM : SW_DCCM`, run, `SW_DONE`,
back to `IDLE`. To load both CCMs via SW you trigger twice (ICCM then DCCM, the
order `el_piw_sram_to_phy_cp_impl` uses).

**BOOT_FROM_ROM selects the source only.** `sram_copy_from_rom = sw_copy_rom`
(bit31). When set the engine reads from the PHY ROM mems
(`sram_fw_mem_iccm_rom` / `sram_fw_mem_dccm_rom`); when clear it reads the PIW FW
SRAM. Either way the **destination base address is still taken from
`copy_base_addr_iccm/dccm`** (offsets 0x18 / 0x10) — so those MUST be programmed
even in ROM mode. `SIZE`/`OFFSET` are the SW-programmed values in ROM mode too;
full copy = OFFSET 0, SIZE = full depth `SRAM_FW_DEPTH = 16384 = 0x4000` lines
(1 line = 32-bit word; `piw_pkg.v` "SRAM FW COPY size is 64K"). SW copy does NOT
require `ICCM/DCCM_SRAM_COPY_INIT.DONE` (that gate is PRST-path only).

**Destination addresses = PHY CCM AHB base (from RTL `pcie_ep_cls_pkg.v`, NOT
`project_bases`).** Derivation: `GBL_PCIE_MNG_BASE_ADDR = 0x0040_0000`;
`PPW_PHY_CTRL_BASE = +PCI_512KB(0x80000) = 0x480000`;
`PPW_PHY_CCM_BASE = +2*PCI_64KB(0x20000) = 0x4A0000`; so
**PHY ICCM base = `0x4A0000`**, **PHY DCCM base = ICCM + PCI_128KB(0x20000) =
`0x4C0000`** (DCCM confirmed by the `ppw_wrap.v` AHB comment `0x4C0000 DCCM`).
The copy register holds the 32-bit MNG-local value (`AXI_MNG_ADDR_WDT=32`); the
SOC/device-region high bits are applied downstream by the NoC.
**WARNING — do NOT use `project_bases` `phy_iccm`/`phy_dccm` (`0x490000`/`0x4a0000`,
nor the older `0x450000`/`0x460000`): those are the PIW FW SRAM instances (copy
SOURCE), NOT the PHY CCM AHB destination.** The CSR **already resets** to
`PPW_PHY_ICCM/DCCM_BASE_ADDR` (`piw_main.v` packs `{BASE, BASE}` into both phy
slots), so `phyfw_cp_to_phy()` writing `LSB32(phy_ccm_addr)` to the `_0` (phy0)
register is only for explicitness; `_1` (phy1) is left at reset (single-PHY,
`PHY_NUM_MACROS=1`).

**Reference SW sequence per CCM (mirrors `phyfw_cp_to_phy`):**
1. `PPW_SW_SRAM_COPY_CFG` (0x8) = `BOOT_FROM_ROM | CCM_TYPE | OFFSET | SIZE`
   → ICCM ROM = `0xC0004000`, DCCM ROM = `0x80004000`.
2. `PPW_{I,D}CCM_SRAM_COPY_BASE_ADDR_0` (0x18 / 0x10) = LSB32 of PHY CCM dest.
3. `PPW_SW_SRAM_COPY_START` (0x0) = 0x1.
4. Poll `PPW_SW_SRAM_COPY_DONE` (0x4) until VAL bit0 = 1.

## Sources
- CSR: `fw/build_artifacts/include/v2/0_0_0_0p0_0000/prototype/piw_main_cfg_regs.h`
  (`PIW_MAIN_CFG_PPW_SW_SRAM_COPY_*`, `..._CCM_TYPE_MSK 0x40000000`,
  `..._BOOT_FROM_ROM_MSK 0x80000000`, base-addr offsets 0x10/0x18).
- RTL: `Elmnt-Internal/ip.pcie.rtl` → `piw_macro/rtl/fw_sram_copy_handler.v`
  (`sram_copy_fsm_t` SW_DCCM/SW_ICCM states, `sram_copy_from_rom_ns`,
  `sram_copy_ppw_base_addr_ns` = `copy_base_addr_{dccm,iccm}`,
  `sram_copy_size_ns`, `csr_sts_sram_sw_copy_done`) and `piw_macro/rtl/piw_pkg.v`
  (`SRAM_FW_DEPTH = 16384`). `piw_main.v` maps CSR fields → handler ports.
- FW: `fw/ip/pcie/ep/drivers/el_piw/el_piw.c` (`phyfw_cp_to_phy`,
  `el_piw_sram_to_phy_cp_impl`, `sram_cp_ccm_base_addr[ccm_type]`).
- Bases: `fw/build_artifacts/include/projects/a1/3_0_11_3p0_1389/bases/fw/native/project_bases_fw_native.h`
  (`MNG_PCIE_EP_CLS_EP_PPW_0_PHY_ICCM_0 = 0x100000000450000`, `..._DCCM_0 = 0x100000000460000`).
