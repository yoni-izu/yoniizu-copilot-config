# `fw_ctl` and `fw_loader.py` — Wrapper Layer

Two wrappers sit on top of `fwloader`. Prefer these over the raw binary for
normal lab work — they resolve the correct image, provide reset/status verbs,
and pin the variant so `flash`/`stream`/ARM-FW-load all use the **same build**.

- **`fw_ctl`** — the operator-facing lab bring-up console/CLI (interactive REPL
  **and** one-shot CLI, one shared command registry). Best for humans in the lab.
  It wraps **both** delivery layers: the local `fwloader` (`flash`/`stream`) and
  the **BMC** flashing path (`bmc …`), so one console covers host-side and
  out-of-band flashing.
- **`fw_loader.py`** — a thin, stdlib Python wrapper used by scripts / CI /
  regressions. Best for automation.

Source: `infra/scripts/fw_ctl/` and `infra/scripts/fw_loader.py` (repo
`module.infra.sw.fw` / `ws-psoc-fw-ci`). The live command reference is
`fw_ctl/README.md` and `flash help` / `stream help` inside the console (which
surface `fwloader --help`).

## fw_ctl — setup & launch

```bash
# Provision a per-user venv + wheels + BOARD_CONFIG_PATH (pick your target):
source /opt/atl/fw/scripts/fw_ctl/setup_fw_ctl_env.sh asic       # silicon
source /opt/atl/fw/scripts/fw_ctl/setup_fw_ctl_env.sh onix       # FPGA
source /opt/atl/fw/scripts/fw_ctl/setup_fw_ctl_env.sh pcie-only  # no board

cd /opt/atl/fw/scripts        # so packages import cleanly

# Interactive console
python3 -m fw_ctl.console --variant asic --interface pcie

# One-shot CLI (scriptable; stdlib-only skin)
python3 -m fw_ctl.cli --variant asic --interface pcie <verb …>

# Star launcher (provisions the container for you)
octl ws exec run_fw_ctl --args 'variant=asic interface=pcie'
octl ws exec run_fw_ctl --args 'variant=asic skin=cli cmd="flash write"'
```

**Variant is auto-discovered from the hostname** (operators rarely pass
`--variant`); `--variant` overrides. See the project reference for the
hostname→variant mapping. `--interface {pcie|i2c|spi|jtag}` selects the
**register/debug** bus (default `pcie`); it is **independent** of the flash/stream
**delivery** transport, which you pass on the verb (`--transport`).

## fw_ctl — flash & stream verbs

| Verb | Args | Notes |
|------|------|-------|
| `flash write` | `[<image>] [--offset <o>] [--force] [--transport {pcie\|i2c\|ftdi-spi}] [selectors]` | Direct SPI-NOR write. **No image = auto-resolve the build for the active target** (see below). |
| `flash read` | `<output> [--size <n>] [--offset <o>] [--transport …]` | Read flash to a file (use for byte-verify). |
| `flash erase` | `[--transport …]` | Chip erase. |
| `flash write-emul` | `[<image>] [--force] [--transport …]` | eFuse-**emulation** partition image → all emul slots; written with `no_clear_fw_load_done` so a booted device isn't disturbed. Not a per-variant build artifact. |
| `stream` | `[<image>] [--retries <n>] [--mbx-addr <a>] [--mbx-size <s>] [--transport …] [--device <bdf>] [selectors]` | Deliver a `.fwpkg` via streaming boot. No image = auto-resolve. Thin pass-through to fwloader. |
| `flash help` / `stream help` | — | Surface the live `fwloader --help`. |
| `ftdi list` | — | `fwloader --ftdi-list`. |

### Image auto-resolution (this is your anti-"wrong-file" lever)

A bare `flash write` / `stream` (no path) resolves the image **built for the
active target** from the `device_fw` tree — it does **not** grab whatever `.bin`
is in `cwd`:

```
flash:  <device_fw_root>/arm/<token>/flash_bf/spi_flash_image_arm_<token>.bin
stream: <device_fw_root>/arm/<token>/stream_bf/pldm_fwu_<token>.fwpkg
```

