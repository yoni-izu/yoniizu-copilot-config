# SE FLR reset policy — how an FLR reaches the C2C ports and who arms the SE units

- **Recorded:** 2026-11-04 · **Corrected:** 2026-11-19
- **Status:** Reset-flow facts verified from A1 FW + PSOC spec Rev1.2.0. The policy section was rewritten on 2026-11-19 from `ip/se` `main` (`f63b2df`) plus the change `4541021` (branch `dev/yoniizu/se-flr-reset-all-default`, PR ip.se.sw.fw#313 with ip.mng.sw.fw#547, IPSESW-509; not yet merged). VM-verified 2026-11-19: `octl ws unit-test ip/se` 5/5 suites pass, ip/mng 6/6 suites pass, and `zephyr_apps/mng` builds. `ip/mng/services/host_management/tests/` is not wired into any build, so its tests do not run. The 2026-11-04 description of an `EL_SE_FLR_RESET_POLICY` choice was wrong: that choice does not exist on `main`.
- **RTL branch/tag:** n/a (FW only)
- **Tags:** flr, function level reset, reset mask, unit_rst_en, el_enable_reset, pre_cfg, reset isr, start delay, psoc keep alive, reboot, el_se_reinit, flr_reset_all_ports, el_se_set_flr_reset_all_ports, EL_SE_FLR_RESET_DOWN_PORTS, EL_SE_FLR_RESET_ALL_PORTS_DEFAULT, set_host_config, c2c_reset_during_flr, kmd, all ports, marked ports, reinit_requested, owned port, scale engine, se, a1

## Scope / Question
"Should the FW reset C2C on every FLR, and which ports?" — the reset-flow facts, plus the FW policy (reset all non-owned ports vs only marked ones, and who selects it).

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
this path is shared by both modes. Caveat: if FLR were ever put
into `psoc-keep-alive-types`, reset ports would **not** be re-initialised in place (no POST_CFG
handler in SE).

### The policy (runtime flag + Kconfig default + host override)
- **Gate:** `CONFIG_EL_SE_FLR_RESET_DOWN_PORTS` (`ip/se/drivers/scale_engine/Kconfig:274`) builds the FLR `EL_RESET_PRE_CFG` callback. In the ISR, the callback arms the SE units of the selected ports (`el_se_reinit.c`, around line 273). When the option is off, nothing is armed, and `el_se_set_flr_reset_all_ports(true)` returns `-ENOTSUP` (`el_se_reinit.c:239-244`).
- **Mode:** the RAM atomic `flr_reset_all_ports` (`el_se_reinit.c:222`):
  - **reset-all:** every FLR resets every non-owned port.
  - **marked-only:** only ports with `reinit_requested` set and not owned are reset.
  - Ports owned by the system manager are **never** reset, in either mode.
  - Marks matter only in marked-only mode, or on a platform reset.
- **Boot default:** `CONFIG_EL_SE_FLR_RESET_ALL_PORTS_DEFAULT` (`Kconfig:292`; bool, depends on DOWN_PORTS, **default y** since `4763db1`) sets the initial value of the atomic. Before that change the initial value was 0 (marked-only). `show se port_state` labels the current mode "(default)" when it matches the knob (`el_se_reinit.c:209`).
- **Runtime/host override:** `el_se_set_flr_reset_all_ports()` / `el_se_get_flr_reset_all_ports()` (`include/el_se.h:278/287`). ip/mng's PSOC MNG `SET_HOST_CONFIG` handler applies the `C2C_RESET_DURING_FLR` bit **unconditionally**, whether set or clear (`ip/mng/services/host_management/el_host_management.c:761-768, 798`). The KMD sends `SET_HOST_CONFIG` on every PSOC channel open, so a KMD with `c2c_reset_during_flr=0` reverts to marked-only. The Kconfig default therefore only governs until the first host config arrives, or when no KMD is present.
- **Consequence of reset-all:** every FLR drops all non-owned scale-up links of the chip. Peers see a Surprise Down (`dm-surprise-down-reporting.md`) and retrain after the reboot.

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
- FW: `ip/se/drivers/scale_engine/el_se_reinit.c`, `Kconfig`, `include/el_se.h`,
  `docs/port_ownership_model.md` ("FLR mode: reset-all or marked-only");
  `ip/mng/services/host_management/el_host_management.c` (host-config handler).
- Historical secondary source: `PSOC Specification Rev1.2.0.docx` §Reset flow (P1440–P1460), §FLR (P1505–P1509); exact A1 Git original unverified. Legacy SoC/PRM paths in the source map provide context, not that missing original.

## A2 delta (2026-11-11, via Neo)

- A2: no FLR in the integration RTL; PERST_N is per C2C (SW-owned); resets are per link; hot reset is detected via SPI. See [se-a2-reset-perst-hot-reset.md](se-a2-reset-perst-hot-reset.md).
