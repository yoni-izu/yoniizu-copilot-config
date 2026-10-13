# `fwloader` — Core Firmware Loader (CLI Reference)

`fwloader` is the low-level C tool that delivers firmware to the SoC and manages
SPI-flash content. Everything else (`fw_ctl`, `fw_loader.py`, CI) wraps it.
Source: `tools/fwloader/` (repo `module.infra.sw.fw.tools`). The authoritative,
always-current option list is `fwloader --help` — reproduce facts from the live
binary when in doubt.

## Two build targets

| Build | Runs on | Default transport | Modes |
|-------|---------|-------------------|-------|
| **Host** (default) | x86_64 lab host | `pcie` (BAR0 mmap) | stream, flash, xmodem, eeprom |
| **BMC** (`BUILD_BMC=1`) | ARMv7 BMC | `spi` (sboot-fsm) | stream (SPI-Slave/I2C), flash (MTD), xmodem, eeprom (I2C-OOB) |

Built binaries land in
`build_artifacts/opt/atl/fw/tools/fwloader/{host,bmc}/fwloader` after
`octl ws build infra`.

## Modes (`-m`)

- **`stream`** (default) — deliver a PLDM `.fwpkg` to volatile SRAM/TCM via the
  streaming mailbox. **Does not touch flash.** SoC must be in FLA-ROM
  streaming-wait (a flash-strapped board with a valid image boots from flash and
  never opens the mailbox → the stream will time out).
- **`flash`** — read/write/erase the on-board SPI-NOR directly. **Persistent.**
  Host drives the DW APB SSI controller (SFDP auto-discovery, 4-byte addressing,
  smart erase, auto SR1 unlock/relock). BMC uses Linux MTD.
- **`xmodem`** — send FLA SoC manifest + runtime over UART XMODEM-1K when the ROM
  is in UART recovery mode. Input is the same `.fwpkg`.
- **`eeprom`** — read/decode or write the on-board FW EEPROM (prebuilt `.bin`
  only; always read-back verified; cannot skip verification).

## Transports (`-t`)

| `-t` value | Build | Notes |
|------------|-------|-------|
| `pcie` | Host | Default. BAR0 mmap via sysfs; **refuses if `el_pci`/`el_ctl` loaded** → `rmmod el_accel el_ctl el_pci`. |
| `i2c` | Host + BMC | I2C Debugger via `/dev/i2c-N`. `--i2c-bus`, `--i2c-addr`. Slow (1 AXI txn/reg). |
| `ftdi-spi` | Host | FTDI MPSSE SPI-Slave over USB; no kernel driver. Slowest. `--ftdi-*`. |
| `spi` | BMC | Default on BMC; SPI-Slave via `/dev/sboot-fsm` (module must be loaded). |
| MTD (`-d /dev/mtdN`) | BMC | Flash mode target; addressed by device path. |

Flash options are transport-agnostic — the same `-f/-O/-c/-C/-E/-o/-s` behave
identically on `pcie`, `i2c`, and `ftdi-spi`.

## Options

| Option | Meaning |
|--------|---------|
| `-m, --mode <stream\|flash\|xmodem\|eeprom>` | Operation mode (default `stream`). |
| `-i, --image <path>` | Image: `.fwpkg` (stream/xmodem) or raw `.bin` (flash). |
| `-o, --output <path>` | Flash-read: dump flash content to a file. |
| `-s, --read-size <bytes>` | Bytes to read with `-o` (default: whole device). |
| `-O, --flash-offset <hex>` | Byte offset for read/write. **Must be erase-block aligned**; `offset+size ≤ device size`. |
| `-t, --transport <type>` | See transport table. |
| `-d, --device <bdf\|path>` | PCIe BDF or `auto`; BMC sboot-fsm path; BMC MTD path (`/dev/mtd7` or `/dev/mtd/<label>`). |
| `-f, --force` | Flash: skip compare pass, erase+program **all** sectors. |
| `-E, --chip-erase` | Flash: full chip erase (WREN + 0xC7); no image needed. |
| `--no-clear-fw-load-done` | Flash: **skip** zeroing `FW_LOAD_DONE` after write/erase. Avoid unless intentionally keeping the booted image. |
| `-c, --spi-cs <n>` | SPI chip select (default 0). |
| `-C, --spi-ctrl <n>` | DW APB SSI controller index (default 0). |
| `-r, --retries <n>` | Max poll retries/step; `0` = infinite (wait until Ctrl-C). |
| `--reset` | Stream: PCIe FLR before polling → forces SoC into FLA-ROM streaming mode. |
| `--mbx-addr <hex>` / `--mbx-size <hex>` | Override mailbox region (semantics differ per transport). |
| `--serial <path>` / `--baud <rate>` | XMODEM UART port / baud. |
| `--bin <path>` / `--read` / `--write` | EEPROM mode image / read-decode / program. |
| `--i2c-bus <N>` / `--i2c-addr <hex>` | I2C Debugger selectors. |
| `--ftdi-list` | List FTDI devices+channels and exit. |
| `--ftdi-serial <SN>` / `--ftdi-usb <path>` / `--ftdi-channel <A-D>` | FTDI device/channel select. |
| `--ftdi-freq <Hz>` / `--ftdi-spi-mode <0-3>` / `--ftdi-cs <n>` / `--ftdi-lsb-first` | FTDI SPI tuning. |
| `-p, --platform <name>` | SoC platform (default `a1`). |
| `-l, --list` | List components in a `.fwpkg` and exit. |
| `-v, --verbose` | Debug logging. |
| `-V, --version` / `-h, --help` | Version / help. |

