# PCIe PIW SEI-to-KMD notifications in ARM ASIC

- **Recorded:** 2026-10-14
- **Status:** Verified from source and build artifact
- **Tags:** pcie, piw, sei, kmd, drain, mailbox, arm asic

## Scope / Question
Whether the inspected Iridium A1 ARM ASIC management image sends the PIW drain notification or other PCIe SEI events to KMD.

## Findings
- The A1 ARM ASIC board config explicitly sets `CONFIG_EL_PIW_DRAIN_NOTIFICATION=n`; the corresponding generated `mng_arm_asic` config also says the symbol is not set. Thus the legacy direct drain-to-EQ fallback is compiled out.
- PIW code has a generic host-notify path: when an error callback is registered, drain events go through `el_piw_sei_notify`; without a callback, the legacy direct path is guarded by `CONFIG_EL_PIW_DRAIN_NOTIFICATION`. `el_piw_sei_notify` returns `-ENODEV` when no callback is installed.
- The MNG plugin registers that callback and sends assembled messages over `EL_MBOX_EQ`, but its code is guarded by `DT_HAS_COMPAT_STATUS_OKAY(el_pcie_sei_error_handler)`. The inspected generated ARM ASIC DTS has no such enabled compatible node. Therefore this image does not deliver drain or other PIW SEI notifications through the plugin either.
- The generic source contains HOST_NOTIFY descriptors for response errors, AXI terminations, completion timeouts and pulse causes. The MNG plugin serializer handles `RESP_ERR`, `AXI_TERM`, `DRAIN_INTR`, `CPL_TIMEOUT`, and `PULSE`; it has no `AER` case, so AER captures currently fall through as unsupported rather than being sent by this plugin.
- `CONFIG_EL_PIW_IRQ_ACTIONS=y` in the generated artifact only enables the dispatcher/workers; it does not itself enable delivery to KMD.

## Sources
- Board configuration: `zephyr_apps/mng/boards/el_arm_a1_asic.conf` (`CONFIG_EL_PIW_DRAIN_NOTIFICATION=n`).
- Generated artifact configuration: `build_artifacts/include/fw/apps/mng/config/mng_arm_asic_a1_3_0_11_3p0_1389.config` (`CONFIG_EL_PIW_DRAIN_NOTIFICATION` unset; `CONFIG_EL_PIW_IRQ_ACTIONS=y`).
- PIW driver: `ip/pcie/ep/drivers/el_piw/el_piw.c` (`el_piw_sei_notify`, `el_piw_port_drain_notify_cause`).
- PIW SEI host-notify descriptors: `ip/pcie/ep/drivers/el_piw/el_piw_sei_tree.c`.
- MNG plugin: `ip/mng/subsystems/el_error_handler/plugins/pcie_sei.c` (callback registration, message serialization, EQ mailbox send).
- Plugin binding: `ip/mng/dts/bindings/el_middleware/el,pcie-sei-error-handler.yaml`.
- Generated DTS: `build_artifacts/include/fw/apps/mng/dts/mng_arm_asic_a1_3_0_11_3p0_1389.dts` (no `el,pcie-sei-error-handler` node).
