---
name: fw-flasher
description: "Flash firmware onto SoC devices from lab hosts (servers/desktops) and the BMC — safely, with anti-stale / anti-false-flash verification. Use when: flashing FW to a device, writing a SPI-flash image, streaming boot, recovering a bricked board, choosing the right transport (PCIe / I2C-debugger / FTDI-SPI / BMC MTD), picking the correct image/build, or confirming which FW version is actually running after a flash. Wraps fwloader (the core tool) and fw_ctl (the lab console), plus fw_loader.py. Keywords: flash firmware, fwloader, fw_ctl, fw_loader.py, flash write, stream boot, .fwpkg, SPI flash image, spi_flash_image, MTD, /dev/mtd, sboot-fsm, FW_LOAD_DONE, fw_boot_sts, rom-status, FPT, BKC, build ID, stale image, verify flashed version, PCIe BAR0, I2C debugger, ftdi-spi, XMODEM recovery, brick recovery. Scope: lab silicon (asic/fpga) + BMC. NOT emulation/PLDM."
argument-hint: "What you want to flash and where — e.g. 'flash the released FLA runtime to a1-r3-d2 over PCIe' or 'reflash the BMC MTD on the ELS tray and verify the version'"
---

# FW Flasher — Safe Firmware Flashing to Lab Devices

Flash firmware onto SoC devices from a **lab host** (server/desktop) or the
**BMC**, using the project's own tooling, with mandatory safeguards so you never
flash a **stale**, **wrong-variant**, or **partially-written** image, and you
always **confirm the version that is actually running** afterward.

> **Scope:** lab **silicon** (`asic`) and the **FPGA prototype** (`fpga`/onix),
> plus **BMC** out-of-band flashing. This skill deliberately does **not** cover
> emulation / Palladium (PLDM) — for that, use the `pldm` skill.
>
> **Projects:** only **Iridium** exists today, but this skill is
> **project-agnostic**. All project-specific facts (image locations, build-ID
> format, hostnames, variants, PCIe rev IDs, recovery) live in
> [references/projects/](./references/projects/). Adding a new project = drop in
> one `references/projects/<project>.md` file; the workflow below does not change.

## The Golden Rule

**Every flash is a three-part transaction — never skip a part:**

1. **KNOW the image** — exact path, build ID, provenance (released BKC vs local
   build vs TEST candidate), and a checksum. Never flash an image you cannot
   identify.
2. **FLASH it** — with the right tool/transport, into a **flash-idle** SoC.
3. **VERIFY it** — read the version back **and** boot it. A flash is not "done"
   until the device reports the **expected** version and reaches boot `done`.

If you cannot complete part 3, treat the flash as **failed**, not "probably fine".

## Step 0 — Identify the project and load its reference

Determine the project (today: **Iridium**) from the workspace / hostname / the
user, then read the matching project reference **before** doing anything else:

- Iridium → [references/projects/iridium.md](./references/projects/iridium.md)

That file is the source of truth for: where released images live, the build-ID
format, variant/token mapping, host/board naming, PCIe revision IDs, environment
overrides, and recovery commands. **Do not hardcode any of those here.**

## Step 1 — Choose the method (decision tree)

There are three families of tooling. Prefer the highest-level one that fits:

```
Are you flashing out-of-band through the BMC (no host PCIe link)?
├── YES → prefer, in this order:
│    1. bmccli flash update <img> -d <device|all>   ← SIMPLEST, PREFERRED
│       Run on the BMC shell; one device or the whole tray (`-d all`).
│       See references/bmc-redfish.md "bmccli".
│    2. fw_ctl bmc load   — drive the BMC remotely over Redfish
│       (references/bmc-redfish.md), when you are not on the BMC shell.
│    3. fwloader BMC build — MTD flash / SPI-Slave stream, lowest level
│       (references/fwloader-cli.md "BMC" + references/projects/<p>.md).
└── NO (lab host with PCIe/USB to the SoC)
    ├── Interactive / one-off in the lab, want auto image-resolution,
    │   reset, and status verbs in one place
    │        → fw_ctl  (RECOMMENDED for humans; see references/fw-ctl.md)
    ├── Scripting / CI / a Python harness
    │        → fw_loader.py  (thin Python wrapper; see references/fw-ctl.md)
    └── Lowest level / debugging the loader itself, or a path fw_ctl
        doesn't expose
             → fwloader binary directly (see references/fwloader-cli.md)
```

