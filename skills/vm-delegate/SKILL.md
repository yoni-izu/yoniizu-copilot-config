---
name: vm-delegate
description: "Delegate FW workspace work to a mirror workspace on ANY machine (EC2 VM or shared lab desktop) via the mctl script (formerly vmctl). Use when: mirror/align a workspace to a VM or remote machine, remote build, run a full parallel workspace build on a strong machine, pull build results/binaries/logs back, run workspace tests remotely, release/clean up the remote mirror workspace, run any wip_/ws_ git operation remotely, start/stop a VM, manage machine targets, execute a command on a target and pull its output, or delegate long-running work that must survive SSH hangups."
argument-hint: "Optional -t <target>, an operation (mirror/build/build-status/test/test-status/pull-build/pull-logs/release/wip/sh/start/stop/setup), and optionally a workspace path"
---

# mctl — Multi-Machine Mirror-Workspace Work Offloading

Offload heavy FW work to **any reachable machine** — a personal EC2 VM (192
cores) or a shared lab desktop — that hosts a **mirror workspace at the same
path relative to `$HOME`**. All operations go through one script, `~/bin/mctl`
(the old `vmctl` name is now a thin shim → `mctl`), so token usage stays low and
every action can be run by hand with no AI involvement.

> **Targets.** A *target* is a named machine in `~/.mctl/targets`:
> `kind=vm` (personal EC2 — IP auto-discovered from `octl vm list`, **never
> hardcoded**; supports `mctl start`/`mctl stop`) or `kind=remote` (any ssh
> `user@host`, e.g. a lab desktop, assumed always up). Select a target with
> `-t/--target NAME` before the subcommand, or set a default with `mctl use
> NAME` (precedence: `-t` ▸ `$MCTL_TARGET` ▸ `~/.mctl/default` ▸ `vm`). Manage
> targets with `mctl targets`, `mctl target add <name> <user@host>|--vm`,
> `mctl target rm <name>`, and `mctl setup [name]` (deploys the `wip` CLI +
> state dirs). For a `kind=vm` target the private IP is cached in
> `~/.mctl/<name>.ip`, self-validating (reused only while SSH/22 is reachable,
> so a new/torn-down VM auto-invalidates it). Force a refresh with `mctl -t
> <name> ip --refresh`; override with `VM_IP=`.
>
> **Path mapping is home-relative.** The mirror lives at the same path relative
> to `$HOME`. On a VM the home is identical so the absolute path is identical
> (the original behaviour). On a lab machine with a different login/home (e.g.
> `SA-DSK61-CSR` → `/users/home/yoni.izutskiver`), the path is remapped
> home-relative. Many lab boxes share one NFS home, so a single `mctl mirror`
> is visible on all of them — mirror once, build on whichever box is free. The
> remote workspace must already exist at the mapped path.

## Golden rules (why the script exists)

- **Prefer `mctl <sub>` over hand-rolled `ssh`.** Do not reconstruct the SSH /
  `nohup` / `rsync` incantations in the model — call the script. It already
  encodes every gotcha below.
- **Path mapping is home-relative.** The script resolves the LOCAL workspace by
  walking up for `.repo`, then maps it to the target's `$HOME` (identity on the
  VM, remapped on a differing-home lab box).
- **The target pulls from GitHub — it does not rsync source.** Branches must be
  pushed before a remote build; `mctl mirror` does that for you.
- **⛔ ALWAYS TREAT `.repo` — `mirror` DOES NOT SYNC IT.** `mctl mirror` aligns
  *source repos* by branch only. The target's `.repo/manifests` is left at
  whatever (possibly months-old) commit it had. The manifest is what pins every
  **external artifact** (D2D PHY FW, PCIe PHY FW, …), so a stale `.repo`
  silently produces an image built from the right source but the **wrong
  external firmware**. Never trust a delegated build until `.repo` parity is
  proven. See "`.repo` is not mirrored" below.
- **Long jobs survive hangups.** Builds/tests run under `setsid nohup` on the
  target with a PID + log file under `<remote-home>/.mctl/jobs/<tag>/`. No
  persistent SSH session is needed — poll from a fresh connection with
  `*-status`. Local state (IP/home caches, pulled logs) lives in `~/.mctl/`.

## SSH gotchas the script already handles (do not re-derive)

- Always `ssh -A` — the target has **no GitHub key of its own**; agent
  forwarding is what makes `git fetch` work remotely.
