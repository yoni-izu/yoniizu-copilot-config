# SE/C2C shell command map + `diag se` aggregator

- **Recorded:** 2026-10-13
- **Status:** Verified from FW source + build (`octl ws build zephyr_apps/mng --args "platform=host variant=asic"`, `octl ws lint ip/se`)
- **Tags:** se, c2c, scale engine, shell, diag, diag se, diag pcie_ep, el_diag_shell, EL_DIAG_SHELL_REGISTER, EL_SE_DIAG, EL_SE_SHELL, EL_C2C_SHELL, EL_SE_SV, EL_C2C_SV, ltssm logger, ras des, event counters, enable encoding, bug report, snapshot, dump wrapper

## Scope / Question

Full inventory of the SE / C2C runtime shell commands, which are safe to replay
unattended, and how the `diag se` one-shot diagnostics command is wired.

## Findings

### The diag framework (`fw/infra/include/el_diag_shell.h`)

```c
#define EL_DIAG_SHELL_REGISTER(ip_name, handler)                                   \
	IF_ENABLED(CONFIG_EL_DIAG_SHELL,                                           \
		   (SHELL_SUBCMD_ADD((diag), ip_name, NULL,                        \
				     "Run diagnostics for " #ip_name, handler, 1, 0)))
```

Contract: the handler is invoked with **`argc == 1` and no optional args**
(`SHELL_SUBCMD_ADD(..., 1, 0)`). `CONFIG_EL_DIAG_SHELL` lives in
`fw/infra/el_shell/Kconfig.el_diag_shell` (`default y`, `depends on EL_SHELL`).

### The established pattern (from `diag pcie_ep`)

`fw/ip/pcie/ep/drivers/el_pcie_ep/el_pcie_ep_diag.c` is the template:

1. Each block shell exports thin argument-free wrappers
   `int <x>_shell_<y>_dump(const struct shell *sh) { return cmd_<y>(sh, 1, NULL); }`
   declared in a shell-only public header (kept out of the core driver header so
   the driver API does not pull in shell concepts).
2. The diag file holds a `static const struct { const char *name;
   int (*dump)(const struct shell *sh); } sections[]` table, prints
   `"=== <IP>: %s ==="` per section, `shell_warn`s and **continues** on a
   non-zero return, and returns the first error.
3. A dedicated `CONFIG_<IP>_DIAG` Kconfig (`depends on EL_DIAG_SHELL && <block shells>`,
   `default y`) + `zephyr_library_sources_ifdef(CONFIG_<IP>_DIAG <ip>_diag.c)`.

Key safety rule: passing `argv == NULL` is only legal because none of the
selected handlers touch `argv` when `argc == 1`. **Verify per handler** — there
is no `argv[0]` use anywhere in the SE/C2C shells, so it is safe there.

### SE/C2C shell command inventory (A1; verify the deployed revision)

Production SE shell — `CONFIG_EL_SE_SHELL`, `fw/ip/se/drivers/scale_engine/el_se_shell.c`:

| Command | Handler | In `diag se` |
|---|---|---|
| `show se health [monitor] [die]` | `cmd_se_health` | yes |
| `show se link_control <flat_port>` | `cmd_se_link_control_status` | no — requires a port |
| `show se bar [die]` | `cmd_se_bar` | yes |
| `show se ec all [lanes] [port] [die]` | `cmd_se_ec_all` | yes, with a counter-enable side effect |
| `show se ec group <group> [lanes] [port] [die]` | `cmd_se_ec_group` | no — covered by `ec all` |
| `show se topology` | `cmd_se_topology_show` | yes |
| `show se lut [die]` (`#if CONFIG_EL_C2C_SHELL`) | `cmd_se_lut_dump` | yes |
| `show se phyfw_verify [iccm\|dccm]` | `cmd_se_phyfw_verify` | no — full ICCM+DCCM readback per port per die |
| `show se monitor_ltssm [port] [die]` | `cmd_se_monitor_ltssm` | no — loops until keypress |
| `show se fiu_oam_table <port> <die>` (`#if CONFIG_EL_FIU_MGR`) | `cmd_se_fiu_oam_table` | no — **dereferences `argv[1]`/`argv[2]`**, 32 entries × ports × dies |
| `set se ltssm / ltssm_en / link_control / role / link_speed / retrain / speed_change / remote_req_id / ownership / conn_scheme / mode b2b / lut {bar,reqid,commit}` | `cmd_se_set_*`, `cmd_se_lut_set_*` | no — mutating |

### DM RAS DES counters (A1, verified 2026-11-03)

