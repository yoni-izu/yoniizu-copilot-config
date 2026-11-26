# Detached Operations — never lose work to a dropped connection

Lab sessions die constantly: VPN drops, SSH hangups, terminal timeouts, VS Code
reloads, agent-session restarts. A foreground command that dies takes its output
*and its exit code* with it — and for `reset` / `flash` you then cannot tell
whether it ran. Re-running blind is how you brick things.

**Rule: nothing that touches a machine runs in the foreground.** Launch → get a
job id back immediately → poll. One record format for every runner: the shared
job record `~/.jobs/<id>/` ([format](../../../lib/jobs.md), owners
`~/.copilot/bin/job` and `~/.copilot/lib/jobs.py`).

| Where the work runs | How to launch | Record |
|---|---|---|
| Workstation (local) | `job run -T <target> …` | `~/.jobs/<id>/` here |
| ELS tray BMC | `job run --kind bmc -T <target> -- mctl -t <target> bmc '<cmd>'` | `~/.jobs/<id>/` here; **nothing on the BMC** |
| VMs / remote build hosts | `mctl build`/`test`/`run` (+ `*-status`) | `~/.jobs/<id>/` on the target, mirrored here |

```
job run [-t ID] [-T TARGET] [--kind K] [--cwd DIR] [--label TEXT] [--plain] [--] '<cmd>'
job status [ID] | log ID [N] | wait ID [SECS] | list [--tsv] [--host H] [--since DAYS] [--runner R]
job busy --cwd DIR | kill ID [--wait S] | clean [DAYS]        # clean defaults to 30 days
```

Each record holds `cmd.sh`, `log`, `pid`, `started`, `runner`, `kind`, `host`,
optional `target`/`cwd`/`label`, and `rc` + `finished` once done. `rc` is the
authority; the `__JOB_DONE__ rc=N` log sentinel is kept for old readers. The
job's environment carries `JOB_ID=<id>`. Lab homes are NFS-shared, so a record
from another lab box can appear here: it lists as `REMOTE` (status is only
judged on its own `host`) and cannot be killed from here.

**BMC work keeps no state on the BMC.** The job runs on the workstation and
holds the `mctl … bmc` session open. If that link drops mid-command the remote
command may be killed with it and the result is **unknown**: read the device
state before re-running anything. Flash, power and reset stay with
`fw-flasher`'s own path. `bmcjob` (both the local client and the BMC-side
script) is deprecated: it still works and warns on `run`; the portal still
lists and can stop old BMC jobs.

**Always name the machine: `job run -T <target> …`** (or export `PILOT_TARGET`).
It writes `<id>/target`, which the Team Portal's Machines job view uses to put
the job under that machine. Without it the portal guesses from the command
(`-t <target>`, a BMC or host name) or the id, and otherwise lists the job as
unattributed.

### Jobs as data (portal / other skills)

`scripts/pilot_jobs.py` is the read-only owner API for pilot's jobs (runner
`pilot`, plus legacy BMC `bmcjob` dirs):

```
pilot_jobs.py list [--days N] [--no-bmc]   # JSON rows: id, source, target, status, rc, ...
pilot_jobs.py refresh-bmc <target>          # one read-only listing of legacy /var/lib/bmcjobs over mctl bmc
pilot_jobs.py stop <id> [--force]           # TERM the job's process group after verifying its pid
```

Ids are `pj:L:<id>` (local record) and `pj:B:<target>:<TAG>` (legacy BMC). Legacy
BMC jobs are only read when `refresh-bmc` runs (the portal does so on a Machines
refresh); the reading is cached in `~/.pilot/bmcjobs-cache/`, a failed read keeps
the old rows and records the error, and a `running` reading older than 10 min is
shown as unverified (older than 6 h as `unknown`) and cannot be stopped until
refreshed. `refresh-bmc` needs non-interactive BMC access through
`mctl -t <target> bmc` (`~/.pilot/bmc.pw`); targets without an mctl BMC entry are
skipped. After a `Permission denied`, automatic (portal) refreshes skip that
target for 6 h so a wrong password cannot lock the BMC account; a manual
`refresh-bmc` still tries.
`stop` (and `job kill`) writes a `stopped` marker before the TERM, so the killed
job lists as `cancelled` rather than `died`. `stop` refuses disturb-critical
commands (flash, fwloader, BMC reset/power, power-cycle, 48 V, mux, GPIO); only
the CLI's `--force` overrides that, and the portal never forces. A record may
also list `unstoppable` phases that both `job kill` and `stop` refuse.
`pilot run --detach` host jobs (`~/.pilot/jobs/` on a lab host) are not covered.