Then choose **what** you are delivering:

| Goal | Mode | Persists across reset? | Tool verb |
|------|------|------------------------|-----------|
| Program the on-board **SPI-NOR** (normal "flash the board") | `flash` (direct SPI) | **Yes** (non-volatile) | `flash write` / `fwloader -m flash` |
| **Boot once** without touching flash (dev iteration) | `stream` (streaming boot) | No (volatile SRAM/TCM) | `stream` / `fwloader` (default mode) |
| Recover a board stuck in **UART ROM recovery** | `xmodem` | Loads runtime, no flash | `fwloader -m xmodem` |
| Program the on-board **FW EEPROM** | `eeprom` | Yes | `fwloader -m eeprom` |

And the **transport** (see [references/fwloader-cli.md](./references/fwloader-cli.md)):

| Transport | Where | Speed | Use when |
|-----------|-------|-------|----------|
| **PCIe BAR0** (`pcie`, default) | Host | Fastest | Normal host flashing; PCIe link is up |
| **I2C Debugger** (`-t i2c`) | Host **or** BMC | Slow | Out-of-band; no PCIe |
| **FTDI SPI-Slave** (`-t ftdi-spi`) | Host | Slowest | No PCIe, no BMC — just a USB cable |
| **MTD** (`-d /dev/mtd/...`) | BMC | — | BMC direct flash |
| **SPI-Slave** (`sboot-fsm`) | BMC | — | BMC streaming boot |

## Step 2 — KNOW the image (anti-stale, before flashing)

**Do not flash an image you cannot fully identify.** Before any write:

1. **Resolve the exact file.** Prefer letting `fw_ctl`/`fw_loader.py` resolve
   the build-for-the-active-target image (so `cwd` clutter can't select the
   wrong file), and **pin the variant/revision** with the project's environment
   overrides so `flash`, `stream`, and the ARM-FW load all resolve the **same
   build**. See the project reference for the exact env vars and `device_fw`
   tree layout. When in any doubt, pass an **explicit absolute path**.
2. **Establish provenance.** Is it a **released BKC** artifact, a **TEST
   candidate**, or a **local build**? Never flash a TEST candidate onto a board
   that is supposed to run a released baseline, and never flash an **older** BKC
   over a newer one by accident. The project reference defines the build-ID
   format and the released-artifact store.
3. **Read the version stamped in the image itself** (build ID / `el_fw_version`
   embedded in the FPT), and record the **build number + date**. This is the
   value you will assert against read-back in Step 4.
4. **Checksum it** (`sha256sum`). A checksum mismatch vs the published artifact
   means a **stale mirror or a partial/corrupt download** — stop and re-fetch.
   Keep the checksum; you will re-hash the flash read-back later.

See [references/verification.md](./references/verification.md) for the concrete
commands and the full anti-stale checklist.

## Step 3 — FLASH it (into a flash-idle SoC)

Preconditions and hazards (details in
[references/verification.md](./references/verification.md)):

- **SoC must be flash-idle** for direct SPI-flash writes (held in reset after
  PCIe FLR, or FLA-ROM streaming-idle). Flashing an **active** SoC causes bus
  races and can corrupt the write. `fwloader --reset` (PCIe FLR) or a
  CPLD-driven reset gets you there.
- **No conflicting kernel driver.** The PCIe transport refuses to open if
  `el_pci`/`el_ctl` are loaded — unload them first (`rmmod el_accel el_ctl
  el_pci`).
- **Let the tool do the SR1 unlock.** Parts ship with SPI status-register
  block-protect bits that cause **silent** write/erase failures; fwloader
  auto-unlocks and relocks. Never assume "no error printed" = "written" — that's
  what Step 4 read-back is for.
