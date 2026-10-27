# C2C Traffic Generator Shell Semantics

- **Recorded:** 2026-10-25
- **Status:** Verified from Iridium FW source; hardware semantics not independently checked
- **Tags:** c2c, traffic generator, shell, tgen, timed run, idle

## Scope / Question
How the Iridium C2C shell handles traffic-generator start windows and the `time` option.

## Findings
- `el_c2c_traffic_gen_start_impl()` programs the selected direction's address window and writes its START register. `el_c2c_traffic_gen_get_status_impl()` reports each direction's IDLE bit.
- In the shell, periodic status checks, idle-direction re-arms, and final stop occur only when `time_sec > 0`. For `time_sec == 0`, firmware starts the requested window once and returns without re-arming or issuing a stop.
- The shell source comment and user documentation have described zero as both indefinite/manual-stop and one-window behavior. Recheck the current help/docs before relying on this as the intended user contract.

## Sources
- FW: `ip/se/drivers/c2c/c2c_traffic_gen.c` (`el_c2c_traffic_gen_start_impl`, `el_c2c_traffic_gen_get_status_impl`)
- FW: `ip/se/drivers/c2c/el_c2c_shell.c` (`c2c_tgen_rearm_idle`, `cmd_c2c_traffic_gen_start`)
- FW docs: `ip/se/docs/shell_commands.md` (auto and raw mode `time` descriptions)
