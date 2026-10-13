# ELS Tray BMC Cookbook (ELS100 / UBB, AST2600 OpenBMC)

Source: Confluence RPI "Platforms Guides → ELS100 BMC Commands" and "OAM A1 SPI
Firmware Update Procedure" (read 2026-10-04). Login `root@bmc-sa-els<NN>-csr`,
password auth. Everything below runs **on the BMC**.

## Credentials — one password for ALL BMCs

Every BMC in the lab uses the **same** `root` credentials, so the password lives
once in **`~/.pilot/bmc.pw`** (mode `0600`) and works for any `bmc-sa-els<NN>-csr`.
There is no need for a per-station `els<NN>.pw`.

Load it into the environment and let `sshpass` read it from there — **never put a
password in `argv`** (it is visible in `ps`), never echo it, never paste it in chat:

```bash
export SSHPASS=$(cat ~/.pilot/bmc.pw)
sshpass -e ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
        -o LogLevel=ERROR -o ConnectTimeout=15 root@<bmc> '<command>'
```

`sshpass -e` takes the password from `$SSHPASS`; `$(cat …)` strips the trailing
newline. The same `-p 220<n>` form reaches an OAM console. If the file is missing,
ask the user to create it — do not guess or retry a password.

Classify before you type: **observe** rows are free; **disturb** rows need explicit
approval and usually belong to another skill.

| Need | Command on the BMC | Class |
|---|---|---|
| Component versions (BMC, DC-SCM CPLD, UBB CPLD primary/secondary, retimers, PCIe switches) | `oem_show_version` | observe |
| FRU of the DC-SCM | `oem_fruid-util BMC_DC_SCM` | observe |
| FRU of the UBB | `oem_fruid-util EA100_UBB` | observe |
| UART console to OAM *n* | `/usr/bin/obmc-console-client -c /etc/obmc-console/client.220<n>.conf` (escape `~.`) | observe (read) / touch (typing) |
| Persisted UART logs | `ls -la /var/log \| grep -iE 'console\|uart\|oam\|pcie'` then `tail -n 400 <file>` | observe |
| Sled ID (pod) | secondary CPLD register `0x09` (i2c — bus/addr per platform doc) | observe |
| Redfish firmware inventory (is the OAM visible?) | from any host: `curl -u root:*** "http://<bmc>:8443/redfish/v1/UpdateService/FirmwareInventory/0?format=json"` | observe |
| Host-style power cycle of the sled | `ipmitool power cycle` | **disturb** |
| Full 48 V sled power cycle (all components incl. BMC + AO domains; replaces a PDU AC cycle; needed after a UBB CPLD update) | `i2cset -y -f 20 0x31 0x2c 0x02` (primary UBB CPLD, reg `0x2c` ← `0x02`) | **disturb** |
| Give the USB mux back to the BMC after RPE used it | `gpioset $(gpiofind USB_MUX_EN)=1` (`=0` + `systemctl reboot` is the recovery sequence) | **disturb** |
| Take SPI mux for the BMC + probe the OAM flash | `gpioset $(gpiofind SPI_MUX_EN)=1; sleep 3; spi_flash_test -d /dev/spidev2.0 -b 8 -a 0x25 probe` | **disturb** — `fw-flasher` territory |
| OAM A1 SPI update over Redfish (`FirmwareUpload/A1`, `ELUpdateService.UpdateA1`, task polling) | see the Confluence procedure | **disturb** — use `fw-flasher` |

Facts worth remembering:
- A sled has **two** BMC names in DNS when attached to a server: `bmc-sa-els<NN>-csr`
  (tray/UBB BMC — the one above) and `bmc-sa-srv<NN>-csr` (the server's own BMC,
  `ipmitool` for the *server*). Verify with `hostname` after login.
- `oem_show_version` output is the component-version baseline to paste into a session
  note before/after any tray FW/CPLD update.
- After `systemctl restart bmcweb` (autodetect workaround) Redfish sessions drop —
  log in again.
