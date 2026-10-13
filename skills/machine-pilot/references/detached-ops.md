# Detached Operations — never lose work to a dropped connection

Lab sessions die constantly: VPN drops, SSH hangups, terminal timeouts, VS Code
reloads, agent-session restarts. A foreground command that dies takes its output
*and its exit code* with it — and for `reset` / `flash` you then cannot tell
whether it ran. Re-running blind is how you brick things.

**Rule: nothing that touches a machine runs in the foreground.** Launch → get a
TAG back immediately → poll. Three runners, one interface:

| Where | Tool | State dir |
|---|---|---|
| Workstation (local) | `job` (`~/bin/job`) | `~/.jobs/<TAG>/` |
| ELS tray BMC | `bmcjob` (`/usr/local/bin/bmcjob`) | `/var/lib/bmcjobs/<TAG>/` |
| VMs / remote build hosts | `mctl` (`build`/`test` + `*-status`) | per-target job dir |

```
job    run [-t TAG] '<cmd>' | status [TAG] | log TAG [N] | wait TAG [SECS] | list | kill TAG | clean [DAYS]
bmcjob run <TAG> '<cmd>'    | status [TAG] | log TAG [N] | wait TAG [SECS] | list
```

Each job dir holds `cmd.sh`, `log`, `pid`, `started`, and `rc` once finished.
Completion is also marked in the log by a `__JOB_DONE__ rc=N` /
`__BMCJOB_DONE__ rc=N` sentinel, so a job is still readable even if the PID is gone.

## Reading results

- `status` → `RUNNING` / `DONE rc=N` / `DIED` (no exit code = killed or rebooted).
- **A timeout on your poll means "not finished yet", NOT "it failed."** The work
  runs on the far side, detached from you. Re-poll — never relaunch a reset or a
  flash because a poll timed out.
- Jobs survive you: come back after a VPN outage and `job status` / `bmcjob status`
  still has the exit code and full log.

## Why it works (don't "simplify" these)

```bash
setsid nohup bash -c 'trap "" HUP; bash "$0/cmd.sh"; rc=$?; echo $rc > "$0/rc"' "$jd" \
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

## BMC specifics

- Stage `bmcjob` in **`/usr/local/bin`** (writable and persistent). Do **not** keep it
  or its job state in `/tmp` — that is tmpfs and gets reaped, losing a running job's
  log even without a reboot.
- Non-interactive SSH has a minimal PATH: call **`/usr/local/bin/bmcjob`** by absolute
  path, not bare `bmcjob` (`command not found`).
- Re-stage anytime from the local copy:
  `cat ~/.pilot/bmcjob | sshpass -e ssh root@<bmc> 'cat > /usr/local/bin/bmcjob && chmod +x $_'`

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
  `bmcjob run … 'bmccli device reset -d <i>'` to recover it. A silent console is
  **data, not an error**, so the exit status stays 0.
- Raise `-t` for slow commands (`ltssm_logger_dump` can exceed the 40 s default).
- Always `ltssm_logger_dump` **before** `ltssm_logger_clear`.

Also: **one client per console.** A second connection attaches and receives nothing,
silently. Stale sessions from a dropped VPN keep holding the port, so a console can
look "dead" when it is merely taken. Check on the BMC with
`netstat -tn | grep ":22[0-9][0-9]" | grep -i estab` and compare the peer IP to your own.