## Reading results

- `status` → `RUNNING` / `DONE rc=N` / `CANCELLED` / `DIED` (no exit code = killed
  or rebooted) / `REMOTE` (recorded on another host).
- **A timeout on your poll means "not finished yet", NOT "it failed."** The work
  runs on the far side, detached from you. Re-poll — never relaunch a reset or a
  flash because a poll timed out.
- Jobs survive you: come back after a VPN outage and `job status` still has the
  exit code and full log.

## Why it works (don't "simplify" these)

```bash
JOB_ID=$id setsid nohup bash -c 'trap "" HUP; bash "$0/cmd.sh"; rc=$?; echo $rc > "$0/rc"' "$jd" \
    > "$jd/log" 2>&1 < /dev/null &
disown
```

- `setsid` makes the child a **session leader with no controlling terminal**, so a
  terminal/SSH death cannot deliver SIGHUP to it. Verify: `ps -o pid,ppid,pgid,sid,tty`
  shows `PGID==SID==PID` and `TT=?`.
- `trap "" HUP` is inherited by the real workload, covering the case where a HUP is
  sent explicitly to the group.
- `< /dev/null` guarantees it can never block on a prompt no one is watching.
- The command is read from `cmd.sh` rather than interpolated, which removes every
  nested-quoting hazard (the usual source of "it ran but did nothing").
- `cd` must be a **separate statement**: `cd $ws && setsid ... &` backgrounds the
  whole compound and the child dies instantly (learned in `mctl`).

## BMC specifics (legacy `bmcjob`)

`bmcjob` is deprecated (see above); these notes apply only to reading old jobs.
It lives in **`/usr/local/bin`** with state in `/var/lib/bmcjobs/` (not `/tmp`,
which is tmpfs), and non-interactive SSH needs the absolute path
`/usr/local/bin/bmcjob`.

## Driving OAM consoles

The only proven console input path is **from the workstation**:
`ssh -tt -p 220<n> root@<bmc>`. Attempts to drive consoles *from the BMC* fail:
`obmc-console-client` needs a controlling terminal and still will not forward stdin,
and BusyBox has no `timeout`/`script`, while its Python lacks `pty`/`termios`.

Use **`oam_exec`** ([scripts/oam_exec.sh](../scripts/oam_exec.sh)) — it wraps that
transport and fans out across the tray, one OAM at a time (never in parallel: one
client per console). Pair it with `job` to get detachment too:

```bash
# whole tray, detached; returns a TAG at once
job run -t sweep "oam_exec -b <bmc> -o ~/pilot-evidence/<run> \
    'dwc_ep_ctl link_status' 'dwc_ep_ctl regs_dump PEP_CTRL_CORE 0x30 0x20'"
job wait sweep 300
```

```
oam_exec [-b BMC] [-o OUTDIR] [-d "0 3 5"] [-w SETTLE] [-t TIMEOUT] 'cmd' ['cmd'...]
```

- Writes one `oam<N>.txt` per device plus `_meta.txt` (timestamp, BMC, commands) so
  a capture is self-describing. Default outdir is `~/.pilot/evidence/oam-exec/<UTC>`.
- Sends two CRs first — the shell prints no prompt unprompted.
- Flags any device returning `<64 B` as `-- SILENT` and prints the exact
  `bmccli device reset -d <i>` to recover it (run it as a `--kind bmc` job). A silent console is
  **data, not an error**, so the exit status stays 0.
- Raise `-t` for slow commands (`ltssm_logger_dump` can exceed the 40 s default).
- Always `ltssm_logger_dump` **before** `ltssm_logger_clear`.

Also: **one client per console.** A second connection attaches and receives nothing,
silently. Stale sessions from a dropped VPN keep holding the port, so a console can
look "dead" when it is merely taken. Check on the BMC with
`netstat -tn | grep ":22[0-9][0-9]" | grep -i estab` and compare the peer IP to your own.
