# Sled BMC over Redfish — `fw_ctl bmc …` (asic only)

The newest out-of-band flashing path: program the SPI flash of **every A1 chip
on a sled in one command**, from any machine with network reach to the BMC. No
PCIe link, no USB cable, no lab-host login required for the *load* itself.

Added in `fw_ctl` 2.x (`fw_ctl/bmc_cmds.py` on top of the stdlib-only
`scripts/bmc` Redfish client). Verified live against a compatible sled BMC with
`fw_ctl 2.3.0`.

> **This is still a flash.** Everything in
> [verification.md](./verification.md) applies — the BMC gives you a cleaner
> delivery mechanism, not permission to skip the KNOW → FLASH → VERIFY
> transaction.

---

## The BMC model (what the verbs are actually talking to)

| Redfish object | Meaning |
|----------------|---------|
| `Chassis/OAM_<n>` | one A1 chip in sled slot `n` |
| `UpdateService/Oem/EL/FirmwareUpload/A1` | image upload (`application/octet-stream`) |
| `UpdateService/Actions/Oem/ELUpdateService.UpdateA1` | start the update — **all chips together** |
| `TaskService/Tasks/OAM_<seq>` | every action creates a task. **`<seq>` is a global task counter, NOT a chip index** |
| `UpdateService/FirmwareInventory` | per-slot VR version (`OAM<n>_XDPE0`); `NA` = empty slot |

Hard constraints to design around:

- **The BMC runs exactly one task at a time.** A stuck task blocks every later
  one (they sit in `New`). `bmc load` / `bmc reset` refuse to start while a task
  is pending; `--force` submits anyway (it just queues behind).
- **A firmware load updates every chip.** There is no per-chip load.
- **There is no reset-all.** `bmc reset --all` resets chips one at a time,
  waiting for each task.

---

## Connection & credentials

```bash
export BMC_HOST="your-bmc-host"     # else pass --bmc <name> on every verb
export BMC_USER=...                  # default: root
export BMC_PASSWORD=...              # load from your approved credential store
```

The BMC is a **network peer**, so `bmc` verbs work on any `--interface`. Only
`bmc load --boot` needs `pcie` (it boots the chips over PCIe afterwards).

TLS is not verified by default (`--verify` in the standalone `python3 -m bmc`
CLI). Never put the password on a command line or in a logged script — export it.

---

## Verbs

| Verb | Args | Example |
|------|------|---------|
| `bmc load` | `<image> [--bmc B] [--boot [--expect N] [--fw-sts-timeout S] [--die-count 1\|2\|4]] [--timeout S] [--no-wait] [--force]` | `bmc load ./a1.bin --bmc your-bmc-host --boot --die-count 4` |
| `bmc chips` | `[--bmc B] [--refresh]` | `bmc chips --bmc your-bmc-host` |
| `bmc reset` | `(--oam N […] \| --all) [--bmc B] [--refresh] [--type T] [--timeout S] [--force]` | `bmc reset --oam 0 3` |
| `bmc tasks` | `[--bmc B] [--last N]` | `bmc tasks --last 20` |
| `bmc task` | `<id> [--bmc B] [--wait] [--timeout S]` | `bmc task 6 --wait` |
| `bmc info` | `[--oam N […] \| --all] [--bmc B] [--refresh]` | `bmc info --all` |

Defaults: `bmc load --timeout 1800`, `bmc reset --timeout 600 --type On`,
`bmc task --timeout 1800`, `bmc tasks --last 10`,
`--fw-sts-timeout 30`, `--die-count 1`.

### `bmc load` — what it does

1. Refuses if the BMC task queue is busy (unless `--force`).
2. Uploads `<image>` (absolute path resolved & existence-checked locally first).
3. Starts `UpdateA1` → programs **every chip's SPI flash**.
4. Waits for the task, printing its progress (`--no-wait` returns once the task
   exists; mutually exclusive with `--boot`).

### `bmc load --boot` — load **and prove it booted** (pcie only)

