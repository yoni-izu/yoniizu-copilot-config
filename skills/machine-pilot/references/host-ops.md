# Host Operations — what `pilot` runs, and the manual fallback

Lab login: `yoni.izutskiver@<host>` (`*-csr`), NFS home `/users/home/yoni.izutskiver`.
`pilot` multiplexes ssh (`~/.pilot/cm-*`, ServerAlive 30 s) and strips the SAT banner.
Remote payloads never carry `rtk`.

| Verb | Runs remotely | Notes |
|---|---|---|
| `probe` / `devices` | [probe_remote.py](../scripts/probe_remote.py) via `python3 -` → JSON | sysfs: `/sys/bus/pci/devices/*/{vendor,device,current_link_*,max_link_*,driver,config[4:6],resource}`; switch = `1000:c040`/`1000:00b2` present; alloc = `/etc/motd` "allocated to:"; `--json` for raw |
| `dmesg` | `dmesg -T` (unprivileged, `dmesg_restrict=0`), filtered locally | default grep: `atl \|el_pci\|el_ctl\|el_accel\|208b\|1d0f\|7596\|FW channel\|PSOC\|fw_boot\|SEI\|split read\|poison`; `--since HH:MM` brackets an experiment; `--save T` stores the full log |
| `run 'CMD'` | `bash -lc CMD` (login shell → `~/.local/bin/octl` on PATH) | prints `rc` + bounded tail; `--detach TAG` → `setsid nohup`, log `~/.pilot/jobs/TAG/run.log`; `status TAG` polls — never sleep-loop |
| `ws` | lists `~/projects/*/*[/*]/.repo/metadata.json` → manifest, version, revision; `*` = integration manifest | pick the `*` row for company tools |
| `exec 'STAR-CMD' [--ws PATH]` | `cd WS && octl ws exec 'STAR-CMD'` in a login shell; WS defaults to the `*` integration ws | touch-class (may start `fw_ctl`, load drivers); `--args` go inside the quoted string |
| `nfs deployments\|notes\|hash\|ls` | `ls -lt ~/a1_power_on_images`, `CHANGE_NOTES.md`, `stat`+`sha256sum`, `ls -la` | identify images by hash + notes, never by branch tip |
| `evidence TOPIC` | probe JSON+text, `dmesg -T`, `lspci -nn/-tv/-vv` of our devices, `lsmod`+`dkms status`, optional BMC `/var/log` listing, optional tty capture | `~/.pilot/evidence/<station>/<DDMMYYYY_HHMMSS>_<topic>/` + `sha256sums` |

## Reading the probe

- `alloc=<user>` — SAT owner; `holders=` — live `fw_ctl`/`minicom`/tests on the box.
  Both mean *someone is using it*.
- Device line: `bdf vid speed xwidth drv mem bar0 bar2 [!]`. `!` = speed/width below
  max or `Unknown` → untrained/degraded link (per-OAM finding → `bug-triage` + `pcie-ep-ip`).
  Healthy A1 on a tray: `64.0G x8`. `drv=-` + `mem=off` = enumerated, no KMD bound.
- `vid` mixes `1d0f` and `208b` on one tray (seen on srv17) — per-module, not per-tray.
- Tray BDFs **change across reboots**; derive OAM↔BDF per boot, never from memory.
- `inv=root-only` = `/var/log/atl/fw_ctl/pcie_inventory.json` exists but fw_ctl runs as
  root there; `opt_atl=no` is normal — `/opt/atl` is the integration workspace's
  `build_artifacts/opt/atl`, mounted at `/opt/atl` **inside the `octl ws exec` container**,
  never on the bare host.

## Integration workspace — where company tools run

The **integration manifest** (`integration-ci.xml`, `type="integration"`, one per project
branch, e.g. `projects/iridium/a1_power_on_branch/integration-ci.xml`) pins the **latest
and greatest of every SW component** as versioned artifacts — KMD `drv-ci`, `psoc-fw-ci`,
`regcli-ci`, `simulator-ci`, `kernels-ci`, the CSR packages, devops tooling … (≈60
entries). A workspace initialised from it is therefore the right `cwd` for `octl ws exec`
star targets (`run_fw_ctl`, driver load, qual/host tools) and any "company tool": it sees
all components at matching versions instead of whatever one dev workspace happens to hold.