## Host examples

```bash
# Inspect a package (ALWAYS do this before flashing an unknown .fwpkg)
./fwloader -l -i pldm_fwu_onix.fwpkg

# Streaming boot (auto-detect device), forcing the SoC into ROM stream mode
./fwloader -i pldm_fwu_onix.fwpkg --reset

# Direct SPI-flash write (SoC must be flash-idle); compare-write (skips unchanged)
./fwloader -m flash -i spi_flash_image_arm_onix.bin

# Force full erase+program
./fwloader -m flash -i spi_flash_image_arm_onix.bin -f

# Read the flash back for byte-verify / backup
./fwloader -m flash -o flash_dump.bin

# Read a specific region (4 KB @ 0x80000)
./fwloader -m flash -o dump.bin -s 0x1000 -O 0x80000

# Chip erase
./fwloader -m flash -E -C 0 -c 0

# Out-of-band variants (functionally identical, slower):
./fwloader -m flash -i img.bin -t i2c --i2c-bus 2 --i2c-addr 0x55
./fwloader -m flash -i img.bin -t ftdi-spi --ftdi-serial FTAQK7H9
./fwloader --ftdi-list
```

## BMC examples

> On the BMC shell, prefer `bmccli flash update <img> -d <device|all>` for a
> plain flash update — it is simpler than the MTD path below. See
> [bmc-redfish.md](./bmc-redfish.md). Use `fwloader` directly for streaming
> boot, erase/read-back, XMODEM recovery, or when debugging the loader itself.

```bash
# Streaming boot over SPI-Slave (sboot-fsm module loaded)
./fwloader -i pldm_fwu_onix.fwpkg

# Direct MTD flash write — address by LABEL, not a bare number
./fwloader -m flash -i spi_flash_image_arm_onix.bin -d /dev/mtd/soc-nv-flash

# Force full erase+program of a dedicated flash device
./fwloader -m flash -i spi_flash_image_arm_onix.bin -d /dev/mtd/soc-nv-flash -f

# Read back for verify / backup
./fwloader -m flash -o /tmp/flash_dump.bin -d /dev/mtd/soc-nv-flash

# Chip erase
./fwloader -m flash -E -d /dev/mtd/soc-nv-flash

# Streaming boot over I2C Debugger (OOB)
./fwloader -t i2c -i pldm_fwu_onix.fwpkg --i2c-bus 3 --i2c-addr 0x55

# Debug the BMC-side flashing process (orchestrator drives the A1 update)
journalctl -u orchestrator -n 200 --no-pager
```

## XMODEM recovery (host or BMC)

```bash
./fwloader -m xmodem -i pldm_fwu_onix.fwpkg                 # default serial/baud
./fwloader -m xmodem -i pldm_fwu_onix.fwpkg --serial /dev/ttyUSB0 --baud 115200
```

## Key built-in safeguards (rely on these, still verify)

- **SFDP discovery** — flash geometry read at runtime; 4-byte addressing auto-on
  for parts > 16 MB.
- **Smart erase + read-before-erase** — unchanged sectors skipped (unless `-f`).
- **Auto SR1 block-protect unlock/relock** — otherwise writes fail **silently**.
- **`FW_LOAD_DONE` auto-clear** after write/erase — forces ROM flash-boot on next
  reset; suppress only with `--no-clear-fw-load-done`.
- **PCIe driver-conflict guard** — refuses if `el_pci`/`el_ctl` loaded.
- **EEPROM write is always read-back verified** — cannot be skipped.

None of these replace Step 4 verification: a clean exit code is **not** proof the
correct image is in flash and boots. Always read the version back.

## `.fwpkg` format (streaming/xmodem input)

DMTF **DSP0267** PLDM FW Update Package. A **transport container** bundling
component images (FLA SoC manifest, FLA runtime, etc.) with FPT headers across
partitions A/B/C. It is parsed by fwloader (format-revision byte + CRC-32 header
check) and is **never written raw to SPI flash** — that's what the
`spi_flash_image_*.bin` is for. Use `-l` to enumerate components.