This is the part that makes it a complete flash transaction. After the load it
runs, aborting on any failure on any chip or die:

1. `iface init --all` — bring every endpoint up (BARs + bring-up exe).
2. `host_lib boot clear fw-load-done --all --die 0`
3. `host_lib boot clear fw-sts --all --die 0`
4. **check every chip reads `fw_sts = not_started`** ← this is what makes the
   final `done` proof of a *fresh* boot rather than a leftover state
5. `host_lib sysreset platform --all --die-count N` (die `N-1` → die 0; die 0
   last, it carries PCIe)
6. poll `host_lib boot fw-sts --all --die 0` until **every** chip reads `done`
   (`--fw-sts-timeout`, default 30 s)

`--expect N` makes every step assert it saw **exactly N chips** — use it. On an
8-OAM tray, `--boot --expect 8` turns "some chips silently absent" into a
failure instead of a false pass.

Same flow as the standalone `scripts/bmc_fw_flash_boot.py` (same `--die-count`).

### Chip discovery & the cache

`bmc chips` asks the firmware inventory: slot `n` is fitted when `OAM<n>_XDPE0`
reports a version (`NA`/`N/A`/empty = empty slot); falls back to the `Chassis`
`OAM_<n>` list. A single-chip board lists `OAM_XDPE0` → `OAM_0`. **Slots can be
non-contiguous** (e.g. `OAM_0, OAM_7`).

Discovery takes seconds, so the list is cached **per BMC** in
`$XDG_CACHE_HOME/fw_ctl/bmc_chips.json` (default `~/.cache`). `bmc chips`,
`bmc reset --all` and `bmc info` reuse it and print its age; `--refresh`
re-discovers. An empty answer is never cached.

> **Stale-cache trap:** after fitting/removing a chip, the cached list is wrong
> and `--all` will silently act on the old set. Run `--refresh` whenever the
> tray's population may have changed.

If the inventory shows nothing and the chassis list is empty, the verb fails and
tells you to pick chips with `--oam`.

---

## Live session example

```text
ctl:pcie:asic> bmc chips --bmc your-bmc-host
BMC your-bmc-host: 8 chip(s) (discovered)
OAM_0 … OAM_7

ctl:pcie:asic> bmc tasks --bmc your-bmc-host --last 5
BMC your-bmc-host: queue idle
task   name      state      pct   status  message
OAM_7  UpdateA1  Completed  100%  OK      completed
OAM_6  UpdateA1  Completed  100%  OK      completed
…

ctl:pcie:asic> bmc info --oam 0 --bmc your-bmc-host
BMC your-bmc-host: 1 chip(s)
oam    model  state    health  reset types
OAM_0  EA100  Enabled          On
```

Notes from that run: the ELS tray reports `model EA100`, and the **only accepted
reset type is `On`** — `bmc reset --type ForceRestart` would be refused there.
`bmc info` is the way to find out before trying.

---

## Choosing this path vs. the others

| Situation | Use |
|-----------|-----|
| Whole sled/tray, one image, out-of-band | **`bmc load`** (+ `--boot --expect N`) |
| One board on a host with a working PCIe link | `flash write` over `--transport pcie` |
| Several endpoints on one host | `flash write --all` (N concurrent fwloaders) |
| No PCIe, no BMC — just a USB cable | `flash write --transport ftdi-spi` |
| **You have a shell on the BMC** | **`bmccli flash update <img> -d <device\|all>`** (simplest) |
| BMC shell, direct MTD (low level) | `fwloader -d /dev/mtd/soc-nv-flash` (see fwloader-cli.md) |
| Dev iteration, don't touch flash | `stream` |

`bmc load` and the BMC-shell MTD path are **both** "flash from the BMC", but they
are different tools: `bmc load` drives the BMC **remotely over Redfish** and hits
every chip; the MTD path is a local write on one flash from inside the BMC.

---

## Failure modes & guards

