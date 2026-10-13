# Verification — The Fool-Proofing Bible

This is the anti-**stale**, anti-**false-flash** reference. The core failure this
skill exists to prevent: *believing a flash succeeded when the device is actually
running an old, wrong, or partially-written image.* A zero exit code is **not**
proof. Only **read-back of the version + a successful boot** is proof.

Project-specific values (build-ID format, released-image store, PCIe rev IDs,
env-var names) live in [projects/](./projects/). This file is the
project-agnostic method.

---

## A. Pre-flash checklist (KNOW the image)

Run every item before writing anything:

- [ ] **Exact file resolved.** Either an explicit absolute path, or a
      tool-resolved build-for-target image with the variant/revision **pinned**
      via the project env overrides (so `flash`, `stream`, and the ARM-FW load
      all pick the **same build**). Never rely on "whatever `.bin` is in `cwd`".
- [ ] **Provenance known.** Released **BKC** vs **TEST candidate** vs **local
      build**. BKC folders are immutable/promoted; TEST candidates are not
      releases. Do not cross the streams.
- [ ] **Not stale / not a downgrade.** Read the build ID stamped in the image and
      compare **build number + date** against what the board *should* run. A
      lower build number or older date than the current baseline is a downgrade —
      confirm it's intentional.
- [ ] **Checksum captured.** `sha256sum <image>` and compare to the artifact's
      published checksum. Mismatch ⇒ **stale mirror or partial download** — stop,
      re-fetch, re-hash. Keep the hash for the post-flash byte-verify.
- [ ] **Right variant for this board.** The token/variant in the filename matches
      the board you're flashing (asic vs onix, correct revision/die).
- [ ] **Package sanity (stream/xmodem).** `fwloader -l -i <pkg>.fwpkg` lists the
      expected components.

```bash
sha256sum spi_flash_image_arm_asic.bin           # record it
strings spi_flash_image_arm_asic.bin | grep -i 'build\|version' | head   # spot-check stamp
fwloader -l -i pldm_fwu_asic.fwpkg               # enumerate .fwpkg components
```

---

## B. Flash-idle preconditions (don't corrupt the write)

- [ ] **SoC is flash-idle** before a direct SPI-flash write: held in reset after
      PCIe FLR, or sitting in FLA-ROM streaming-idle. Flashing an **active** SoC
      races the on-die masters on the SPI bus. Get idle with `fwloader --reset`
      (PCIe FLR) or a CPLD reset; PCIe downstream master-termination must be armed
      before reset/flash on active hardware.
- [ ] **No conflicting kernel driver** (PCIe path): `el_pci`/`el_ctl` must be
      unloaded or fwloader refuses to open. Unload dependents first:
      `rmmod el_accel el_ctl el_pci`.
- [ ] **Correct target device.** BMC: use the **label** symlink
      (`/dev/mtd/soc-nv-flash`), never a bare `/dev/mtdN` that can renumber
      across boots. Host: correct PCIe BDF / chip-select / controller.
- [ ] **Stale SPI read buffer drained** (I2C/SPI OOB): a leftover CRC-retry word
      can desync the first read — `fw_ctl`'s `iface clear` / `iface init` handles
      this; the SPI transport also auto-drains on init.

---

## C. Post-flash verification (a flash isn't done until ALL pass)

### C1. Version read-back — assert equality
Read the version the **device** reports and assert it equals the **expected
build ID** from step A. This is the single most important anti-stale check.

```bash
# Host, via fw_ctl:
python3 -m fw_ctl.cli --variant asic host_lib rom-status version
# BMC: read the flash-version daemon log (it compares the FPT copies and logs
#      version=<ver> build=<build_id> uuid=<uuid>, warning on any mismatch).
```
The **FPT** carries **3 redundant copies** (partitions A/B/C). The BMC daemon
compares them field-by-field; **any mismatch is a warning** = a bad/partial
write. Treat a version that doesn't match the expected build ID as a **failed
flash**, not a cosmetic difference.

### C2. Byte-verify the flash (direct SPI writes)
Read the region back and compare to the source — catches SR1-silent write
failures and undriven-SPI `0x00` misreads that C1 alone can miss.

