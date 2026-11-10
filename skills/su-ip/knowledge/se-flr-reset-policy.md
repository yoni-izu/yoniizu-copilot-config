# SE FLR reset policy — how an FLR reaches the C2C ports and who arms the SE units

- **Recorded:** 2026-11-04
- **Status:** Verified from A1 FW source (ip/se, ip/psoc el_reset, proj_infra DTS) + PSOC Specification Rev1.2.0 (§Reset flow, §FLR); builds verified (mng arm/asic both policies, sve_host host/asic)
- **RTL branch/tag:** n/a (FW workspace `a1_power_on_0p5p231`, ip/se `iridium_release_0.5.231` + local change)
- **Tags:** flr, function level reset, reset mask, unit_rst_en, el_enable_reset, el_reset_config_comp_reset_mask_die, pre_cfg, reset isr, start delay, start_dly, psoc keep alive, reboot, el_se_reinit, el_se_orch_setup_flr_reset, el_se_flr_reset_policy, all ports, down ports, reinit_requested, preserved port, scale engine, se, a1

## Scope / Question
"Should the FW reset C2C on every FLR, and is setting the FLR reset mask enough (does the reboot
re-initialise the ports)?" — the design facts behind the `EL_SE_FLR_RESET_POLICY` Kconfig choice
introduced in `ip/se` (ALL_PORTS default / DOWN_PORTS / NO_PORTS).

## Findings

### How an FLR executes on A1 (reset manager + FW)
- **HW-autonomous reset, FW gets a window.** PSOC spec Rev1.2.0 §FLR: "PF FLR - can be configured
  to trigger hardware reset. In addition, it generates an interrupt to the firmware." §Reset flow:
  reset accepted → RST_SRC saved → early notification → FIU isolation → configurable wait → gradual
  per-block assert (per the unit-enable CSRs) → de-assert → optional `Repeat_Boot_Flow[type]`.
- **FW side** (`ip/psoc/drivers/el_reset/el_reset_isr.c` `el_reset_flr_reset_isr`): capture true
  cause, MSI-X doorbell, then **unless FLR is in the reset node's `psoc-keep-alive-types`**:
  `el_device_state_notify_reset(true)` → `el_reset_pre_reset(FLR, die)` per die (= the
  `EL_RESET_PRE_CFG` callbacks, **ISR context**) → `el_reset_halt_early_engines()` →
  `el_reset_cm7_halt()`. The HW then applies the FLR `UNIT_RST_EN` mask after `start-delay`
  (`BOOT_AND_RST_TOP_RST_MISC_START_DLY`, A1 DTS `start-delay = <0xffff>` cycles,
  `proj_infra/soc/dts/a1/a1_psoc.dtsi`).
- **FLR reboots the MuC on A1**: no A1 DTS/overlay sets `psoc-keep-alive-types` or
  `masked-reset-types` (only `start-delay` and `prstn-decouple-su` are set), so the keep-PSOC flow
  is not taken; the whole MANAGEMENT image re-runs, including SE init.
- **The SE units are disarmed at every boot**: `el_enable_reset()` (`el_reset.c`, called only
  from the reset driver init) clears SE_0..SE_7 in `UNIT_RST_EN` of **every reset type on every
  die** ("Resetting an SE would force the entire rack to go through a reset sequence"). Hence any
  SE arming is per-boot and must be redone after each reboot. `el_reset_config_comp_reset_mask_die
  (type, comp, die, enable)` RMWs the single bit (short IRQ lock only → ISR-safe).
- **Read-back**: `el_reset_is_component_reset(type, die, comp)` (syscall) returns the live bit —
  usable from tests to assert the policy took effect.

### Why "set the FLR mask and that's it" is sufficient
After the FLR reboot, `el_se_port_classify()` (`el_se_reinit.c`) finds the PHY CPU of a cold-reset
port halted (`el_dmp_ctrl_cpu_running` → false) → the port is `UNINITIALIZED` → ordinary full
bring-up (PHY FW load, DM config, C2C/LUT, LTSSM). No post-FLR re-init code exists or is needed;
this is the same path the earlier down-ports-only design relied on. Caveat: if FLR were ever put
into `psoc-keep-alive-types`, reset ports would **not** be re-initialised in place (no POST_CFG
handler in SE).

### The policy choice (`ip/se/drivers/scale_engine/Kconfig`, `choice EL_SE_FLR_RESET_POLICY`, depends on EL_RESET)
| Symbol | Mechanism | Owned ports | `el_se_set_port_reinit()` |
|---|---|---|---|
| `EL_SE_FLR_RESET_ALL_PORTS` (**default**) | `el_se_orch_setup_flr_reset()` arms SE_0..SE_7 of every SE die **once at SE init**, right after `el_se_orch_check_dies()` (before anything else in init can fail). Static HW mask → works even if the MuC is hung when the host FLRs. No reset callback. | reset too (every physical port, C2C node or not) | `-ENOTSUP` |
| `EL_SE_FLR_RESET_DOWN_PORTS` | per-die `EL_RESET_PRE_CFG` callback arms exactly `reinit_requested & ~owned & has-C2C-dev` at FLR time (ISR). | never reset | supported |
| `EL_SE_FLR_RESET_NO_PORTS` / `EL_RESET=n` | nothing armed | — | `-ENOTSUP` |
- Hidden `EL_SE_FLR_RESET = ALL || DOWN` gates the shared arming code.
- `reinit_requested` link-health tracking is kept under every policy (it feeds `show se port_state`,
  which now prints the active policy line); it only drives the FLR under DOWN_PORTS.
- Consequence of ALL: every FLR drops all scale-up links of the chip → peers see Surprise Down
  (`dm-surprise-down-reporting.md`) and retrain after the reboot.
- SV test `test_se_flr_mask_all_ports` (`tests/reinit_tests_sv.c`) asserts the mask via
  `el_reset_is_component_reset()`; it compiles in `sve_host` host/asic (has `EL_RESET` +
  `EL_SE_SV` + `EL_PCIE_IF`).

### Related
- `se-unit-reset-fanout.md` — what the SE unit reset physically resets (C2C + DM + DMP/PHY).
- `drop-mode-error-containment.md` — `el_reset_request_scale_up_reset()` (PLATFORM-type variant
  of the same arming, all dies).

## Sources
- FW: `ip/psoc/drivers/el_reset/el_reset_isr.c` (`el_reset_flr_reset_isr`, `EL_RESET_KEEPS_PSOC`),
  `el_reset.c` (`el_enable_reset`, `start_delay`), `el_reset_api.c` (`el_pre_reset`,
  `el_reset_issue_scale_up_reset`), `infra/include/el_reset_common.h`
  (`el_reset_config_comp_reset_mask_die`, `el_reset_is_component_reset`, callback contract),
  `proj_infra/soc/dts/a1/a1_psoc.dtsi` (reset node), `ip/psoc/dts/bindings/reset/el,reset.yaml`.
- FW: `ip/se/drivers/scale_engine/el_se_reinit.c`, `el_se_orch.c` (`el_se_orch_init`),
  `Kconfig`, `docs/port_ownership_model.md` ("FLR policy").
- Spec: `~/docs/PSoC/PSOC Specification Rev1.2.0.docx` §Reset flow (P1440–P1460), §FLR (P1505–P1509).
