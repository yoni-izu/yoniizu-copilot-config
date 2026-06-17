---
name: pldm
description: 'Connect to and run firmware on Palladium (PLDM) emulation stations. Use when: connecting to emulation/Palladium VNC or EDK host, loading a PLDM image (pd_load), power-cycling / resetting the emulated design, capturing or loading waves (pd_wave), backdoor-loading memories (ROM/RAM/SRAM ECC), debugging over UART/JTAG (minicom/Lauterbach t32), IPMI power control, finding a station/host/IPMI IP, or looking up an image name by PCIe rev ID.'
argument-hint: 'What you want to do on Palladium — connect, load image, power cycle, capture/load waves, init memories, or debug'
---

# Palladium (PLDM) Emulation Stations

## When to Use

- Connecting to an emulation station (VNC desktop or EDK host server) for **Iridium** or **Lotus**
- Loading a PLDM image with `pd_load`, or terminating a stuck load
- Power-cycling / resetting the emulated design (Palladium reset sequence + EDK host IPMI power cycle)
- Clearing and re-applying default forces
- Capturing waves and loading them offline with `pd_wave` (Verisium / SimVision)
- Backdoor-loading memories (FLA ROM, CM7 ITCM, SP SPRAM) with ECC generation
- Debugging hardware over **UART** (minicom) or **JTAG** (Lauterbach Trace32)
- Looking up a station/EDK-host/IPMI hostname or IP, or an image name by PCIe rev ID

> Source of truth: Confluence "Palladium Getting Started and User Guides"
> (VLSIFE space, page 100827150). This skill is a distilled operational copy.

## Prerequisites

