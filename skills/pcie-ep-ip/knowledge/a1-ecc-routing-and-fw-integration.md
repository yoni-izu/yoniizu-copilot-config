# A1 PCIe EP ECC routing and firmware integration

- **Recorded:** 2026-11-15
- **Status:** A1 Neo mirror trace and checked workspace source; no built image or injection verified
- **Tags:** a1, pcie, piw, ppw, ecc, serr, derr, irq, sram_ecc, ras, flit

## Scope / Question

Are PCIe EP ECC indications routed to firmware and handled by the shared SRAM
ECC error and RAS subsystems?

## Findings

- Neo traced the A1 PIW SERR/DERR sources through the chip interrupt
  aggregation: PCIe0 DERR is full-chip interrupt bit 384 and SERR bit 448.
  PPW0 DERR/SERR are bits 480/496. The effective `fc_interrupt` implementation,
  PPW internal sources, and effective chip build were not verified. In a
  follow-up trace, Neo followed these bits through the CPU PLIC to CM7
  interrupt outputs 24/28/30/31, respectively. These source-level routes do
  not prove that a deployed image handles them.
- `a1_wire_interrupts.h` confirms the corresponding legacy Zephyr multi-level
  encoded indexes are `0x118`/`0x11c`/`0x11e`/`0x11f`, respectively: these
  are a different numbering domain, not conflicting hardware bit numbers.
  Current multi-die MNG uses third-level encoding (`WIRE_D0`), so board wiring
  needs revalidation against the current PLIC configuration.
- PIW combines PIW memory, FIU memory and IIP indications. In the traced A1
  IIP integration, PEP auxiliary memories' ECC inputs are tied off; the real
  IIP ECC indications come from controller RAM RAS signals. FLIT ECC is a
  separate SPI/SEI indication, not the PIW memory SERR/DERR path.
- In the checked A1 workspace, a dedicated PCIe ECC driver can forward PIW
  and conditional IIP PEP memory captures via `el_mem_ecc` and an
  `el_err_handler` callback; the PPW-control driver has a parallel optional
  ECC callback. The `sram_ecc` error-handler plugin registers the callbacks,
  unmasks sources, and routes events to the management bus and host EQ.
  The RAS SRAM-ECC plugin can subscribe, maintain a log and evaluate alarms,
  but has separate configuration dependencies.
- **Not enabled end to end in the checked A1 management configurations:**
  `EL_PCIE_EP_ECC` and `EL_PCIE_PPW_ECC` default off; the PCIe ECC device
  and ECC tree roots are disabled in base DTS; the A1 ASIC and host/ARM
  Palladium mng overlays do not enable them or add the PCIe/PPW producers
  to the SRAM-ECC plugin's device list. That list contains MuC ECC and NoC
  in the base tree. An enabled plugin alone is not PCIe integration. No
  generated `.config` / final DTS or running target was inspected.
- The PIW implementation leaves FIU memory masked and without a leaf
  callback; it leaves controller `RAS_RAM_*` masked and has only log/clear
  handling if observed. Thus controller RAM ECC is not forwarded as
  `el_ecc_info_t`. On A1 the conditional PEP memory path is tied off in the
  traced RTL. A1 FLIT uncorrectable ECC's separate SEI action is log-only;
  the correctable SPI cause has no leaf callback.
- **Multi-error loss risk:** both PCIe producers use `el_ecc_info()`. The
  checked shared implementation sweeps and clears all pending wrappers, but
  its compatibility callback retains only the first capture; multiple pending
  memory errors can therefore be cleared without being reported individually.
  This is a static code-path observation, not an injection-test result.
- Existing tracking and PRs as of 2026-11-15: Jira `IPPCIESW-161` (SRAM ECC
  interrupt handling) is In Review under the A1 `IPPCIESW-343` SRAM ECC
  protection epic (In progress). EP PR #299, project-infra PR #906 and
  MNG plugin PR #319 are merged, while MNG board enablement PR #115 (main)
  and #258 (power-on) are open and conflicting. The old PR #115 diff refers
  to `pcie_ppw_ecc0`, whereas current DTS uses `ppw_ctrl0`; it also uses
  legacy IRQ encodings and therefore needs adaptation and validation.
  `IPINFRASW-2043` (multi-wrapper error reporting) is Closed and infra PR
  #818 merged, but PCIe producer PR #424 remains open. The driver references
  `IPPCIESW-716` for controller RAM RAS trace forwarding, but the live Jira
  story explicitly scopes A2 1p4 and 1p3 unchanged; do not present it as
  confirmed A1 coverage. A targeted PCIe Jira search found no separate A1
  controller RAS-RAM or FIU ECC follow-up; this does not prove none exists.
  `IPPCIESW-550` (FLA Runtime polled copy-time check) is Closed.
