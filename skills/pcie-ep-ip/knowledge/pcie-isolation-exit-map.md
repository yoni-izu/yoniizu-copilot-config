# PCIe isolation-exit map + Device Info SRAM access prerequisites (device side)

- **Recorded:** 2026-11-01
- **Status:** Verified from FW source (A1 power-on workspace)
- **Tags:** isolation, iso_sw_man, pep_ctrl, piw fiu, fiu termination, pcie_ep_iso, por clamp, device info sram, sram_info, gw_mem_init, special_memory_select, fla_rom, fla_rt, boot order, exit termination

## Scope / Question
Every place the PCIe block (PEP + PIW) is taken out of isolation in the FW, and the
prerequisites for accessing the **Device Info SRAM** (PIW CTRL block) from the device/FW side.
Complements `ds-term.md`, which covers the *host-side* gating (DS_TERM MSTR gate + `CTRL_RR_2`).

## PCIe isolation / termination — three distinct mechanisms

1. **POR-level cluster clamp** — `PSOC_MISC_POR_PCIE_EP_ISO` (in PSOC_PIO_MISC POR block).
   Cleared by ROM `fla_rom_pcie_isolation_cfg()` (`baremetal/fla_rom/src/common.c:948`,
   die0/die1 only, efuse-skippable via `EFUSE_EXTN_SKIP_PCIE_ISOLATION_CFG`).
2. **PIW FIU_TOP terminations** (NoC FIU instances fronting the PCIe cluster): FIU_MEM_MST,
   FIU_CTRL_MST, FIU_MNG_MST + FIU_SLV's MEM/MNG/CTRL SLV + DBG `ISO_SW_MAN`. HW enters
   termination on reset (NoC spec "Reset Isolation"); FW must exit.
