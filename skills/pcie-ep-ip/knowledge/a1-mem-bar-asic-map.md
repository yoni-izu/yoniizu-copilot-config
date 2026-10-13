# A1 (Iridium) MEM BAR map — ASIC 256GB vs Palladium 32GB, and where it lives in DT

- **Recorded:** 2026-09-27
- **Status:** Verified from generated header + DTS + driver
- **Tags:** mem bar, mem_bar, bar map, pcie_map, el,pcie-bar-map, a1_pcie_map.h, mem_bar_size, mem_bar_region_size, pldm, palladium, asic, 256gb, 64gb per die, die0_region, bmu mem regions, tlp coverage, el_pcie_map, bar descriptors, iridium

## Scope / Question
What the A1 PCIe MEM BAR (BAR2) descriptor set should be on real silicon (ASIC),
which DT file is the source of truth, and what "all memory space is mapped" means
for the BMU.

## Findings

### Canonical sizes live in the generated `a1_pcie_map.h` (project `shared_defs`)
Two parallel families of labels — ASIC (real silicon) and `*_PLDM` (Palladium emulation):

| Label | Value | Meaning |
|-------|-------|---------|
| `MEM_BAR_SIZE` / `_HI`=0x40 `_LO`=0x0 | `0x40_0000_0000` | **256GB** total ASIC MEM BAR |
| `MEM_BAR_REGION_SIZE` / `_HI`=0x10 `_LO`=0x0 | `0x10_0000_0000` | **64GB** = one full die window |
| `MEM_BAR_SIZE_PLDM` / `_HI`=0x8 | `0x8_0000_0000` | 32GB Palladium BAR |
| `MEM_BAR_REGION_SIZE_PLDM` / `_HI`=0x1 | `0x1_0000_0000` | 4GB Palladium region |
| `MEM_BAR_DIE_SIZE` | `0x40_0000_0000` | **mislabelled** in the header comment as "64GB per die" — the value is 256GB. Use `MEM_BAR_REGION_SIZE` for the per-die window. |

Per-die bases (`Dx_MEM_BAR_GMEM_BASE_HI/LO`) are `chip_base + die*0x10_0000_0000`:
`0x0200_0000_0000_0000`, `0x0200_0010_...`, `0x0200_0020_...`, `0x0200_0030_...`.
`Dx_MEM_BAR_SOB_LMEM_BASE_*` (`...0F/1F/2F/3F_0000_0000`) are **Palladium-only** —
they exist because PLDM maps only 2×4GB slices per die; on ASIC the single 64GB
per-die region already contains GMEM + SOB + LMEM.

### ASIC descriptor set (4 regions, full coverage)
```
die0_region  0x0200_0000_0000_0000  64GB
die1_region  0x0200_0010_0000_0000  64GB
die2_region  0x0200_0020_0000_0000  64GB
die3_region  0x0200_0030_0000_0000  64GB
bar-size     0x40_0000_0000 (256GB)  -> 4 x 64GB == BAR size, no TLP holes
```
Both constraints hold: every region size and the BAR size are powers of two
(BMU `MASK = ~(SIZE-1)`), and `sum(regions) == bar-size` so no TLP address in the
BAR misses BMU matching (unmatched → HW error interrupt). 4 regions fit easily in
`CONFIG_EL_PIW_BMU_MEM_REGIONS_NUM` (default **8** MEM regions; CFG has 32).

### Where the map lives (DT layering) — A1
- **`proj_infra/soc/dts/a1/a1_hsi.dtsi`** holds `pcie_map` + `cfg_bar` + `mem_bar`
  (all `status = "disabled"`). This is the **ASIC default** and the single source of
  truth for real silicon. It used to (wrongly) carry the PLDM 8×4GB / 32GB map with
  the comment "ASIC enumerates the same 32GB aperture as Palladium".