- Minimal `fla_runtime` has a distinct synchronous PHY-FW copy ECC check,
  not the management interrupt/error pipeline. A1 ASIC enables PIW and PPW
  checks in log-only mode; the Palladium configuration enables the PIW
  check in log-only mode.

## Sources

- Neo A1 snapshot 2026-11-09: `ip.pcie.rtl` @ `693ce5494a4e`,
  `piw_macro/rtl/piw_main.v`, `pcie_iip_device_wrap.v` and `pcie_ep_cls.v`;
  `project.iridium.rtl` @ `d1462076095a`, `fullchip/rtl/a1_chip.v`;
  `ip.infra.rtl.shared.iridium` @ `9c296e568b62`,
  `fc_interrupts/rtl/fc_interrupt.v`; `ip.psoc.rtl` @ `953b9621a6f4`,
  `top/rtl/puc_intr_aggr.v` and `psoc_cpu_wrap/rtl/psoc_cpu_wrap.v`;
  `ip.armcpu.rtl` @ `63d85ff2a0ae`, `cpu_wrap/rtl/cpu_mng_slv_top.v`;
  `ip.infra.rtl` @ `5bb28e7f5a82`, `shared/plic/rtl/plic_top.v`.
  Neo's effective-build and MuC-instance qualifications are important.
- `ip.pcie.sw.fw.ep` @ `263383967de4`: `drivers/el_piw/el_piw_ecc.c:121`,
  `:144`, `:228`, `:279`, `:414`, `:465`;
  `drivers/el_ppw_ctrl/el_ppw_ctrl.c:317`, `:374`, `:531`;
  `drivers/el_piw/el_piw_spi_tree.c:280`,
  `drivers/el_piw/el_piw_sei_tree.c:1218`;
  `drivers/el_piw/Kconfig:206`, `drivers/el_ppw_ctrl/Kconfig:34`.
- `project.iridium.sw.fw.infra` @ `2ede1372d98b`:
  `soc/dts/a1/a1_hsi.dtsi:2716`, `:3016`;
  `soc/dts/a1/a1_psoc.dtsi:446`; `soc/dts/a1/a1_piw_irq_trees_enable_all.dtsi:1`;
  `include/a1_wire_interrupts.h:13`, `:689`;
  `boards/arm/a1/el_arm.dts:35`.
- `zephyr_apps/mng` @ `906ac8e0745b`: `boards/el_arm_a1_asic.overlay:1678`,
  `:1720`; `boards/el_arm_a1_palladium.overlay:641`, `:1113`;
  `boards/el_host_a1_palladium.overlay:54`;
  `boards/el_arm_a1_asic.conf:263`; `boards/el_host_a1_palladium.conf:113`.
- `ip.mng.sw.fw` @ `6845afac97ae`:
  `subsystems/el_error_handler/plugins/sram_ecc.c:68`, `:107`, `:164`, `:240`;
  `subsystems/el_error_handler/el_error_dispatch.c:80`, `:121`;
  `subsystems/el_ras/plugins/sram_ecc.c:634`;
  `subsystems/el_ras/Kconfig:11`.
- `ip.infra.sw.fw` @ `069af35b5069`:
  `drivers/el_mem_ecc/el_mem_ecc.c:72`, `:161`, `:220`, `:256`.
- Workspace `zephyr_apps/fla_runtime/boards/el_arm_a1_asic.conf:90` and
  `el_arm_a1_palladium.conf:61`: synchronous boot-check configuration.
- GitHub: `ip.pcie.sw.fw.ep` PRs #299 and #424;
  `project.iridium.sw.fw.infra` PR #906; `ip.mng.sw.fw` PR #319;
  `project.iridium.sw.fw.psoc_mng` PRs #115 and #258;
  `ip.infra.sw.fw` PR #818. Jira: `IPPCIESW-161`, `IPPCIESW-343`,
  `IPPCIESW-550`, `IPPCIESW-716`, `IPINFRASW-2043`; checked 2026-11-15.