3. **PEP_CTRL block-local reset-isolation regs** — `PEP_CTRL_{MSTR,ELBI,SLV,DBI,MNG}_ISO_SW_MAN`
   in PEP_MNG_PEP_CTRL. **Pulse semantics**: write 1 then 0 ("FW needs to write it twice in
   order to generate pulse (TERM_EN=0 is the default)… else the Reset ISO flow will not work
   properly on the next loop" — `common.c:901-919`).

## All the places PCIe is taken out of isolation

| # | Stage | Code | What it exits |
|---|-------|------|---------------|
| 1 | **FLA.ROM, every boot** (die0/die1, `!VARIANT_SIMULATOR/ONIX`) | `fla_rom_fiu_cfg()` (`common.c:1392` → 1056-1060): `fla_rom_pcie_isolation_cfg()` → `fla_rom_init_pcie_term_cfg()` → `fla_rom_pcie_termination_cfg()` (`common.c:854`) → `fla_rom_fiu_term_timeout_cfg()` | POR clamp; all PIW FIU MST+SLV+DBG `ISO_SW_MAN`; PEP_CTRL ×5 pulse; PCIe FIU MNG_SLV TERM_TIMEOUT (skipped if link already up). Each step efuse-skippable (`EFUSE_EXTN_SKIP_*`). |
| 2 | **FLA.ROM recovery path** | `fla_rom_recovery_pcie_term_cfg()` (`common.c:936`) | Same `fla_rom_pcie_termination_cfg()`; separate efuse skip (inverted polarity vs init: init-skip bit 1=do, recovery-skip bit 1=skip). |
| 3 | **FLA.RT** (asic/palladium boards) | `fla_rt_init_devices()` → `el_fiu_mgr_exit_termination_all()` (`zephyr_apps/fla_runtime/src/common.c:474`), with `CONFIG_EL_FIU_MGR_DISABLE_INIT=y` so the timing is explicit; preceded by `el_pcie_ep_host_set_downstream_termination(true)` on production (EOM) parts | PIW + PEP FIU **groups** via the el_fiu driver (board DT scopes the manager's groups). |
| 4 | **Runtime apps (e.g. mng)** | `EL_FIU_MGR` SYS_INIT level 1 prio 45 → full `fiu_mgr_reinit()` (step 4 = exit-termination) | All FIU groups incl. `piw_fiu` + `pep_fiu` (mng DT `fiu-groups` list), chip-wide, + TERM_TIMEOUT etc. |
| 5 | **Post-reset** | reset driver → `el_reset_invoke_fiu_post_actions()` (PSOC high-prio term-exit) then `el_reset_invoke_fiu_reinit()` | Re-exits piw/pep groups after a die/PCIe-domain reset. |
| 6 | **SV/verification only** | `dwc_ep_ctl_sv.c:625-674` | Its own FIU MST + PEP_CTRL exits for test benches — not a production path. |

**The production PCIe EP driver (`el_pcie_ep`/`dwc_ep_ctl`) does NOT exit any isolation.**
Its DS_TERM writes (`PEP_CTRL_AUX_DS_TERM_CTRL`) are host-traffic termination, a different
mechanism (see `ds-term.md`).

## Device Info SRAM — device-side access prerequisites

SRAM lives in the **PIW CTRL block** (`CTRL_PCIE_EP_CLS_PIW_CTRL_SRAM_INFO_0`; ARM FW address
`0xa8400400` die-0 / `0xa2400400` local alias, generated map
`proj_infra/include/arm/device_info_sram_mem_addr.h`: FLA_ROM_STATUS 1K + DEVICE_INFO 1K +
PTL_TUNING 1K + reserved, 8K total). FW reaches it over the **CTRL fabric → PIW FIU CTRL_SLV**.

Prerequisites (in boot order):
1. **PSOC FIU_DRAIN MST exits** (`fla_rom_psoc_fiu_cfg`, `common.c:742`) — MuC can master out.
2. **`PSOC_MISC_POR_PCIE_EP_ISO` cleared** — PCIe EP cluster un-clamped (die0/die1 only).
3. **PIW FIU_TOP CTRL_SLV termination exited** (and MNG_SLV for the PIW_MAIN CSRs used in #4).
4. **SRAM HW memory-init completed**: select memory idx **23** in
   `PIW_MAIN_CFG_SPECIAL_MEMORY_SELECT`, poll `PIW_MAIN_CFG_SPECIAL_GW_MEM_INIT == 0`
   (1 ms timeout) — `fla_rom_poll_for_device_info_sram_init_done()`
   (`baremetal/fla_rom/src/device_status.c:27`). The GW memory initializer zeroes the SRAM
   after reset; writing before it finishes is unsafe.
5. **Policy gates**: die0 only; efuse `fla_rom_efuse_pcie_device_info_sram_en()` must be set.
6. ROM status area is cleared 1 dword per **16-byte stride** (`fla_rom_clear_device_rom_status`)
   — matches the upper-16B-access-raises-FW-interrupt granularity noted in `ds-term.md`.

**NOT needed for device-side access**: the PCIe EP driver, PHY FW load, link-up, the PEP_CTRL
ISO pulse (that gates PEP controller ports — the SRAM is in PIW), DS_TERM (host-side only).
**Proof**: FLA.ROM writes the Device Info SRAM (`fla_rom_device_status_init`, called from
`fla_rom_post_init`, `common.c:1441`) long before any driver exists; the isolation exits at
`common.c:1392` precede it in the ROM flow.

Host-side access additionally needs: link up, DS_TERM MSTR gate clear, and the `CTRL_RR_2` /
`boot_log_dis`-efuse security range filter (see `ds-term.md`).

## Sources
- FW: `baremetal/fla_rom/src/common.c:742-960, 1052-1060, 1392, 1441` (isolation/termination cfg
  + call order), `baremetal/fla_rom/src/device_status.c` (SRAM init-done poll + writes),
  `baremetal/fla_rom/src/sec_access_ctrl.c` (CTRL_RR_2), `baremetal/fla_rom/src/efuse.c:842`,
  `zephyr_apps/fla_runtime/src/common.c:460-478`, `zephyr_apps/fla_runtime/boards/*.conf`
  (`EL_FIU_MGR_DISABLE_INIT`), `ip/noc/drivers/el_fiu/el_fiu_mgr.c` (reinit steps),
  mng app DT (`piw_fiu`/`pep_fiu` in `fiu-groups`), `ip/pcie/ep/drivers/snps_ep_ctl/dwc_ep_ctl_sv.c`,
  `proj_infra/include/arm/device_info_sram_mem_addr.h` (A1 power-on workspace)
- Related: `ds-term.md` (host-side), noc-ip skill `knowledge/fiu-termination-boot-ordering.md`


## A2 UPDATE (2026-11-09, Neo against A2 RTL/CSR)
On A2, `PSOC_MISC_POR.PCIE_EP_ISO` resets to 1 on every die, and FW must clear it. Also handle `RST_ISO_EN`, the isolation watchdog and `CFG_ISO_SW_MAN_SL`. See [a2-reset-isolation-perst.md](a2-reset-isolation-perst.md).