- Membership in the project emulation security group (e.g. `project-iridium-vlsi-emul-sg`).
  New users need an **IT Jira** to get VNC/server access. Check membership at the
  [Office 365 groups page](https://myaccount.microsoft.com/groups/groups-i-belong-to).
- **VPN** connection if not on the office network.
- **VNC / EDK host login**: personal user credentials (the same personal credentials documented
  on the platform wiki). Do NOT hardcode these.
- **IPMI login** (shared lab creds): user `elmnt_user`, password `elmnt123!`.

## Quick Index of Resources

- Full station / EDK-host / IPMI inventory (Iridium Z2 & Z3, Lotus): [references/stations.md](./references/stations.md)
- Image name ⇄ PCIe rev ID lookup table: [references/images.md](./references/images.md)
- Palladium dashboard (image content, machine status, allocation calendar):
  https://emul-online.elmnt.local/simple_view.html

## 1. Connect and Load an Image

1. Pick a station for the design from [references/stations.md](./references/stations.md). Each
   station maps to a **VNC desktop** (the Palladium GUI / control terminal) and an **EDK host
   server** (the SoC host, reachable by SSH).
2. **VNC in** to the station (e.g. `vnc-emul-iridium01:1` → `192.168.110.142:1`) using your
   personal credentials.
3. In the station terminal, load an image:
   ```bash
   pd_load          # interactive menu — pick the image to load
   ```
4. If the load **hangs**, terminate it with the job id printed in the `pd_load` terminal:
   ```bash
   nc stop <jobid>
   ```
5. **SSH to the EDK host** for the SoC side (X-forwarding for GUIs):
   ```bash
   ssh user@pldm-edk0-z2a -X     # host hostname from references/stations.md
   ```
   On a **first-time** connection to a station/EDK host, bootstrap your personal environment
   first — see [§1a](#1a-first-time-station-setup-bootstrap).

## 1a. First-Time Station Setup (bootstrap)

Detect whether this is a first connection to the target station/host, and if so copy your SSH
keys and shell helpers over. The marker file `~/.palladium_bootstrapped` records that a host is
already set up.

**Detection** (run against the target host; replace `<host>`):
```bash
host=<host>   # e.g. pldm-edk0-z2a
if ssh "$host" 'test -f ~/.palladium_bootstrapped'; then
  echo "already bootstrapped"
else
  echo "first-time — needs bootstrap"
fi
```

**Bootstrap** — copy SSH keypair, `wip_funcs.sh`, aliases, and `my_funcs.sh`, then drop the
marker. Use the helper script:
```bash
./scripts/bootstrap_station.sh <host>     # e.g. ./scripts/bootstrap_station.sh pldm-edk0-z2a
```

What it copies (only the items present locally):
- `~/.ssh/id_ed25519` and `~/.ssh/id_ed25519.pub` → host `~/.ssh/` (private key kept at `600`)
- `~/wip_funcs.sh` → host `~/wip_funcs.sh`
- `~/.bash_aliases` → host `~/.bash_aliases`
- `~/my_funcs.sh` → host `~/my_funcs.sh`

The script also ensures `~/.bashrc` on the host sources `wip_funcs.sh` and `my_funcs.sh`
(idempotent), then writes `~/.palladium_bootstrapped` so subsequent connects skip the copy.

> The same bootstrap applies to a VNC station shell and an EDK host — run it once per host.
> Re-run with `--force` to refresh the copied files.

## 2. Power Cycle Sequence

Do BOTH steps — reset the emulated design first, then power-cycle the EDK host.

1. In the Palladium GUI, reset the design (replace `<proj>`, e.g. `iridium`):
   ```tcl
   force <proj>_rst_n 1'b0
   # wait ~2 seconds
   force <proj>_rst_n 1'b1
   ```
2. Power-cycle the EDK host over IPMI (from a shell; mgmt host/IP from
   [references/stations.md](./references/stations.md)):
   ```bash
   ipmitool -I lanplus -H <mgmt-host-or-ip> -U elmnt_user -P 'elmnt123!' power cycle
   ```
   `power` also accepts `status` / `on` / `off`.

## 3. Clear and Re-apply Default Forces

In the PLDM GUI:
```tcl
source ../../setup/release_and_reforce.qel
```

## 4. Waves: Capture and Load

### Capture (in the Palladium GUI control tab)
1. Name the trace file in advance under **"Wave DB name"**.
2. Set the required trace size and trigger position.
3. Capture the wave using the trigger.
4. Press **"Prepare offline"**.

### Load offline with `pd_wave`
```bash
pd_wave <full-path-to.phy> [image_name]
# e.g.
pd_wave /vlsi/proj/iridium/emul/users/<user>/trace.phy
# pass image_name only if the image in the capture folder was replaced, e.g.:
pd_wave /vlsi/proj/neon/emul/users/<user>/trace.phy iridium_fullchip_687_mc_1rack
```
- Default wave tool is **Verisium**. To use **SimVision** instead, run `pd_wave_simv`.
- Use the dedicated wave-loading VNC desktops (displays `:9`–`:16` on each station — see
  [references/stations.md](./references/stations.md)).

## 5. Memories Initialization (backdoor load with ECC)

Backdoor load is available (via offline script) to generate ECC over an original hex file and
backdoor-load it into PLDM. Supported: **FLA ROM**, **CM7 ITCM**, **SP SPRAM**.

1. Copy the offline scripts from `/vlsi/proj/iridium/emul/pldm/setup/ecc` to your user area:
   `logical_yml_to_physical_yml.py`, `ecc_eqns.txt`, and the relevant `*.yml` (per ROM/RAM).
2. Edit the yml to point at your **origin** hex file (the hex before ECC generation).
3. Generate ECC + backdoor hex files (example for SP SRAM):
   ```bash
   ./logical_yml_to_physical_yml.py -verif_mode -yml babel_sp_sram.yml -outdir .
   ```
4. Copy and adjust the load file to match your generated hex-with-ECC files, e.g. SP SRAM:
   `/vlsi/proj/iridium/emul/pldm/setup/ecc/sp_sram_load.qel`

## 6. Debug Hardware

### UART (from the EDK host, USB-connected)
```bash
minicom -D /dev/ttyUSB0 -b 300
```

### JTAG (Lauterbach Trace32)
Available only on EDK hosts **edk0, edk2, edk4** (in both PLDM Z2 and Z3).
From that EDK server (debugger on USB):
```bash
/opt/trace32_2025_09_000186004/bin/pc_linux64/t32marm64   # or the alias: t32marm
```

## 7. Debug Tools & Useful Paths

- **PCIe TLP monitor SDL**: `/vlsi/proj/<proj>/emul/pldm/setup/pciesb_gen6_tlp_mon_fc_scale_en.tdf`
- **CM7 CPU PC** hierarchy path (for triggers / PC tracking):
  ```
  {iridium_testbench.iridium_chip.\f.psoc_top .psoc_cls.\f.psoc_wrap .\f.cpu_macro .\f.psoc_cpu_wrap .\cpu_0_.cpu_top .\f.cpu_core .\f.eip_arm_wrap .\f.u_cm7 .u_cortexm7.u_top_sys.u_core.u_cm7_dpu.pc_ret[31:0]}
  ```
- Verisium user guide: see the VLSI SharePoint (`verisium_cdadoc.pdf`).

## Notes

- "Running Regressions", "CSR DB Alignment" are placeholder sections on the source page (no content
  yet) — check the live Confluence page if you need them.
- The source page is actively maintained; verify station IPs/credentials against it if a connection
  fails.