- `token = <variant>[_<revision>]`.
- **`EL_FW_VARIANT` wins**, else the lab variant from the hostname
  (`fpga`→`onix`, `asic`→`asic`).
- **`EL_FW_REV`** optionally qualifies the variant into a specific build.
- One `export EL_FW_VARIANT=… ; export EL_FW_REV=…` pins **`flash write`,
  `stream`, and `load_arm_fw` to the same build** — this is how you avoid an ASIC
  host silently programming an onix image just because it existed first.
- Pass an explicit absolute path to bypass all resolution:
  `flash write /abs/path/image.bin`.

## fw_ctl — verification / boot / reset verbs

| Verb | Purpose |
|------|---------|
| `host_lib rom-status version` | ROM/FW **version banner** — read the flashed build ID back. |
| `host_lib rom-status all` | Combined banner + boot-progress bits + decoded status (the bring-up evidence log). |
| `host_lib rom-status progress` / `status` | Individual sections. |
| `host_lib boot fw-sts` | Aggregate `fw_boot_sts`: `0 not_started · 1 in_progress · 2 done`. |
| `host_lib boot status` | Per-component boot status. |
| `host_lib boot check <component>` | Test one component's boot bit (e.g. `fla_runtime`). |
| `host_lib boot wait <component> [--timeout <s>]` | Block until a component boots (bounded). |
| `host_lib boot clear <config\|progress\|fw-load-done\|component-sts\|all> [--force]` | Clear boot state; `all` is destructive → needs `--force`. |
| `host_lib sysreset {soft\|hard\|platform}` | Reset the SoC. |
| `host_lib sysreset status` | Last reset type/cause. |
| `host_lib reset unit <component>` / `reset range <s> <e>` | Targeted block reset. |
| `rpe cpld …` (e.g. power cycle) | CPLD-driven power/reset/strap/mux (board variants only — hidden on pldm). Recovery lever of last resort. |

`--simulate` runs with no hardware (dummy reads `0xDEADC0DE`) — useful to dry-run
a script; **never** a substitute for real read-back verification.

## fw_ctl — BMC verbs (flash a whole sled out-of-band)

`fw_ctl` also **wraps the BMC flashing tool**: the `bmc …` verbs drive the sled
BMC remotely over Redfish, so you can flash **every device on a sled from the
fw_ctl console** without a PCIe link or a lab-host login.

```bash
export BMC_HOST="your-bmc-host"
ctl:pcie:asic> bmc load ./spi_flash_image_arm_asic.bin --boot --expect 8
```

`bmc load <image>` uploads the image and programs **all chips on the sled in one
command** (there is no per-chip load); `--boot` then clears boot state, platform-
resets, and proves every chip reaches `fw_boot_sts = done`. Companion verbs:
`bmc chips`, `bmc reset`, `bmc tasks`, `bmc task <id>`, `bmc info`.

Full semantics, task-queue constraints, cache traps and failure modes:
[bmc-redfish.md](./bmc-redfish.md). When you already have a shell **on** the BMC,
prefer `bmccli flash update <img> -d <device|all>` instead.

## fw_loader.py — scripting / CI wrapper

Stdlib argparse CLI + a Python API (`FwLoader`). Subcommands:

```
list  stream  flash-write  flash-read  flash-erase  xmodem
eeprom-read  eeprom-write  version  ftdi-list
```

```bash
python3 fw_loader.py flash-write -i spi_flash_image.bin -f
python3 fw_loader.py flash-read  -o dump.bin -s 0x1000 -O 0x80000
python3 fw_loader.py stream -i pldm_fwu_onix.fwpkg          # --reset available
python3 fw_loader.py version
```

Python API (used in fixtures/regressions):

```python
fw.stream("pldm_fwu_onix.fwpkg", reset=True)   # streaming boot (PCIe)
fw.flash(FwLoadTarget.HOST, device_idx=0)      # flash the configured default
```

The CI/regression stages call this (e.g. `stages/flash_emul.py`, `load_arm_fw`)
via `octl ws exec` / Starlark, dispatched on the GitHub Actions runner fleet.
The version stamped into the artifact is the one CI resolves — always the live
build, so pin it via the same `EL_FW_*` overrides.
