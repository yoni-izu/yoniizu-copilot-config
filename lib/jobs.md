# Shared job record (`~/.jobs/<id>/`)

One format for every detached job the tools start: `job run` (machine-pilot),
`mctl` remote build/test/run, and the vm-delegate local flash runner. The owner
is [`jobs.py`](jobs.py) (Python) and [`bin/job`](../bin/job) (POSIX shell). Both
read and write the same plain files.

## Location

`$JOB_ROOT` (default `~/.jobs`). Lab homes are NFS-shared, so one root can
hold records from several hosts. Every record carries `host`, and liveness is
judged only on that host. Elsewhere the status is `remote`.

## Id

`<runner>-<host|target>-<YYYYmmddTHHMMSSZ>-<4 hex>`, matching
`[A-Za-z0-9][A-Za-z0-9._-]{0,127}`. The directory name is the id. A caller may
pass its own id (for example, mctl generates it locally, then launches the job
remotely under the same id).

## Files

| File | Written by | Meaning |
|------|------------|---------|
| `runner` | launcher | `pilot`, `mctl` or `flash` |
| `kind` | launcher | `job`, `build`, `test`, `run`, `flash`, `bmc`, ... |
| `host` | launcher | short host name (`hostname -s`, lower case) of the machine running it |
| `target` | launcher | the machine or mctl target the job is *about* (optional) |
| `cwd` | launcher | working directory / workspace on `host` (busy-guard key) |
| `local_cwd` | pointer only | the local workspace a remote job mirrors |
| `user`, `label` | launcher | free text |
| `cmd.sh` | launcher | the script that runs |
| `started` | launcher | ISO UTC `YYYY-mm-ddTHH:MM:SSZ` |
| `pid` | wrapper | session leader (process group id) |
| `rc`, `finished` | wrapper, on exit | authoritative exit code and time |
| `stopped` | stopper, **before** TERM | marks a deliberate stop |
| `log` | wrapper | stdout+stderr |
| `events.jsonl` | job | optional `{"type":"phase","phase":...}` lines |
| `meta.json` | launcher | optional extras (EC2 instance, systemd unit, image, ...) |
| `unstoppable` | launcher | optional space-separated phases during which a stop is refused |

The job's environment carries `JOB_ID=<id>`; with the directory in the
wrapper's argv, that guards against pid reuse. The launcher writes `pid` only
once the wrapper has exec'd and its argv carries the directory. On a host with
`/proc`, a pid missing from `/proc` counts as not running, even if `kill -0`
just succeeded on the reaped zombie.

## Status

`running` (live pid that is still this job), `passed` (`rc`=0), `failed`,
`cancelled` (`stopped` and rc≠0 or none), `died` (no rc, no live pid),
`remote` (another host). `rc` is authoritative; the legacy log sentinels
(`__JOB_DONE__`, `__MCTL_DONE__`, `__BMCJOB_DONE__`) are read only when `rc`
is missing.

## Operations

- `busy(cwd)`: running jobs in a directory; also unfinished `remote` records
  younger than 6 h (mctl refuses a second job in the same workspace).
- `stop(id, refuse_phases)`: writes `stopped`, TERMs the group, KILL is the
  caller's choice. Unstoppable phases (flash `write`/`verify`/`boot`) refuse.
- `clean(days)`: removes finished records older than `days` (default 30): local ones, and copies from other hosts that already hold an `rc`. Running or unresolved records are kept.

## Remote pointer records

A job mirrored from another machine has a local directory with the same id
and `host` set to the remote host. `mctl jobs-sync` copies its `log`, `rc`,
`finished` and `stopped` files back. vd keeps its probe readings in
`delegation.db`, keyed by the same id.
