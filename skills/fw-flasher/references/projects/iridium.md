# Project: Iridium (A1)

Iridium-specific facts for FW flashing. This is the **only** project today; the
skill is structured so a new project = a new sibling file in this folder
(`references/projects/<project>.md`) with the same sections, and **nothing in the
main workflow changes**.

> Source of truth is the live repo + Confluence; values below are the stable
> facts. When a value looks off, verify against `fwloader --help`, the
> `fw_ctl/README.md`, and the Confluence pages linked at the end.

## Platform / silicon

- SoC platform name: **`a1`** (fwloader `-p a1`, the default).
- One vendor/device ID across all variants; the **variant is identified by the
  PCIe Revision ID** read from config space after the link is up.

## Variants, hostnames, and tokens

`fw_ctl` auto-discovers the **variant from the hostname**; `--variant` overrides.

| hostname pattern | variant | target | flash/stream token |
|------------------|---------|--------|--------------------|
| `a1-<rack>-<dev>-csr` | `asic` | A1 silicon | `asic` |
| `vlsi-fpga…` (e.g. `vlsi-fpga01-csr`) | `fpga` | A1 on **onix** FPGA | `onix` |
| *(pldm — out of scope for this skill)* | `pldm` | Palladium | `palladium` |

**Token** used to resolve the built image is `<variant>[_<revision>]`:
`fpga`→`onix`, `asic`→`asic`. Example resolved paths:

```
flash:  <device_fw_root>/arm/asic/flash_bf/spi_flash_image_arm_asic.bin
stream: <device_fw_root>/arm/onix/stream_bf/pldm_fwu_onix.fwpkg
```

## Environment overrides (pin the build — anti-stale)

| Env var | Effect |
|---------|--------|
| `EL_FW_VARIANT` | **Wins** over the hostname-derived variant; pins the token. |
| `EL_FW_REV` | Qualifies the variant into a specific build/revision (die- or rev-specific package). |

Setting both pins **`flash write`, `stream`, and `load_arm_fw` to the same
build** — the primary defense against flashing a mismatched/older image. Pass an
explicit absolute path to bypass resolution entirely.

## PCIe Revision IDs (variant sanity)

- A1 ASIC silicon reads **rev `0x22`** (verify via DBI config-space read).
- fwloader classifies ASIC / Palladium / FPGA by rev-ID ranges from
  `a1_pcie_ids.h` and logs the detected variant at startup — cross-check that log
  line against the board you think you're on.

## Image sourcing & versioning (released vs candidate)

- **Build ID format:** `1.<build_num>.0-<DDMMYYYY>-<HHMMSS>`
  (e.g. `1.699.0-08092026-140444`). The **build number** and **date** are your
  staleness/downgrade signal. Generated per-configure into `el_fw_version.h`
  (PRJIRDMSW-6095) and embedded into the **FPT** and SoC registers.
- **Released store (S3):**
  `s3://s3-sw-external-artifacts/iridium/system_fw/<bkc_folder>/<component>/<board>/<revision>/<version>/…`
  - Numbered folders (`bkc01`, `bkc02`, …) are **immutable, promoted** releases.
  - **`TEST`** holds candidates — **not** releases. Never flash a `TEST`
    candidate onto a board that must run a released baseline.
- **BKC governance (power-on):** auto-updates are **disabled** during power-on.
  Candidates are validated on ASIC (`octl ws test -g asic_tests -O <artifact>:vN`)
  and manually promoted by `SW-LEADS-SG` via the DevOps portal. Follow "PO
  Playbook 2 — Update the integration manifest (BKC)".
- **Manifests** (`ip.infra.sw.manifests`, `.xml`) pin repo revisions and
  versioned prebuilt S3 tarballs (`dep_type="build"`/`"external"`); Starfiles
  (`.star`) define the build/test steps stitched by `octl`.

## Flash layout / read-back (FPT)

- Boot NOR holds **3 redundant FPT copies** — partitions A/B/C at
  **`0x40000` / `0x41000` / `0x42000`**.