In the management Zephyr shell, `show se ec all` dumps groups 0-7 for every
connected port on every die. The optional filters are positional, in **lanes,
physical port, die** order. `lanes` is a count (1-8, default 8), starting at
lane 0, and applies to per-lane groups 0 and 4. A1 SE links are x4: use `4`
for physical lanes 0-3. The shell's default of 8 is a software limit, not the
physical lane count.

```text
show se ec all 4 7       # port 7 on all dies
show se ec all 4 7 0     # port 7 on die 0
show se ec group 2 4 7 0 # group 2, port 7, die 0
```

The production commands require `CONFIG_EL_SE_SHELL`. On an SV-enabled image,
`el_se_sv ec_print_all [lanes] [die] [port]` is an alternative; its die and
port argument order is reversed (for example, `el_se_sv ec_print_all 4 0 7`).
The selected connection supplies RP/DSP vs EP/USP role. The DM API guard
rejects preserved ports and, when ownership gating is enabled, MC-owned ports.

The dump does **not** clear counts, but it is not strictly read-only:
`dm_ec_read_counter` writes the event/lane selector and `ALL_ON` (0x7) before
each data read. Thus it enables all counters, including when invoked by
`diag se`, and does not restore a previous disabled state. The `set se ec clear`
command clears all counters and also enables counting.

In `ip.se.sw.fw` checkout `dev/akshay.bharad/se-lut-o-bdf` at `a26c0c870e41`,
`set se ec enable off` writes enable code `0x0` and reports success, but the
DM reference specifies that `0x0` causes no change and `0x5` is `ALL_OFF`.
Do not rely on that shell command to disable counting in this revision. This
is a code-versus-manual finding; it has not been tested on hardware.

Sources: `ip.se.sw.fw` `drivers/scale_engine/el_se_shell.c:1879,2052,2213`,
`drivers/scale_engine/el_se_shell_sv.c:998,4576`,
`drivers/scale_engine/el_se_diag.c:28`, `drivers/snps_dm_ctl/dwc_dm_ctl.c:1587`,
`include/dwc_dm_ctl.h:168`, `drivers/scale_engine/Kconfig:157,183`, and
`docs/shell_commands.md:50`; Synopsys DM databook `~/docs/C2C/DWC_pcie_ctl_dm_databook.pdf`
pp. 809-810 and DM register reference `~/docs/C2C/DWC_pcie_ctl_reference.pdf`
pp. 2655-2658 (enable encoding also verified on pp. 4688 and 6435). See also
`link-width-configuration.md` for the A1 controller's x4 width.

### SE link down/up (A1, verified 2026-11-03)

For a firmware-owned, non-preserved port, the shell controls the LTSSM using
physical-port then die numbering:

```text
set se ltssm off 2 0
set se ltssm on 2 0
show se health 0
```

Omitting the die applies to that port on every die; omitting the port applies
to all ports on all dies. The handler prints per-port errors or skips but
returns success after iterating, so inspect its output and the link status.

For an MC-owned port, `set se ltssm_en off <flat_port>` and
`set se ltssm_en on <flat_port>` control APP_LTSSM_EN on either RP or EP.
`show se link_control <flat_port>` reports the observed state. The flat index
is `die * ports_per_die + physical_port`; the devicetree value defaults to 8
ports per die. For an RP, `set se link_control dis <flat_port>` / `en` instead
controls the independent standard PCIe Link Disable bit. Both holds must permit
training for the link to come up.

Both flat-port setters require MC ownership. Their optional `override_ownership`
temporarily acquires firmware-owned ports, but acquiring ownership disables
port interrupts and clears its FLR re-init request; releasing ownership does
not restore those or replay configuration. Quiesce traffic before disabling:
deasserting APP_LTSSM_EN outside Detect forces Detect/reset and may violate PIPE
timing. Enabling permits training but does not guarantee link-up.

Sources: `ip.se.sw.fw` `drivers/scale_engine/el_se_shell.c:622,1499,1547,2157,2250`,
`docs/shell_commands.md:102,125`, `README.md:75`; `module.infra.sw.fw`
`include/el_glob_params.h:36`. Hardware caveat: SNPS DM databook v6.42a-lca01
section 2.4, PDF page 106.

LTSSM logger — same Kconfig, `el_se_ltssm_logger_shell.c`:

| Command | Handler | In `diag se` |
|---|---|---|
| `show se ltssm_logger status [die] [port]` | `cmd_ltssm_logger_status` | yes |
| `show se ltssm_logger print [die] [port]` | `cmd_ltssm_logger_print` | yes (static entry buffer — no stack blow-up) |
| `set se ltssm_logger enable/clear/halt_on_full/mask/threshold` | `cmd_ltssm_logger_*` | no — `clear` would destroy the evidence |

`ltssm_logger_parse_filters(sh, argc, argv, first=1, ...)` is NULL-safe at `argc == 1`.