- SPI probe results `0x000000` / `0xFFFFFF` / no output = mux not owned by the BMC or a
  dead SPI path — **do not flash**.
- BMC flash device for the OAM is addressed by label `/dev/mtd/soc-nv-flash`
  (`fw-flasher` project reference), never by a bare `/dev/mtdN`.

## `bmccli` — A1 BMC management orchestrator CLI

`/usr/bin/bmccli`, "bmccli v1". Present on `bmc-sa-els35-csr` (hostname `els100`),
surveyed 2026-10-04. A thin client onto a BMC-side daemon — it is the *supported*
front end for the raw `gpioset` / `i2cset` / `spi_flash_test` recipes above, so prefer
it when it exists. Grammar: `bmccli <group> <verb> [args] -d <idx|all>`.
Device-scoped verbs **require** a target: `-d <idx>` (0-based OAM index) or `-d all`.
`bmccli help <command>` gives per-leaf usage; `bmccli search <word>` finds commands.

> **`-d all` is NOT "every device" for `device reset`.** It prints *"reset requested
> for all **ready** devices"* and silently **skips anything not in `state=READY`** —
> so it is a no-op exactly when devices are wedged (wedged usually ⇒ `INIT_FAILED`).
> To recover a stuck device use an **explicit index**: `bmccli device reset -d 3`.
> Confirmed 2026-10-05: `-d 0` revived a dead console that `-d all` had ignored.
> A single-device reset can take **>120 s** — run it via `bmcjob`, never foreground.

| Command | Does | Class |
|---|---|---|
| `version` | daemon control version | observe |
| `sled-id` | physical sled (compute tray) ID — the supported form of the CPLD `0x09` read | observe |
| `help [<cmd>]` / `search <word>` | help / keyword lookup | observe |
| `device status [-d <i\|all>]` | list devices + lifecycle state | observe |
| `device oam-status [-d <i\|all>]` | per-OAM live status: `present`/`pwrgd`/`platform_thermtrip` (platform HAL masks) + `thermtrip`/`boot`/`throttle` from the OAM CPLD; masks the board doesn't support read `N/A`; defaults to all | observe |
| `cpld version -d <i\|all>` | running EA100 CPLD version | observe |
| `flash version -d <i\|all>` | A1 OAM FW version from the FPT header — **3 lines per device** (FPT A/B/C, each tagged with the flash offset it came from) with `fw_version` raw + decoded `major.minor.patch`, `fpt_uuid`, `build_version` | observe |
| `capture status` | FSM/SPI pcap capture state | observe |
| `capture start [<path>]` / `capture stop` | start/stop FSM+SPI pcap (defaults to the configured `fsm_pcap_path`) | touch |
| `device perst -d <i\|all>` | pulse A1 `PERST#` (PCIe reset) via the OAM CPLD | **disturb** |
| `device reset -d <i\|all>` | reset device(s) — reboot from the current image | **disturb** |
| `device power on\|off\|cycle -d <i\|all>` | OAM CPLD's own power control | **disturb** |
| `device platform-power on\|off\|cycle -d <i\|all>` | baseboard per-OAM power path (platform hook) | **disturb** |
| `device pwr-limit pwrbrk on\|off \| pwrrdt <0-3> -d <i\|all>` | per-OAM power brake / power-reduce level | **disturb** |
| `cpld update <sea> <sed> -d <i\|all>` | reprogram EA100 CPLD flash (SSPIEm); **synchronous, blocks** until the daemon finishes | **disturb** |
| `flash update <img> -d <i\|all>` | program the A1 boot NOR flash; **synchronous, blocks** | **disturb** — `fw-flasher` territory |

Notes:
- `flash version -d all` is the cheapest cross-tray "what FW is actually on each OAM"
  readout — no host, no PCIe, no driver needed. Three FPTs per device means a version
  mismatch between A/B/C is a finding, not an error.
- `device oam-status` is the first thing to run when the host shows `k < 8` devices:
  it separates "not present / no power-good" from "present but not trained".
- `-d` is a **0-based OAM index**, not a BDF and not the UART port number
  (UART ports are 2200+index).