- **Every Palladium board/overlay overrides `&mem_bar` `reg`/`reg-names`/`bar-size`
  explicitly** with the `*_PLDM` labels — verified across `proj_infra/boards`,
  `tools/{fw_reg_cli,soc_cli}`, `zephyr_apps/{mng,primeboot}`. So changing the
  `.dtsi` default only moves ASIC boards.
- ASIC boards (`el_arm_a1_asic*`, `el_host_a1_asic*`) only do `&mem_bar { status = "okay"; }`
  and inherit the `.dtsi` default.
- **Onix** (`el_host_a1_onix.dts`) `/delete-node/`s both bar-map nodes and defines only
  a CFG BAR — MEM BAR changes don't reach it.

### `reg-names` are debug-only
`soc_region.region_name` is consumed only by log/shell prints
(`el_pcie_ep_shell.c`, `el_piw.c` BMU cfg log). The one place names are matched is
`generate_pcie_constants.py::_annotate_canonical_addr()`, and only for **truncated
32-bit** variants (Onix) borrowing a 64-bit reference — 64-bit variants are their own
canonical. So renaming ASIC regions (`die0_gmem_region`+`die0_sob_lmem_region` →
`die0_region`) is safe.

### Host-side Python constants are derived, not hand-written
`infra/scripts/interfaces/pcie_if/generate_pcie_constants.py` +
`proj_infra/scripts/python/pcie_if/pcie_sources.json` parse `a1_pcie_map.h` and the
DTS. `variants["asic"]["bars"]` = the **base** `a1_hsi.dtsi` map; variant DTS files
override per `bar_num`. It can be driven standalone for validation:
`g.parse_h_defines(...)` + `g.build_variant_bars(base_dts, variants, defines)` and
then assert `sum(region sizes) == bar size` per BAR.

### Known pre-existing gaps (not ASIC MEM BAR)
- **CFG BAR (BAR0)**: 256MB BAR vs 234.375MB of named regions → 16MB
  (`0x0F00_0000..0x0FFF_FFFF`) unmapped. No label exists in `a1_pcie_map.h` for it.
- **Palladium dual-die boards**: keep `bar-size = 32GB` while declaring only 4 of the
  8 PLDM regions → 16GB uncovered.

## Sources
- Header: `build_artifacts/include/projects/a1/shared_defs/a1_pcie_map.h`
  (copy at `sim/simulator/simulator_specs/a1_pcie_map.h`)
- DTS: `fw/proj_infra/soc/dts/a1/a1_hsi.dtsi` (`mem_bar`, `cfg_bar`),
  `fw/proj_infra/boards/native/el_host_a1/el_host_a1_{asic,palladium,onix}.dts`
- Binding: `fw/infra/dts/bindings/el_pcie_bar_map/el,pcie-bar-map.yaml`
- Driver: `fw/infra/drivers/el_pcie_map/el_pcie_map.c`
  (`BUILD_ASSERT_REGION_SIZE_POW2`, `BUILD_ASSERT_BAR_SIZE_POW2`, `DT_BAR_SIZE_GET`)
- BMU limits: `fw/ip/pcie/ep/drivers/el_piw/Kconfig`
  (`EL_PIW_BMU_MEM_REGIONS_NUM`=8, `EL_PIW_BMU_CFG_REGIONS_NUM`=32)
- Doc: `fw/ip/pcie/ep/docs/pcie-address-translation.md`
  (power-of-two rule, TLP routing coverage requirement)

## Update 2026-10-12 — A1 now ships TWO map nodes, selected at runtime

Verified on workspace `iridium/a1_power_on` (branch `a1_power_on_branch`).
`a1_hsi.dtsi` no longer has a single `mem_bar`; it declares two sibling
`el,pcie-bar-map` nodes plus a selector, **all `status="disabled"` by default**:

| Node | `map-id` | CFG child | MEM child | MEM shape |
|---|---|---|---|---|
| `pcie_map` | `EL_PCIE_MAP_SCHEME_LOW_BAR` | `cfg_bar` | `mem_bar` | 8 regions = die0..3 × (gmem, sob_lmem) @ `MEM_BAR_REGION_SIZE_PLDM`, `bar-size = MEM_BAR_SIZE_PLDM` (**32 GB**) |
| `pcie_map_high_bar` | `EL_PCIE_MAP_SCHEME_HIGH_BAR` | `cfg_bar_high` | `mem_bar_high` | 4 regions = one per die @ `MEM_BAR_REGION_SIZE` (64 GB), `bar-size = MEM_BAR_SIZE` (**256 GB**) |
| `pcie_map_sel` (`el,pcie-bar-map-sel`) | — | — | — | `default-map-ref = <&pcie_map>` (fallback when no strap) |

`cfg_bar` / `cfg_bar_high` are identical: 5 regions —
`mng_fabric`, `dbg_fabric`, `ctrl_fabric`, `global_stm`, `spi_flash`.

**Runtime selection:** `el_arm_a1_asic.dts` enables `pinctrl`, `gpio_ctrl_16_6`
and `not_on_tray_gpio` (die-0 pad 96), which auto-selects
`CONFIG_EL_PLATFORM_TRAY_DETECT`; it then compiles `pcie_map_high_bar` in
alongside the low map so `el_pcie_map_select()` / `el_utils_is_on_tray()` can
pick the 256 GB sled map at runtime. Without the high map compiled in, the strap
reads "on tray" but finds no map 0 and silently falls back to the default.

**Who enables what on A1 (verified):**
- `el_arm_a1_asic.dts` (ARM board) → `pcie_map_high_bar` + the tray strap only.
  The LOW map is enabled by each **app** overlay
  (`primeboot/boards/el_arm_a1_asic.overlay`, `mng/.../el_arm_a1_asic.overlay`
  turn on `pcie_map`/`cfg_bar`/`mem_bar`). `fla_runtime`'s ARM ASIC overlay
  enables only the EP nodes — its only okay map is the high map from the board.
- `el_host_a1_asic.dts` (native/host board) → `pcie_map` + `cfg_bar` + `mem_bar`
  (re-stating the PLDM 8-region/32 GB map). **No high-BAR map, no tray strap** on
  the host platform, for any app.
- `el_host_a1_palladium.dts` → `mem_bar` trimmed to **4 regions (die0+die1 only)**
  while keeping `bar-size = 32 GB` (the known uncovered-16 GB gap above).
- `el_host_a1_onix.dts` → `/delete-node/`s both BAR children, re-adds BAR0 only.

**Board conf/overlay file naming** in this tree is Zephyr's *SHORT* build string
(`zephyr_build_string(... SHORT ...)` drops the first qualifier, the SoC):
board `el_host_a1@ls_single_die/native/asic` → `boards/el_host_a1_asic_ls_single_die.{conf,overlay}`;
board `el_host_a1/native/asic` → `boards/el_host_a1_asic.{conf,overlay}`.
A missing file is silently skipped — so an app listed for a board but lacking the
matching pair builds with **zero** app-level PCIe config (`CONFIG_EL_PCIE_MAP`,
`EL_PCIE_EP`, `EL_PIW` are all `default n`, and `pcie_ep0`/`ep_ctl0`/
`pcie_ep_piw0` stay disabled). Current example: `tools/fw_host_cli` lists
`el_host_a1/native/asic` in its build matrix but only ships
`el_host_a1_asic_ls_single_die.*`, whose board entry is commented out in
`tools/module.infra.sw.fw.tools.star`.

Sources for this update: `fw/proj_infra/soc/dts/a1/a1_hsi.dtsi` (~L2570-2665),
`fw/proj_infra/boards/arm/a1/el_arm_a1_asic.dts` (~L555-592),
`fw/proj_infra/boards/native/el_host_a1/el_host_a1_{asic,palladium}.dts`,
`fw/infra/dts/bindings/el_pcie_bar_map/el,pcie-bar-map-sel.yaml`,
`fw/infra/drivers/el_pcie_map/Kconfig` (`EL_PCIE_MAP default n`),
`fw/zephyr/cmake/modules/extensions.cmake` (`zephyr_build_string`).