- **Prefer compare-write** (default) over `-f`/force; use force only when you
  must rewrite unconditionally. Either way, **verify by read-back**, not by the
  tool's exit code alone.
- **Leave `FW_LOAD_DONE` auto-clear on.** After a flash write/erase, fwloader
  zeroes `FW_LOAD_DONE` so the ROM re-reads flash on the next reset. Do **not**
  pass `--no-clear-fw-load-done` unless you specifically intend the device to
  keep its currently-booted image.
- **BMC, fwloader MTD path: address the flash by label**, e.g.
  `/dev/mtd/soc-nv-flash`, not a bare `/dev/mtdN` number that can shift between
  boots. `bmccli flash update` selects the device for you (`-d <device|all>`) —
  prefer it.

## Step 4 — VERIFY it (mandatory; a flash isn't done until this passes)

Run **all** that apply; see [references/verification.md](./references/verification.md)
for exact commands:

1. **Read the version back from the device** and assert it **equals the expected
   build ID** from Step 2. Sources: `host_lib rom-status version` / the FPT
   version banner (host) or the BMC flash-version daemon log. A mismatch =
   stale/failed flash.
2. **Byte-verify the flash** (for direct SPI writes): `flash read` the region
   back and `cmp`/`sha256` it against the source image. This catches SR1-silent
   failures and undriven-SPI `0x00` misreads.
3. **Boot it and watch `fw_boot_sts`**: reset, then poll the aggregate boot
   status `0 (not_started) → 1 (in_progress) → 2 (done)`. **Stuck at
   `not_started`** = image not taken / ROM not kicked; **stuck at `in_progress`**
   = secondary-stage hang. Both are real, recurring failure signatures — do not
   declare success on a timeout.
4. **Sanity-check the variant** via the PCIe Revision ID (per project reference).
   Confirms you didn't flash a wrong-variant image that happens to boot.

If any check fails, **do not iterate blindly**: see Recovery below.

## Recovery (stuck / bricked / bad flash)

1. **Safe reset first:** `host_lib sysreset {soft|hard|platform}` via `fw_ctl`.
2. **CPLD power-cycle** if unresponsive: `rpe cpld power cycle` (targets `sys`,
   or `bmc`).
3. **Re-flash from a known-good released image** (not the suspect one).
4. **UART XMODEM recovery** if the board only reaches FLA-ROM recovery mode:
   `fwloader -m xmodem -i <released>.fwpkg`.
5. Capture `host_lib rom-status all` output for the ticket — the ROM
   version/progress/status decode is the evidence log for bring-up regressions.
6. **Flashed via the BMC?** Read the BMC-side log of the flashing process:
   `journalctl -u orchestrator` on the BMC (see
   [references/bmc-redfish.md](./references/bmc-redfish.md)). Redfish only
   reports the task verdict; the orchestrator journal says *why* it failed.

## References

- [references/fwloader-cli.md](./references/fwloader-cli.md) — the `fwloader`
  binary: modes, transports, every option, host + BMC examples.
- [references/fw-ctl.md](./references/fw-ctl.md) — `fw_ctl` console/CLI verbs and
  `fw_loader.py`; setup, launch, image auto-resolution, status/boot verbs, and
  the `bmc …` verbs that wrap the BMC flashing tool (`bmc load` = flash a whole
  sled).
- [references/bmc-redfish.md](./references/bmc-redfish.md) — BMC paths: the
  simple on-BMC `bmccli flash update` command, and `fw_ctl bmc …` over Redfish
  (whole-sled load/boot/reset, task queue, orchestrator journal).
- [references/verification.md](./references/verification.md) — the fool-proofing
  bible: anti-stale checklist, flash-idle preconditions, read-back/boot
  verification, and the catalogue of known false-flash traps.
- [references/projects/iridium.md](./references/projects/iridium.md) — Iridium
  specifics: released-image store (BKC/S3), build-ID format, variant/token map,
  host/board naming, PCIe rev IDs, environment overrides, recovery commands.
