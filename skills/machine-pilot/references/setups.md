# Setup Types — Taxonomy, Detection, Per-Type Facts

A *station* is a host (server/desktop), optionally a supplementary NUC, zero or one
BMC, and the devices they expose. Classify by **what the bus shows** plus **what the
memoir declares** — never by hostname alone.

## Device identity (same silicon, two vendor IDs)

| `lspci -nn` shows | Meaning | Seen on |
|---|---|---|
| `Processing accelerators [1200]: Device [208b:0001] (rev 01)` | A1 with the Element Labs vendor ID | dsk61, dsk70 (1 each), srv26 (2) |
| `Processing accelerators [1200]: Amazon.com, Inc. Device [1d0f:7596] (rev 01)` | A1 with the Amazon vendor ID | srv17 tray (6 visible) |

- **Always scan both**: `lspci -d 208b: -nn; lspci -d 1d0f: -nn`.
- BARs: BAR0 256 MiB (regs), BAR2 32 GiB (dsk) / 256 GiB (tray — memory window
  sizing differs per image/variant), BAR4 16 KiB. `[disabled]` = memory decoding off
  because no driver is bound — expected before `el_pci` loads.
- dmesg names the device `atl 0000:<BDF>:` once the KMD (`el_pci`, DKMS package
  `atl-dkms`) binds. Clean enumeration without a driver only shows
  `pci 0000:<BDF>: [1d0f:7596] type 00 class 0x120000 PCIe Endpoint`.
- The PCIe **Revision ID** in `lspci` is `rev 01` on current lab parts; `fw-flasher`
  reads the variant-identifying revision via DBI/config space through its own tooling.
  Do not use the `lspci` rev to argue about variants.

## Detection rules

```
n = count(lspci -d 208b:) + count(lspci -d 1d0f:)
switch = lspci -tv shows the devices under Broadcom/LSI bridges [1000:c040]
         (+ a "PCIe Switch management endpoint" [1000:00b2])
```

| Observation | Classify as | Notes |
|---|---|---|
| `n` between 1 and 8 **and** `switch` **and** host is `sa-srv*` | **ELS tray** (`n` of 8 up) | Report missing OAMs. BDFs stride by switch port (e.g. `5b 5d 5f 62 66 6e`). |
| `n == 1`, device under the CPU root / a small on-board bridge, host `sa-dsk*` | **stand-alone** *or* **riser** | Indistinguishable on the bus — use the memoir's declared type; ask if absent. |
| `n == 0` | **nothing enumerated** | Not a setup type: either no device attached (NUC), link down, or tray powered off. Check memoir + BMC. |
| several switch subtrees with many devices on a Lenovo host | **pod / rack** | Not available yet; sled ID = secondary CPLD reg `0x09`. |

A supplementary **NUC** (`sa-nuc<NN>-csr`) has **no PCIe device** — it carries the USB
serial adapters (UART / I2C-debugger) for a bench next to it. `n == 0` on a NUC is
normal; its job is `/dev/ttyACM*`/`/dev/ttyUSB*`.

## ELS tray (ELS100 / UBB, 8 OAMs)

- Hardware: ASPEED AST2600 BMC on the DC-SCM, primary/secondary Lattice MachXO3D
  CPLDs, Broadcom PCIe switches, Aries6 retimers, per-module SPI/UART MUX.
- **Two BMCs resolve for a tray host — don't mix them up:**
  `bmc-sa-srv<NN>-csr` is the *server's* own BMC; **`bmc-sa-els<NN>-csr` is the tray
  (UBB) BMC** that owns the OAM UARTs, `oem_show_version`, FRU, sled power.
  Observed: `sa-srv17-csr` ↔ `bmc-sa-els17-csr` (192.168.176.154); other known tray
  BMCs: `bmc-sa-els25-csr`, `bmc-sa-els35-csr`; a bench S-board BMC: `bmc-sa-eab39-csr`.
- UART: BMC `obmc-console` ports **2200–2207 = OAM0–7**, **2208–2209 = PCIe switch
  UARTs**, **2210–2211 = PCIe SDB**; persisted logs in BMC `/var/log/`. See
  [uart.md](./uart.md).
- Multi-device host tooling: `fw_ctl` refuses to auto-pick on >1 device — use
  `pcie devices`, `--bdf <BDF>`, `iface init --all` then `iface bdf <BDF>`, or
  `--device a b c` / `--all` fan-out (see [host-ops.md](./host-ops.md)).
- OAM slot ↔ BDF mapping is **per tray** and must be learned once (BMC console banner
  / `fw_ctl` inventory / slot LEDs) and recorded in the memoir registry.
- Hosts are **SAT-allocated** — `/etc/motd` shows the current owner.

## Stand-alone (EA100-S bench, 1 device)

- One A1 on a bench board, PCIe to a desktop (`sa-dsk<NN>-csr`); the desktop may carry
  no USB adapters at all (dsk61/dsk70 have none) — then the UART/I2C adapters sit on a
  **supplementary NUC** (`sa-nuc<NN>-csr`) next to it.
- UART: FTDI/USB serial adapter → **`/dev/ttyACM0`** (device UART), open with
  **`minicom -D /dev/ttyACM0`** (115200 8N1, the lab `minirc.dfl` default). The
  node is world-writable (`crw-rw-rw- root dialout`) and the lab user is in `dialout`
  — no `sudo`. Verify which adapter backs the node with `ls -l /dev/serial/by-id/`
  (on nuc74 `ttyACM0` is the `Microchip MCP2221` "MCP1" bridge; `ttyACM1` is "MCP2";
  FTDI Quad/Dual adapters appear as `ttyUSB0–3`, `ttyUSB8–9`).
- Bench LEDs (Device_RDY, Module_PWRGD, PERST#, A1 RESET); power/reset/straps via
  **RPE** CPLD bridge (`fw_ctl rpe cpld …`) — *disturb* class.
- BMC: usually none that matters for the OAM (a bench S-board BMC such as
  `bmc-sa-eab39-csr` exists on some benches — record per station).

## Riser board (1 device, SE loopback)

- Same host-side picture as stand-alone (1 device, same UART path), **plus the
  scale-up (SE) ports are cabled back to one another** so SE/C2C link tests run
  without a partner chip. Invisible to `lspci`.
- Declare it in the memoir registry once (`type: riser`) — the pilot must not demote a
  riser to "stand-alone" because the bus looks the same.
- SE link state is read through the SoC (`fw_ctl`/`su-ip` knowledge), not the host bus.

## Full rack / pod (future)

- 4 × ELS100 per Lenovo host ("Pod PCIe Addresses Per Sled", RPI space): each sled has
  its own PCIe address range and tray BMC; the sled number is read from the
  **secondary CPLD reg `0x09`**.
- Not available yet. When it lands: treat it as N trays — one registry row per sled,
  one BMC per sled, shared host.

## Known stations

The live registry is [stations.tsv](./stations.tsv) (`pilot stations`; edit with
`pilot stations set <st> k=v`). Observed per-boot facts (BDFs, link health, device
counts) belong in the memoir activity log, not here — they change.