| Symptom | Meaning | Guard |
|---------|---------|-------|
| `no BMC: pass --bmc <name> or export BMC_HOST` | no host resolved | set `$BMC_HOST` |
| load/reset refused, "queue busy" | an earlier BMC task is still pending (possibly stuck) | `bmc tasks` → `bmc task <id> --wait`; `--force` only once you know what's queued |
| `BMC … reports no chip` | empty inventory **and** empty chassis list | `--refresh`, then `--oam <n>` explicitly |
| `--all` acted on the wrong set | stale `bmc_chips.json` cache | `--refresh` after any tray re-population |
| `--boot` refused | not on `pcie` | `iface use pcie` first |
| `fw_sts not cleared (want not_started)` | step 4 failed — the clear didn't take | real failure; do not re-run blindly |
| `fw_sts timeout after Ns; not done: …` | some chip never reached `done` | a **failed** flash/boot — capture `host_lib rom-status all` for that chip |
| "N/M device(s) seen" | fewer chips than `--expect` | chips missing/down — not a pass |
| `task … Exception/Killed/Cancelled` | BMC task failed | `bmc task <id>` for the message |

### Debugging from inside the BMC — `journalctl -u orchestrator`

Redfish only gives you the task's final state/message. The **orchestrator**
service is what actually drives the A1 update on the BMC, so its journal is the
real log of the flashing process (per-chip progress, SPI/MTD errors, why a task
went `Exception`/`Killed`). SSH to the BMC and read it:

```bash
ssh root@$BMC_HOST journalctl -u orchestrator -n 200 --no-pager   # recent
ssh root@$BMC_HOST journalctl -u orchestrator -f                   # follow a running load
ssh root@$BMC_HOST journalctl -u orchestrator --since '10 min ago' --no-pager
```

Use it whenever a `bmc load`/`bmc reset` task fails, hangs, or the queue is stuck
— **before** re-running the flash. Capture the relevant excerpt for the ticket.

**Still do the version read-back.** `bmc load` proves the *task* completed and
(with `--boot`) that every chip reached `fw_boot_sts = done`. It does **not**
assert the running build ID equals the one you intended. Finish with
`host_lib rom-status version --all` and compare to the expected build ID.

---

## `bmccli` — the simple BMC-shell path (preferred when you are on the BMC)

If you already have a shell on the BMC, this is the shortest, preferred way to
flash — no Redfish, no fwloader, no MTD label lookup:

```bash
bmccli flash update <image> -d <device>     # one device
bmccli flash update <image> -d all          # every device on the tray
```

It wraps the whole BMC-side update (device selection + SPI flash program), so
prefer it over `fwloader -m flash -d /dev/mtd/...` on the BMC. Use `fw_ctl bmc
load` instead when you are **not** on the BMC (remote, over Redfish).

> **Still a flash.** KNOW → FLASH → VERIFY applies unchanged: copy the image to
> the BMC and `sha256sum` it there against the source, then after the update do
> the version read-back (`host_lib rom-status version`) and the
> `fw_boot_sts → done` check. If the update fails or hangs, read
> `journalctl -u orchestrator` on the BMC before re-running.

---

## Standalone CLI (same client, no fw_ctl)

```bash
python3 -m bmc --host <bmc> load   <a1.bin> [--reset-oam 0 1] [--no-wait] [--force]
python3 -m bmc --host <bmc> upload <a1.bin>
python3 -m bmc --host <bmc> update [--wait]
python3 -m bmc --host <bmc> reset  (--oam 0 [1 …] | --all) [--type On]
python3 -m bmc --host <bmc> chips | tasks [--last N|--all] | task <id> [--wait] | info --oam 0
python3 -m bmc --host <bmc> get /redfish/v1/TaskService/Tasks     # raw Redfish GET
python3 -m bmc lookup <compute-host> […] [--json]                 # NetBox: host -> BMC + NUC
```

`bmc lookup` resolves a compute host to its BMC and NUC via NetBox
(`$NETBOX_URL`) — the fastest way to find the `--bmc` name for a lab machine.