```bash
fwloader -m flash -o readback.bin -s <size> -O <offset>   # or fw_ctl: flash read
cmp readback.bin spi_flash_image_arm_asic.bin             # must be identical
# or: sha256sum readback.bin  (compare to the source hash from step A)
```

### C3. Boot it and watch `fw_boot_sts`
Reset and poll the aggregate boot status — it must climb
`0 (not_started) → 1 (in_progress) → 2 (done)` within the expected window.

```bash
python3 -m fw_ctl.cli --variant asic host_lib sysreset platform
python3 -m fw_ctl.cli --variant asic host_lib boot wait fla_runtime --timeout 60
python3 -m fw_ctl.cli --variant asic host_lib boot fw-sts     # expect done (2)
```
- **Stuck at `not_started` (0)** — image not taken / ROM not kicked / bootloader
  timeout. (Real signature: PRJIRDMSW-6888 `load_arm_fw: FAILED`.)
- **Stuck at `in_progress` (1)** — secondary-stage boot hang; device started but
  never reached `done`. (Real signature: PRJIRDMSW-8122, stalled ~1200 s.)
- **A timeout is a FAILURE, not "probably fine."** Capture `host_lib rom-status
  all` for the ticket.

### C4. Variant/identity sanity
Confirm the PCIe **Revision ID** matches the expected variant (per project
reference) so a wrong-variant image that happens to boot is caught. `FW_LOAD_DONE`
should have been auto-cleared by the write (so the ROM re-read flash); if you
suppressed that with `--no-clear-fw-load-done`, the device may still be running
the **old** image — that's expected only if you meant it.

---

## D. Known false-flash traps (and the guard)

| Trap | Symptom | Guard |
|------|---------|-------|
| **Stale mirror / partial download** | Old or corrupt image looks fine | sha256 vs published checksum **before** flashing (A) |
| **Wrong-variant image auto-picked** | Boots, but wrong build | Pin `EL_FW_VARIANT`/`EL_FW_REV`; check token in filename (A) |
| **Downgrade by accident** | Older build overwrites newer | Compare build number+date to current baseline (A) |
| **SR1 block-protect** | Write/erase **silently** no-ops | fwloader auto-unlock/relock **+** byte read-back (C2) |
| **Undriven SPI reads `0x00`** | False "failing status" or false compare | retry probe / `iface clear`; byte read-back (B, C2) |
| **Flashing a non-idle SoC** | Bus races, partial write | FLR/reset to flash-idle first (B) |
| **`el_pci`/`el_ctl` loaded** | fwloader won't open PCIe | `rmmod el_accel el_ctl el_pci` (B) |
| **Bare `/dev/mtdN` renumbered** | Flashed the wrong flash | Use `/dev/mtd/<label>` (B) |
| **`--no-clear-fw-load-done`** | ROM keeps booting the OLD image | Leave auto-clear ON unless intentional (C4) |
| **`--simulate` mistaken for real** | "0xDEADC0DE" reads, nothing flashed | Never verify against simulate; real HW only |
| **`stream` times out** | Board is flash-booting, mailbox never opens | Check boot strap; `--reset`/FLR into ROM stream mode |
| **Exit code 0 ≠ success** | Loader "ok", device stale | Mandatory C1+C2+C3 |

---

## E. Recovery ladder

1. `host_lib sysreset {soft|hard|platform}` (via `fw_ctl`).
2. `rpe cpld power cycle` (targets `sys` or `bmc`) if unresponsive.
3. Re-flash from a **known-good released** image (not the suspect one).
4. `fwloader -m xmodem -i <released>.fwpkg` if the board only reaches FLA-ROM
   UART recovery.
5. Attach `host_lib rom-status all` output to the incident ticket.

---

## F. One-glance "definition of done"

A flash is **done** only when **all** are true:
1. Image identity + checksum verified **before** write.
2. Loader reported success **and** byte read-back matches the source.
3. Device version read-back **equals** the expected build ID (FPT copies agree).
4. `fw_boot_sts` reached **done (2)** within the timeout after reset.
5. PCIe rev ID / variant matches the board.

Anything less = **not done**.