- `octl` and the `wip` CLI are **not on the non-login PATH** — every remote
  command runs inside a login shell (`bash -l -s` fed over stdin).
- **The target must have the `wip` CLI.** All remote git ops (`mirror`,
  `release`, `mctl wip …`) call `~/bin/wip` on the target. A fresh mirror may
  not have it yet — run `mctl setup [name]` once (it deploys `wip` + state dirs
  if missing), or deploy by hand:
  ```bash
  ~/bin/wip-deploy "$(mctl -t <name> ip)"     # vm target (by IP)
  ~/bin/wip-deploy user@host                   # remote target
  ```
- `rtk` does **not** exist on remote machines — never prefix remote commands
  with `rtk`.

## Operations

Prefix any command with `-t <target>` to pick a machine (or set a default with
`mctl use <target>`). Examples below omit it (default target).

| You want to… | Command |
|---|---|
| List / manage machine targets | `mctl targets` · `mctl target add <name> <user@host>` · `mctl use <name>` |
| Prepare a target (deploy `wip` + dirs) | `mctl setup [name]` |
| Start / stop a VM target | `mctl -t <vm> start` · `mctl -t <vm> stop` |
| Align the remote mirror to my local branches | `mctl mirror [ws]` |
| Full parallel build (detached) | `mctl build [ws] [extra args]` |
| Check the remote build | `mctl build-status [ws]` |
| Pull binaries **and** logs back (as if built locally) | `mctl pull-build [ws]` |
| Pull just logs for analysis | `mctl pull-logs [ws]` |
| Run workspace tests (detached) | `mctl test '<octl ws test args>' [ws]` |
| Check the remote test run | `mctl test-status [ws]` |
| Every job ever delegated, with its outcome | `mctl jobs` |
| Machine-readable state of every remote job dir | `mctl -t <name> jobs-probe` |
| Mirror every remote job log locally (so it survives a re-run) | `mctl -t <name> jobs-sync` |
| Last N lines of one live remote job log | `mctl -t <name> jobs-tail <tag> [kind] [n]` |
| EC2 power state of every instance | `vd.py vms` (wraps `octl vm list`) |
| Clean up: return remote repos to baseline + sync | `mctl release [ws]` |
| Any `wip` git op remotely | `mctl wip <action> [ws]` or `... --wip` |
| Run an arbitrary command in the remote ws root | `mctl sh '<cmd>' [ws]` |
| Print a VM target's private IP (auto-discovered) | `mctl -t <vm> ip` (add `--refresh`) |

`[ws]` is optional — omit it to use the workspace containing the current
directory. Run from inside the FW workspace (e.g. `~/projects/iridium/one/fw`)
and the path is inferred.

## Artifact Deployment to NFS

For deploying built firmware images into a user's shared NFS home, use
[fw-deploy](../fw-deploy/SKILL.md). This is separate from source mirroring and
`pull-build`: the deployment workflow discovers the shared destination, copies
only the requested artifacts without NFS ownership changes, and verifies their
SHA-256 checksums from the relevant machines. It does not flash hardware.

`mctl sh` is text-only because it captures output using shell command
substitution. Do not stream raw image/tar bytes through it; use the binary-safe
transfer procedure in `fw-deploy`, or `pull-build` for the full artifact tree.

## Standard delegated-build flow

```bash
cd ~/projects/iridium/one/fw
mctl sh 'octl ws sync' # 0. MANDATORY: refresh .repo/manifests (mirror skips it)
mctl mirror            # push local dev branches + align the mirror
mctl build             # detached parallel `octl ws build --parallel`
mctl build-status      # poll (repeat from a fresh shell; no session held)
# ...when STATE=DONE...
mctl pull-build        # rsync binaries + logs back to the local path
```

Step 0 is not optional. Skipping it builds correct source against stale
external firmware — see "`.repo` is not mirrored" below.

When reporting to the user, poll with `build-status` between turns (never
`sleep`-loop). `STATE=RUNNING`/`DONE`, an `ERRORS=` count, and a log tail are
printed each poll. For the full deduplicated error list after completion:

```bash
mctl sh 'grep -E "error:" ~/.mctl/jobs/*/build.log | sed "s#.*/fw/##" | sort -u | head'
```

## What `mirror` does (and the force-push caveat)