- The BMC management daemon compares the copies field-by-field and logs
  `version=<ver> build=<build_id> uuid=<uuid>`, **warning on any mismatch** — a
  mismatch means a bad/partial write.

## BMC specifics

- Flash device: address by label, **`/dev/mtd/soc-nv-flash`** (not a bare
  `/dev/mtdN`).
- Streaming boot: SPI-Slave via **`/dev/sboot-fsm`** (module must be loaded), or
  I2C Debugger via `/dev/i2c-N`.
- EEPROM OOB write: I2C Debugger transport (`-t i2c`).
- Hardware: ELS100 (UBB) 8-module tray — ASPEED **AST2600** BMC (DC-SCM),
  Primary/Secondary Lattice **MachXO3D** CPLDs, Broadcom PCIe switches, Aries6
  retimers, per-module SPI/UART MUX routing.

## Lab host / board naming

- Host desktops/servers: `sa-dsk<NN>-csr`, `sa-srv<NN>-csr`; Taiwan jigs
  `jig-dsk<NN>-twn`. Board hostnames: `a1-<rack>-<dev>-csr`.
- Bench: EA100-S single-A1 stations (LEDs: Device_RDY, Module_PWRGD, PERST#,
  A1 RESET); slot/module IDs assigned remotely via **RPE**.
- **RPE** orchestrates power supplies, CPLD bridges, thermal (HeatCon), EEPROMs;
  **SVE** is the host-side validation-embedded runtime over PCIe/I2C/SPI/JTAG.

## Recovery commands (Iridium)

```bash
# 1. Safe reset via fw_ctl
python3 -m fw_ctl.cli --variant asic host_lib sysreset platform
# 2. CPLD power cycle if unresponsive (targets: sys | bmc)
python3 -m fw_ctl.cli --variant asic rpe cpld power cycle
# 3. UART XMODEM recovery from a released package
fwloader -m xmodem -i <released>.fwpkg
# 4. Evidence for the ticket
python3 -m fw_ctl.cli --variant asic host_lib rom-status all
```

## Known real incidents (learn the signatures)

- **PRJIRDMSW-8122** — ARM FW `fw_boot_sts` stuck at `in_progress` after reset
  (~1200 s, never `done`): secondary-stage boot hang.
- **PRJIRDMSW-6888** — `fw_boot_sts` stuck at `not_started`; `load_arm_fw:
  FAILED` (bootloader timeout / missing ROM kick).
- **PRJIRDMSW-6893 / -4062** — integration-manifest workflow failures during
  artifact promotion.
- **IPPCIESW-637** — PCIe BMU config needed for `fla_rt` recovery over PCIe.

## Confluence references (verify against these)

- *Firmware Loader (fwloader)* (SWENG) — architecture / mailbox state machine.
- *Flash SPI Image Using fw_loader via OOB from BMC* (SWENG) — BMC OOB steps.
- *PLDM Firmware Update Package (.fwpkg) Generation* (SWENG).
- *FW Bring-up & Boot Toolbox (fw_ctl) — Design* / *FW_CTL tool a1 power on* (RII).
- *FW Flash procedure over PCIe and FTDI-SPI* (RPI) — RPE/CPLD MUX sequences.
- *A1 Bring-Up — FW Knowledge Base* / *Power-On Tools & Infrastructure* /
  *EA100-SB Gen 6 Setup* (RPI); *Hardware Architecture — ELS100 (UBB) & EA100*
  (RIM).
- *Iridium SW Build and CI Architecture: Manifests, Starfiles and octl*;
  *PO Playbook 2 — Update the integration manifest (BKC)* (RPI);
  *Firmware and BKC Release Procedure — Chip Power-On Stage*.

---

## Adding a new project

Create `references/projects/<project>.md` mirroring these sections:
platform/`-p` name · variant↔hostname↔token map · PCIe rev IDs · image store &
build-ID format · FPT layout · BMC specifics · lab naming · recovery · incidents
· Confluence links. Then reference it from `SKILL.md` Step 0. The generic
workflow, `fwloader-cli.md`, `fw-ctl.md`, and `verification.md` stay unchanged.
