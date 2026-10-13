# A1/Iridium ASIC — indirect transport selection & per-app D2D SB init

- **Recorded:** 2026-09-25
- **Status:** Verified from FW (workspace `iridium/a1_power_on_003`, branch `a1_power_on_branch`)
- **Tags:** iridium, a1, asic, indirect transport, spi-gw, spi_gw0, el,die-indirect-transport,
  chosen node, sbinit, sideband bringup, el_d2d_manager_sideband_bringup, per-app d2d,
  primeboot, mng, fla runtime, aw_d2d depends el_spi_gw, transport selection

## Scope / Question
For the A1/Iridium ASIC flash build (FLA.RT + PRIMEBOOT + MNG): does D2D init the sideband (SB)?
What is the selected indirect transport? Does SPI-GW survive if the D2D driver is disabled?

## Findings

### Selected indirect transport
- Chosen node lives in `proj_infra/soc/dts/a1/a1_hsi.dtsi`:
  `chosen { el,die-indirect-transport = &spi_gw0; };`
  (DTS name uses hyphens/comma → C macro `DT_CHOSEN(el_die_indirect_transport)`; grep with the
  underscore form misses it — search `el,die-indirect-transport`.)
- `spi_gw0` = `el,spi-gw`, die-id 0, die-mode "primary" (in `a1_psoc.dtsi`). It registers the
  `el_die_indirect_transport_api` vtable (`el_spi_gw.c`, `el_spi_gw_transport_api`).
- Runtime default selector is `EL_DIE_INDIRECT_TRANSPORT_DEFAULT` → routes through the chosen
  device = **SPI-GW**. (`EL_DIE_INDIRECT_TRANSPORT_MMIO` is the narrow runtime override; see
  `pre-fiu-transport-reachability.md`.)
- **=> The active indirect transport is SPI-GW (`spi_gw0`).**

### Per-app D2D / SB init (ASIC quad-die flash build, board `el_arm_a1_asic`)
- **FLA.RT** (`fla_runtime/boards/el_arm_a1_asic.conf`): **no D2D and no SPI-GW config at all** —
  D2D driver not built, no SB init. (Matches boot-flow table: EL D2D `—` in FLA.RT.)
- **PRIMEBOOT** (`primeboot/boards/el_arm_a1_asic.conf`): `EL_D2D_MANAGER=y`,
  **`EL_D2D_MANAGER_SIDEBAND_BRINGUP=y`**, `EL_D2D_WRAPPER=y`, `AW_D2D=y`, `EL_SPI_GW=y`.
  This is **where SBINIT / UCIe.SB is brought up**. Conf comment: *"Mainband is non-functional on
  the current silicon: bring the link up over UCIe.SB only."*
- **MNG** (`mng/boards/el_arm_a1_asic.conf`): `EL_D2D_MANAGER=y`, `EL_D2D_WRAPPER=y`, `AW_D2D=y`,
  `EL_SPI_GW=y`, `EL_D2D_DUP_FW_LOAD=y`, `EL_D2D_MCU_FW_FLASH=y`, `EL_D2D_MCU_FW_AUTHENTICATE=y`
  — but **`SIDEBAND_BRINGUP` is NOT set**. So MNG does not replay the SBINIT bring-up; per
  `d2d_init_flow.md` it takes the fast-skip (links already up) or SRAM/mainband flow, expecting SB
  already trained by PRIMEBOOT.
- **Net:** SB is initialized **only in PRIMEBOOT**. FLA.RT has no D2D; MNG runs D2D but does not
  re-init SB.

### Disabling the D2D driver vs SPI-GW
- Dependency direction: **`AW_D2D` (and the D2D stack) `depends on EL_SPI_GW`** (`Kconfig.aw_d2d`).
  `EL_SPI_GW` independently `select`s `EL_DIE_INDIRECT_TRANSPORT` and registers the transport vtable.
- => Disabling the D2D driver (`EL_D2D_MANAGER`/`EL_D2D_WRAPPER`/`AW_D2D`) does **NOT** disable
  SPI-GW. As long as `CONFIG_EL_SPI_GW=y` and `spi_gw0` is `status="okay"`, **SPI-GW still works as
  the indirect transport**. You can have SPI-GW without D2D, but not D2D without SPI-GW.
- SPI-GW transport is used well beyond D2D (dup-eng, efuse/vcore/vme/psoc binning, FIU, SE) — all
  gated on `EL_DIE_INDIRECT_TRANSPORT`, all keep working without the D2D driver.

## Sources
- DTS: `proj_infra/soc/dts/a1/a1_hsi.dtsi` (chosen `el,die-indirect-transport = &spi_gw0`),
  `proj_infra/soc/dts/a1/a1_psoc.dtsi` (`spi_gw0` node)
- Header: `infra/include/el_die_access.h` (`EL_DIE_INDIRECT_TRANSPORT_DEV`, selector enum)
- FW: `ip/psoc/drivers/el_spi_gw/el_spi_gw.c` (`el_spi_gw_transport_api`),
  `ip/psoc/drivers/el_spi_gw/Kconfig` (`select EL_DIE_INDIRECT_TRANSPORT`),
  `ip/d2d/drivers/fw/Kconfig.aw_d2d` (`AW_D2D depends on EL_SPI_GW`)
- Confs: `zephyr_apps/{fla_runtime,primeboot,mng}/boards/el_arm_a1_asic.conf`
- Flow doc: `ip/d2d/docs/d2d_init_flow.md`
- Related: `pre-fiu-transport-reachability.md`, `sbinit-done-probe.md`