It is a thin, token-cheap wrapper around the `wip` CLI — a fresh local scan is
snapshotted into a `wip save` file, shipped to the target, and replayed there.
The `.wip` save file **is** the transferred branch scheme (its `# WIP_INFO`
section maps `ws:repo=path:branch` for every repo, using **workspace-relative**
paths so it ports cleanly across machines with different homes):

1. Local: `wip scan` (fresh scan of the workspace) → `wip ws push --yes`
   (force-push every non-baseline branch to origin) → `wip save <tag>` (save the
   branch map to `~/.wip/saved/<tag>.wip`).
2. `scp` that one saved map to the target's `~/.wip/saved/`.
3. Target: `wip load <tag>` (consume the branch map) → `wip ws checkout` (check
   out every recorded branch) → `wip ws hard-reset --yes` (fetch + `reset --hard
   origin/<branch>` so a rebased/force-pushed branch is reproduced *exactly* —
   this is mode-independent, unlike `align`, so the mirror is an exact copy of
   the pushed state regardless of the target's `wip` mode).

Because step 1 **force-pushes** your dev branches, only run `mirror` when you
intend to publish the current local state. After a local rebase, re-run
`mctl mirror` to bring the target back in sync. On shared-NFS lab boxes, one
`mctl mirror` is visible on every box sharing that home.

## `.repo` is not mirrored — sync it FIRST, every time

`mirror` replays a branch map with `wip`; it touches **only the source repos**.
`.repo/manifests` is never transferred. A mirror can therefore be perfectly
branch-accurate and still build the wrong product.

**Why it bites:** the manifest pins external artifacts. A target whose
`.repo/manifests` predates your release revision may not even contain that
revision's manifest dir, so `octl` falls back to an older one and downloads
stale PHY/firmware blobs. The build log looks clean — zero errors — and the
resulting flash image is quietly wrong.

**Order matters.** `octl ws sync` resets repos to their *manifest baseline*,
which destroys a `wip` alignment. So always sync **before** mirroring:

```bash
mctl sh 'octl ws sync'   # 1. refresh .repo/manifests + repos to baseline
mctl mirror              # 2. re-apply your dev branches on top
# 3. only now build
```

**Prove parity before trusting any build** (cheap, do it every time):

```bash
# manifest commit must match local
diff <(git -C .repo/manifests rev-parse HEAD) \
     <(mctl sh 'git -C .repo/manifests rev-parse HEAD')

# and the external pins the build will actually consume
REV=iridium_release_0.5.11      # your manifest revision
mctl sh "grep -oE 'fw_external/[A-Za-z0-9/_.-]*\.tar\.gz' \
         .repo/manifests/projects/iridium/$REV/psoc-fw-ci.xml"
```

After the build, confirm what was *actually* pulled — the on-disk artifact is
the only real proof:

```bash
mctl sh 'ls build_artifacts/opt/atl/fw_external/d2d/phy/ \
            build_artifacts/opt/atl/fw_external/pcie/phy/'
```

## Testing on a target

`mctl test '<args>'` runs `octl ws test <args>` detached. Pass whatever
`octl ws test` accepts, e.g.:

```bash
mctl test 'ip/pcie/ep --group fast --show-summary'
mctl test-status          # STATE + tail
mctl pull-logs            # bring test logs local for analysis
```

## Delegation history (`mctl jobs` + the portal's VM tab)

The target keeps exactly **one** `build.log`/`test.log` per workspace tag — the
next run of the same kind overwrites it. So the remote holds *current* state
and no history at all.

Every dispatch is therefore also appended locally to **`~/.mctl/joblog.jsonl`**
(target, kind, workspace, tag, command, pid, jobdir, start time). That file is
the only record that survives the next run.

```bash
mctl jobs           # every job ever delegated, from the local log
mctl jobs-probe     # one ssh round trip: TSV state of EVERY remote job dir
mctl jobs-sync      # rsync every remote job log into ~/.mctl/jobs/<target>/
mctl jobs-tail <tag> [kind] [n]   # last n lines of ONE live remote log
```

`jobs-probe` emits `tag kind STATE rc pid log_mtime lines errors cmd last_line`,
which is what the resolver consumes:

```bash
python3 ~/.copilot/skills/vm-delegate/vd.py list       # history + resolved status
python3 ~/.copilot/skills/vm-delegate/vd.py targets    # per-target freshness + last error
python3 ~/.copilot/skills/vm-delegate/vd.py refresh [target]  # probe + mirror logs
python3 ~/.copilot/skills/vm-delegate/vd.py sync    [target]  # mirror job logs only
python3 ~/.copilot/skills/vm-delegate/vd.py log  <target> <tag> [kind]  # stored log
python3 ~/.copilot/skills/vm-delegate/vd.py tail <target> <tag> [kind]  # live log
python3 ~/.copilot/skills/vm-delegate/vd.py payload    # JSON for the portal
python3 ~/.copilot/skills/vm-delegate/vd.py vms        # EC2 power state
python3 ~/.copilot/skills/vm-delegate/vd.py start|stop <instance-id>
```

### Refresh one machine, not the fleet

`refresh` and `sync` both take an optional target name, and that is not a
convenience: **an unreachable target costs a 90 s ssh timeout**, so with two
dead lab desktops registered a fleet refresh runs for minutes while
`vd.py refresh vm` returns in about four seconds. Ask for the machine you care
about.

Scoping is honest about what it did *not* check. Each target carries its own
`checked` timestamp in `jobstate.json`, surfaced per target in the payload's
`target_state`, so refreshing one machine never makes the rest look freshly
read; and per-target errors are merged rather than replaced, so a VM-only
refresh does not erase the knowledge that `dsk61` was unreachable.

```
$ vd.py targets
vm         running    jobs=16  running=0   checked        11s ago  ok
dsk61      -          jobs=0   running=0   checked     3m 10s ago  ⚠ jobs-probe failed (rc=255)
```

Scoped to a target with no EC2 instance behind it, the `octl vm list` call is
skipped too — it revalidates SSO and tells you nothing about a bare-metal lab
desktop.

The target name is validated against the registered target list before it
reaches a shell, so an unknown or crafted name is refused outright rather than
escaped.

### Log retention — the remote clobbers, so mirror first

`refresh()` calls `sync_logs()` for every target it reached, which is
`mctl jobs-sync`: an rsync of `~/.mctl/jobs/*/` on the target into
`~/.mctl/jobs/<target>/`, keeping the previous copy as `<name>.log~` when a tag
is re-run. **Without this a log is gone the moment its slot is reused**, which
is why only a mirrored log can be reopened later. `has_log` / `log_bytes` on
each payload row say whether a local copy is held; the portal's **Log** column
offers *open* only when one is.

`start_job` also drops the dispatched command in `<jobdir>/<kind>.cmd` on the
target, so the command survives even for a job adopted from another machine.
Jobs that predate this show **not recorded** in the portal rather than a command
that was never captured.

`vd.py` writes the resolved status to `~/.mctl/jobstate.json` and binds each
probe row to the **newest** dispatch for that `(target, tag, kind)` — older
entries settle as `superseded`, because their log is gone. Jobs found on a
target that were never dispatched from here are adopted once (`found on target`).

**`refresh()` probes EVERY registered target, always.** It used to ask only the
targets that still had an unresolved job, which was self-defeating: once the
last job settled the target was never asked again, so a job started *afterwards*
— the one you actually want to watch — could never be discovered. A re-run of a
tag that already has a settled record is adopted as a new dispatch when the
remote log's mtime has moved past it; without that second rule, rebuilding a
workspace you have built before stays invisible forever.

A target that fails to answer (`jobs-probe failed (rc=255)` = ssh) is recorded
in `state["errors"]` and surfaced verbatim in the payload's `error` string. It
does **not** silently drop the target, and the probe is retried next refresh.

`vd.py vms` parses `octl vm list --output json` (instance id, state, type,
private IP, lease expiry) and matches each instance to an mctl target through
the cached `~/.mctl/<target>.ip`, caching the result in `~/.mctl/vmstate.json`.
`vd.py start|stop <id>` shells out to `octl vm start|stop`; a start also clears
the cached IPs, because a resumed VM usually comes back on a different one.

`octl` brackets its JSON with noise — `INF ... Validating SSO credentials` lines
before it and, when a newer octl exists, an "A new release is available" banner
after it. Parse with `json.JSONDecoder().raw_decode()` from the first `{`, never
`json.loads(raw[start:])`: the trailing banner makes the latter raise.

The Team Portal renders all of this in its **VM jobs** tab: a power strip with a
state dot, lease countdown and a Start/Stop button per instance, the currently
running job highlighted below it, then the full history.

| Control | Endpoint | What it costs |
| --- | --- | --- |
| **Refresh status** | `POST /api/vm-refresh` → background job, polled via `/api/align/<id>` | one ssh probe + one log rsync per target, plus `octl vm list` |
| **↻ on a machine card / target chip** | same endpoint with `{"target": "<name>"}` | one ssh probe + one rsync, that target only |
| **Check VM status** | `POST /api/vm-machines` → `vd.portal_machines()` | `octl vm list` only — no ssh |
| **Sync logs** | `POST /api/vm-sync` → `vd.portal_sync()`, scoped by the target filter | one rsync per target — no probe, no octl |
| **Log ▸ open** | `POST /api/vm-log` → `vd.portal_log()` | reads the local mirror (falls back to the remote) |
| **Log ▸ tail** | `POST /api/vm-tail` → `vd.portal_tail()`, re-polled every 5 s | one `tail -n` ssh per poll |

Neither costs a model token. Three design points worth keeping:

- **Refresh is scoped by default where it matters.** Every EC2 card and every
  bare-metal target chip carries its own `↻`, and the toolbar button follows the
  target filter (it relabels to *Refresh vm*). Each chip also shows its own
  age — refreshing one machine must not make the others look checked.
- **Refresh is a background job, not an inline request.** One unreachable target
  costs a 90 s ssh timeout and `octl vm list` revalidates SSO, so the inline
  version regularly outlived the browser's patience: the fetch gave up, the
  section never re-rendered, and the tab looked dead while the probe was in fact
  succeeding server-side. It now streams per-target rows into the align banner.
- **The tail is a poll, not a socket.** Each poll is the same `tail -n` ssh the
  probe already makes, so the live view needs no websocket and nothing to tear
  down but a `setInterval`. `/api/vm-log` and `/api/vm-tail` deliberately do
  **not** take `VM_REFRESH_LOCK` — reading a log must never be blocked by an
  in-flight probe.

The payload carries `machines_age_h` / `refresh_age_h` and the tab renders them
next to each reading. A day-old `stopped` that looks live is indistinguishable
from a fresh one otherwise, and that is exactly how the tab came to show a
stopped VM that ssh could reach perfectly well.

## Releasing the mirror

`mctl release` returns every repo in the remote workspace to its manifest
baseline (`main` / the project fb tag) via `wip ws return` and runs `octl ws
sync`. Use it when you're done with a delegated task so the mirror is clean for
the next one.

## wip/ws operations on a target

Any action supported by the `wip` CLI (`checkout`, `push`, `pull`, `align`,
`set-upstream`, `stash`, `pop`, `diff`, `return`, `hard-reset`) runs remotely.
Legacy underscore names (`hard_reset`, `set_upstream`) are still accepted:

```bash
mctl wip align            # ws-scope: rebase this workspace's branches remotely
mctl wip diff             # generate diff patches remotely
mctl wip return --wip     # wip-scope: return ALL remote workspaces to baseline
```

Default scope is the single workspace (`ws_*`); pass `--wip` for all-workspaces
(`wip_*`). See the `wip-workspace` skill for the semantics of each action.

## Failure-triage reminders (shared with `build-fw`)

- `octl ws build` (even `--parallel`) **aborts at the first failing project**.
  If an unrelated early project is broken, build the rest individually with
  `mctl sh 'octl ws build <project>'` to still get coverage.
- **Never call `main` broken from a single-project build.** A valid baseline is
  a **clean full `octl ws build`** on an all-`main` workspace. See the
  `build-fw` skill's "Prove a failure is pre-existing" and "Known false
  failures" sections before blaming a change.
- Deduplicate errors — the same `error:` repeats per translation unit.

## Script reference

`~/bin/mctl` (on `PATH`; `~/bin/vmctl` is a backward-compat shim). Run `mctl
help` for the built-in usage summary. The script is intentionally
self-contained: local state lives in `~/.mctl/` (per-target IP/home caches +
per-target job dirs + `joblog.jsonl`/`jobstate.json`) so builds/tests keep
running and remain pollable even across disconnects and token-less sessions.

`~/.copilot/skills/vm-delegate/vd.py` is the companion resolver: it merges the
append-only dispatch log with a `jobs-probe` of each live target, mirrors the
remote logs locally, and exposes `list` / `refresh` / `sync` / `log` / `tail` /
`payload` / `vms` / `start` / `stop` (plus `payload()`, `portal_refresh()`,
`portal_machines()`, `portal_power()`, `portal_sync()`, `portal_log()` and
`portal_tail()` as a module, which is how the Team Portal's VM tab is fed).
