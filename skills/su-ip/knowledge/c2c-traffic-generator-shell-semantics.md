# C2C Traffic Generator Shell and Address Semantics

- **Recorded:** 2026-10-29
- **Status:** Verified from Iridium FW source; hardware semantics not independently checked
- **Tags:** c2c, traffic generator, shell, tgen, timed run, idle

## Scope / Question
How the Iridium C2C shell selects traffic-generator address windows and handles the `time` option.

## Findings
- `el_c2c_traffic_gen_start_impl()` programs the selected direction's address window and writes its START register. `el_c2c_traffic_gen_get_status_impl()` reports each direction's IDLE bit.
- In the shell, periodic status checks, idle-direction re-arms, and final stop occur only when `time_sec > 0`. For `time_sec == 0`, firmware starts the requested window once and returns without re-arming or issuing a stop.
- The shell source comment and user documentation have described zero as both indefinite/manual-stop and one-window behavior. Recheck the current help/docs before relying on this as the intended user contract.
- In auto mode, the active connection scheme selects a fixed peer; a scale-up port requires `chip` (with `tray` defaulting to the current tray). The target DPA is `EL_MEMORY_BASE | tray | chip | die_index`, at offset zero, and the window ends at `DPA + len`.
- The traffic generator issues that DPA; the egress `DPA_TO_NPA` map translates it. Auto mode checks the live `DPA_TO_NPA_ADDR_SEL` selects the topology's expected LUT slot, then prints the resolved DPA, modeled NPA, and slot index. `show c2c traffic_gen [port] [die]` reads back the programmed start/end window and idle state. Raw mode accepts explicit 64-bit DPA window halves.

## Sources
- FW: `ip/se/drivers/c2c/c2c_traffic_gen.c` (`el_c2c_traffic_gen_start_impl`, `el_c2c_traffic_gen_get_status_impl`)
- FW: `ip/se/drivers/c2c/el_c2c_shell.c` (`c2c_tgen_rearm_idle`, `cmd_c2c_traffic_gen_start`)
- FW: `ip/se/drivers/scale_engine/el_se_orch.c` (`el_se_get_port_peer_target`)
- FW API: `ip/se/include/el_se.h` (`el_se_peer_target`, `el_se_get_port_peer_target`)
- FW docs: `ip/se/docs/shell_commands.md` (auto and raw mode `time` descriptions)

## A2 delta (2026-11-11, via Neo)

- A2 still has traffic_gen in the master pipe (4 KB TRAFFIC_GEN.yml block); POR status unknown. See [c2c-a2-mng-counters-observability.md](c2c-a2-mng-counters-observability.md).