- Deployed on the shared NFS home (visible from every `*-csr` box):
  **`~/projects/iridium/a1_power_on_branch/ws-integration-ci`** (`pilot ws` marks it `*`).
  Artifacts-only layout: `build_artifacts/opt/atl/{fw/scripts/{fw_ctl,fw_loader.py,
  host_lib,rpe,pcie_safe_reset,pldm,jtag,el_log_tools},device_fw/arm/<token>/…,dkms,
  el-smi,el-qual,cpld,hw_system_tests,…}`; no source repos; `.repo/metadata.json` records
  manifest/version/revision.
- Use it: `pilot -t <st> exec 'run_fw_ctl --args "variant=asic interface=pcie"'` (touch
  class — it starts a root console in a container on the device) or for one-shots
  `pilot -t <st> exec 'run_fw_ctl --args "variant=asic skin=cli cmd=pcie devices"'`.
  Read-only inspection of its tooling needs no container: `pilot -t <st> run 'ls
  ~/projects/iridium/a1_power_on_branch/ws-integration-ci/build_artifacts/opt/atl/fw/scripts'`.
- Fresh one: `cd ~/projects/<prj>/<branch> && octl ws init -p iridium -m integration-ci
  [--version <ver>] [--artifacts-only]` (touch class, large download — ask first).
  Check staleness with `pilot ws` (version stamp `1.0.0-DDMMYYYY-HHMMSS`) against
  `~/octl/manifests/projects/iridium/<branch>/integration-ci.xml` locally.
- Do not treat loose files in its root (e.g. a `spi_flash_image_arm_asic.bin`) as
  manifest-pinned artifacts — identify them by hash (`pilot nfs hash`) and ask.

## Drivers / tools (facts)

- KMD `el_pci.ko` from DKMS `atl-dkms` (`/lib/modules/$(uname -r)/updates/dkms/`); dmesg
  tags the device `atl 0000:<BDF>:`. Load/unload needs root → the user. `fwloader` PCIe
  refuses to open while `el_pci`/`el_ctl` are loaded (`fw-flasher`).
- `fw_ctl` on a multi-device host: `pcie devices`, `--bdf <BDF>`, `iface init --all` then
  `iface bdf <BDF>`, `--all`/`--device a b` fan-out; `--variant asic` is mandatory on
  `sa-*` hostnames. Observe verbs: `host_lib rom-status all`, `boot fw-sts`, `boot status`,
  `sysreset status`. Everything else is disturb-class.
- dmesg signatures: `atl device found` (enumerated) · `Waiting for firmware boot` →
  `probe … failed with error -110` (FW didn't boot) / `-5` (I/O) · `Failed to send PSOC
  message … rc -5`, `Failed to init FW channel`, `component ready`, `heartbeat` (FW-channel
  stages) · `Failed to send fini command` … `el_pci: driver removed` (teardown with a dead
  channel) · `SEI`, `split read response`, `poison` (RAS → `ras-manageability`).

## NFS map

`/users/home` ← `*:/homes` (NFSv4.2 `soft`, shared by all `*-csr` boxes) · `/validation`
(`/validation/<first.last>`, `/validation/artifacts`). In `~`: `a1_power_on_images/
<DDMMYYYY_HHMMSS_topic>/asic/{spi_flash_image_arm_asic.bin,CHANGE_NOTES.md}` (fw-deploy),
`bar_images/`, `for_c2c_test/`, `po/dump/`, Lauterbach `*.cmm`, `fw_host_cli_*.exe`,
`minicom.log` (minicom's hangup log — **not** a capture). Writes are touch-class; never
`--delete`/`sudo`; a hanging `ls` is the soft mount, not permissions.

## Manual fallback (when `pilot` lacks a verb)

```bash
rtk proxy ssh yoni.izutskiver@<host> '<cmd> 2>&1 | tail -n 40'      # bounded
rtk proxy mctl -t <t> sh ': || cd "$HOME"; <cmd>' "$ws"            # mctl targets; first cd warning is expected
```