Production C2C shell — `CONFIG_EL_C2C_SHELL`, `fw/ip/se/drivers/c2c/el_c2c_shell.c`:

| Command | Handler | In `diag se` |
|---|---|---|
| `show c2c counters all [monitor] [die]` | `cmd_c2c_counters_all` | yes |
| `show c2c counters port [monitor] [port] [die]` | `cmd_c2c_counters_show` | no — superseded by `counters all` |
| `show c2c ebr [port] [die]` | `cmd_c2c_ebr_show` | yes |
| `show c2c traffic_gen [port] [die]` | `cmd_c2c_traffic_gen_status` | yes — catches a tgen left running |
| `set c2c rate_limit / compression / pos / ebr / abort_discard / counters reset / traffic_gen start,stop` | various | no — mutating (`counters reset` erases what diag just captured) |
| `set c2c tlp inject …`, `tlp capture …` (`CONFIG_EL_C2C_TLP_GEN`) | `cmd_c2c_tlp_*` | no — generates traffic; `capture poll` blocks on timeout |
| `set c2c irq inject …`, `set c2c irq test` (`CONFIG_EL_C2C_IRQ_TEST`) | — | no — deliberately raises interrupts |

SV-only shells are **all excluded** (not compiled into production images, same
precedent as `diag pcie_ep`): `el_se_shell_sv.c` (`CONFIG_EL_SE_SV`, ~4300 lines
— AER, error counters, DBI r/w, ASPM, FLIT einj/log/perf, MPS, L0p, einj6),
`el_c2c_shell_sv.c` (`CONFIG_EL_C2C_SV` — credit_status, rl_prime/measure,
wr_coal, irq_inject/clear), `dwc_dm_ctl_shell_sv.c`.

### What was added

- `fw/ip/se/drivers/scale_engine/el_se_diag.c` — 9-section aggregator,
  `EL_DIAG_SHELL_REGISTER(se, cmd_se_diag)`.
- `fw/ip/se/include/el_se_shell.h` (new) + additions to `el_c2c_shell.h` —
  wrapper declarations.
- `CONFIG_EL_SE_DIAG` (`depends on EL_DIAG_SHELL && EL_SE_SHELL`, `default y`);
  the four C2C sections are `#if defined(CONFIG_EL_C2C_SHELL)`-guarded rather
  than a hard Kconfig dependency, so the command still works on an SE-only image.

### Build/verification gotchas (A1 workspace)

- `octl ws build ip/se` **skips the compile** — it prints
  `SKIP build_sanity (covered by downstream apps)`. Force it with
  `--args "sanity=true"`, but note the sanity config does **not** set
  `CONFIG_EL_SE=y`, so SE shell sources are still not compiled.
- To actually compile SE shell/diag code, build a consuming app:
  `octl ws build zephyr_apps/mng --args "platform=host variant=asic"`
  (target `el_host_a1/native/asic`, ~4 min). Boards that set `CONFIG_EL_SE=y`
  are listed under `zephyr_apps/mng/boards/` and `zephyr_apps/sve/basedon_app/`.
  `--args "board=el_host_a1_asic"` does **not** match — the board string is
  `el_host_a1/native/asic`; filter with `platform=` + `variant=` instead.
- `octl ws lint ip/se` runs checkpatch against the **last commit**, not the
  working tree — check uncommitted work with
  `clang-format --style=file <f> | diff -u <f> -` (`fw/ip/se/.clang-format`,
  `ColumnLimit: 100`, tabs). There is no `.clang-format` at `fw/ip/` level.
- `octl` must be run from the workspace root `…/fw`, not its parent, or it
  resolves the wrong manifest and fails with
  `stat ~/.repo/manifests/projects/iridium/<other>-fw-ci.xml: no such file`.

## Sources

- FW: `module.infra.sw.fw` → `fw/infra/include/el_diag_shell.h`, `fw/infra/el_shell/Kconfig.el_diag_shell`
- FW: `ip.pcie.sw.fw.ep` → `drivers/el_pcie_ep/el_pcie_ep_diag.c`, `drivers/el_pcie_ep/Kconfig`, `drivers/snps_ep_ctl/dwc_ep_ctl_shell.c`, `drivers/el_piw/el_piw_shell.c`
- FW: `ip.se.sw.fw` → `drivers/scale_engine/{el_se_shell.c,el_se_ltssm_logger_shell.c,el_se_shell_sv.c,Kconfig,CMakeLists.txt}`, `drivers/c2c/{el_c2c_shell.c,el_c2c_shell_sv.c,Kconfig}`, `include/{el_se_shell.h,el_c2c_shell.h}`, `docs/shell_commands.md`
