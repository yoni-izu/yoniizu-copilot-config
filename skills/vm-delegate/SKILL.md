---
name: vm-delegate
description: "Delegate FW workspace work to a mirror workspace on ANY machine (EC2 VM or shared lab desktop) via the mctl script (formerly vmctl). Use when: mirror/align a workspace to a VM or remote machine, remote build, run a full parallel workspace build on a strong machine, pull build results/binaries/logs back, run workspace tests remotely, release/clean up the remote mirror workspace, run any wip_/ws_ git operation remotely, start/stop/wake a VM, VM lease and auto-start, provision a new VM or lab machine with prerequisites, manage machine targets, execute a command on a target and pull its output, or delegate long-running work that must survive SSH hangups."
argument-hint: "Optional -t <target>, an operation (mirror/workspace/build/build-status/test/test-status/pull-build/pull-logs/release/wip/sh/start/stop/wake/lease/provision), and optionally a workspace path"
---

> **Portal handoff:** For Team Portal/dashboard questions, feature requests,
> UI/API changes, bugs or operations, load
> [portal-maintainer](../portal-maintainer/SKILL.md). Domain records and
> workflows remain owned by this skill; coordinate mixed changes with the maintainer.

# mctl — Multi-Machine Mirror-Workspace Work Offloading

**RTL is not a mirror-workspace task.** Follow
[rtl-vm-policy](../rtl-vm-policy/SKILL.md): every RTL question goes to
Neo via `~/bin/neo-ask` (neo-brain on target `vm`, using its own mirrors).
Never use `mctl sh`, `wip`, `pull-logs`, `jobs-sync`, or another transfer to
send RTL source, patches, diff hunks, or source-bearing logs to WSL. If Neo is
unavailable, report the prerequisite and stop. These
restrictions do not change normal FW or software CSR workflows.

**Mixed/unknown job targets:** `~/bin/mctl jobs-probe` returns the last line of
every job log, and `jobs-sync` copies every remote job log. `vd.py refresh` and
the portal's refresh invoke these operations. Do not run them for a target that
may contain RTL or unclassified jobs; neither helper enforces this policy.
For a verified software-only job, the per-job status, tail and pull workflows
remain available only when their output is known not to contain RTL.

Offload heavy FW work to **any reachable machine** — a personal EC2 VM (192
cores) or a shared lab desktop — that hosts a **mirror workspace at the same
path relative to `$HOME`**. All operations go through one script, `~/bin/mctl`
(the old `vmctl` name is now a thin shim → `mctl`), so token usage stays low and
every action can be run by hand with no AI involvement.

> **Targets.** A *target* is a named machine in `~/.mctl/targets`:
> `kind=vm` (personal EC2 — **bound to an instance id** in the registry's host
> column; IP auto-discovered from `octl vm list` for that id, **never
> hardcoded**; supports `mctl start`/`mctl stop` of that id) or `kind=remote` (any ssh
> `user@host`, e.g. a lab desktop, assumed always up). Select a target with
> `-t/--target NAME` before the subcommand, or set a default with `mctl use
> NAME` (precedence: `-t` ▸ `$MCTL_TARGET` ▸ `~/.mctl/default` ▸ `vm`). Manage
> targets with `mctl targets`, `mctl target add <name> <user@host> [--bmc [user@]host]|--vm [user] [instance-id]`,
> `mctl target bmc <name> <[user@]host|->` (set/clear a lab machine's BMC; optional 5th
> registry column), `mctl target set <name> [--user U|-] [--host H|-] [--bmc B|-]` (edit a row in
> place; name/kind fixed; a login/host change drops the cached `$HOME`/IP), `mctl target rm <name>`, `mctl -t <name> bmc` (interactive SSH to the
> BMC, default user `root`, password via `sshpass -e` from `~/.pilot/bmc.pw` if present), and `mctl provision [name]` (installs the target's
> prerequisites from recipe lists — see **Provisioning a machine**). For a `kind=vm` target the private IP is cached in
> `~/.mctl/<name>.ip`, self-validating (reused only while SSH/22 is reachable,
> so a new/torn-down VM auto-invalidates it). Force a refresh with `mctl -t
> <name> ip --refresh`; override with `VM_IP=`.
>
> **Path mapping is home-relative.** The mirror lives at the same path relative
> to `$HOME`. On a VM the home is identical so the absolute path is identical
> (the original behaviour). On a remote machine with a different login/home,
> the path is remapped home-relative. Multiple lab boxes may share one NFS home,
> so a single `mctl mirror`
> is visible on all of them — mirror once, build on whichever box is free. The
> remote workspace must already exist at the mapped path.

## Golden rules (why the script exists)

- **Honor dedicated workspace purpose.** The workspace owner
  ([policy](../workspace-manager/SKILL.md#dedicated-workspaces)) checks supported
  `mctl` dispatches. Review/Development slots can be sources; build/test execution
  destinations must be General, Build root or Mirror. Mirror destinations obey
  the same rule. Generic `run`, `sh` and `wip` require General destinations;
  whole-root `wip` also checks other tracked destinations under that root.
  Guards resolve physical paths, including home-relative paths and symlinks.
  If a tracked path is an alias, refresh its workspace status to reconcile the
  canonical identity before dispatch; do not register a duplicate to bypass it.
  Use the structured `workspace sync`/`free` APIs for dedicated-target maintenance,
  not `sh` to bypass policy. Dispatch holds the workspace policy lock; running
  or unverified delegated jobs block repurposing after dispatch. Status, logs,
  artifact retrieval and safe release remain available. No force flag bypasses
  dedication. This is managed-tool policy, not an OS sandbox.
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
  not have it yet — run `mctl provision [name]` once (its `wip` recipe deploys
  it, and redeploys whenever the local copy changes). Remote workspace-manager checks also require WIP to advertise
  `forall-argv-v1` via `wip capabilities`; update an older target WIP before
  those checks. To deploy by hand:
  ```bash
  ~/bin/wip-deploy "$(mctl -t <name> dest)"   # vm target ([user@]ip)
  ~/bin/wip-deploy user@host                   # remote target
  ```
- `rtk` does **not** exist on remote machines — never prefix remote commands
  with `rtk`.

## Operations

Prefix any command with `-t <target>` to pick a machine (or set a default with
`mctl use <target>`). Examples below omit it (default target).

| You want to… | Command |
|---|---|
| List / manage machine targets | `mctl targets` · `mctl target add <name> <user@host> [--bmc host]` · `mctl target bmc <name> <host\|->` · `mctl target set <name> [--user U] [--host H] [--bmc B\|-]` · `mctl use <name>` |
| Open a shell on a lab machine's BMC | `mctl -t <name> bmc` |
| Install a target's prerequisites (wip, creds, git, copilot, neo, RTL mirrors…) | `mctl provision [name]` · `--dry-run` · `--status` |
| Start / stop a VM target | `mctl -t <vm> start` · `mctl -t <vm> stop` |
| Make sure a VM is up (auto-starts it) | `mctl -t <vm> wake` |
| Last start, predicted stop, lease policy · renew | `mctl -t <vm> lease` · `mctl -t <vm> lease renew` |
| Align the remote mirror to my local branches | `mctl mirror [ws]` |
| Full parallel build (detached) | `mctl build [ws] [extra args]` |
| Check the remote build | `mctl build-status [ws]` |
| Pull binaries **and** logs back (as if built locally) | `mctl pull-build [ws]` |
| Pull just logs for analysis | `mctl pull-logs [ws]` |
| Run workspace tests (detached) | `mctl test '<octl ws test args>' [ws]` |
| Check the remote test run | `mctl test-status [ws]` |
| Every job ever delegated, with its outcome | `mctl jobs` |
| State of every job dir, only on a verified source-free target | `mctl -t <name> jobs-probe` |
| Mirror all job logs, only on a verified source-free target | `mctl -t <name> jobs-sync` |
| Last N lines of one verified source-free software job | `mctl -t <name> jobs-tail <tag> [kind] [n]` |
| EC2 power state of every instance | `vd.py vms` (wraps `octl vm list`) |
| Clean up: return remote repos to baseline + sync | `mctl release [ws]` |
| Guarded independent remote slot operations | `mctl -t NAME workspace status\|sync\|free\|mirror\|align /absolute/remote/path WS-R-uuid BASELINE [options-json]` |
| Any `wip` git op remotely | `mctl wip <action> [ws]` or `... --wip` |
| Run an arbitrary command in the remote ws root | `mctl sh '<cmd>' [ws]` |
| Print a VM target's private IP (auto-discovered) | `mctl -t <vm> ip` (add `--refresh`) |
| Print a target's ssh destination (`[user@]host`) | `mctl -t <name> dest` |
| List your EC2 instances and their targets | `mctl vm list` |
| Show / switch the Main VM (target `vm`) | `mctl vm main` · `mctl vm main <instance-id> [--user LOGIN]` |

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

## Switching the Main VM (new or replacement instance)

Target `vm` is the **Main VM**: `neo-ask`, the default `mctl` target and the
portal's terminal/live link (`terminal_targets`/`live_targets` = `vm`) all
follow it by name. Nothing else stores an instance id or IP, so switching is a
single registry change:

```bash
octl vm create --flavor m7i.48xlarge -d main-dev-vm   # if a new one is needed
mctl vm list                                         # instance ids <-> targets
mctl vm main i-<new-id> --user <login>               # rebind target vm
mctl provision vm                                    # everything in lists/vm.list
```

- The previous Main is kept as target `vm-<first 6 hex of its id>` (same
  login), so it can still be started, probed and released. Switching back
  (`mctl vm main <old-id>`) reuses that target's login and drops the alias.
  The portal's **★ Set as main** on a VM card does the same.
- `--user` defaults to a login already recorded for that instance, else the
  instance's `owner` tag. VMs created through `octl vm create` accept the SSO
  identity (e.g. `yoni.izutskiver`), not the local WSL user; mctl remaps paths
  home-relative, so `/home/<local>/projects/...` → `/home/<sso>/projects/...`.
- **Use `mctl -t <vm> dest`, never a bare IP, for hand-rolled ssh** — the login
  is per-VM.
- An unbound `kind=vm` target (host `-`) is legacy: it resolves to the only
  running VM and refuses when several run; `start`/`stop` refuse outright. Bind
  it with `mctl vm main <id>`.
- What does **not** move: workspaces mirrored on the old VM (recreate/clone
  them, then `mctl mirror`; old remote workspace records under `vm` show as
  missing). neo-brain + its RTL mirrors for `neo-ask` are rebuilt on the new
  VM by `mctl provision vm` (recipes `neo`, `rtl`; RTL is cloned on the VM only).
  The one manual step left is `ssh -t "$(mctl dest)" '~/.local/bin/copilot login'`. Dispatches record the bound
  `instance` in `joblog.jsonl`, so history shows which machine ran a job.
- Leases are handled for you — see **VM power: auto-start and lease**.

## VM power: auto-start and lease

mctl starts VMs on a short lease (`MCTL_VM_LEASE`, default **30m**) and
remembers every start/renew/stop in `~/.mctl/power/<instance-id>`
(started, expires, by) plus a JSONL event log `~/.mctl/power.log`.

- **Prediction.** `mctl lease` prints when the VM was started and when it is
  predicted to stop (lease expiry). While the lease is predicted live and ssh/22
  answers, no `octl` call is made.
- **Auto-start, on demand.** *Active* commands (`sh`, `mirror`, `build`,
  `test`, `run`, `pull-*`, `release`, `wip`, `term`, `provision`/`setup`,
  `workspace` other than `status`, `wake`) start a stopped or lease-expired VM,
  wait for ssh (`MCTL_WAKE_TIMEOUT`, 600 s) and continue. *Passive* commands
  (`jobs` — local log only — and `jobs-probe/watch/sync/tail`, `ip`, `dest`,
  `*-status`, `cancel`, `workspace status`) never wake a VM: the portal polls
  them every few seconds, so waking there would keep an idle VM up (and billed)
  around the clock, against the on-demand policy; a stopped VM has no running
  jobs to report anyway. `neo-ask` calls
  `mctl wake` first. Disable with `MCTL_AUTOSTART=0`.
- **Renewal.** `build`/`test`/`run`/`wake` renew when less than
  `MCTL_RENEW_BELOW` (30m) is left. While a delegated job is confirmed running,
  the portal watchdog (every 60 s) calls `mctl lease renew --if-below 10m`
  (`MCTL_KEEPALIVE_BELOW`) so a long build is never reaped; a long `provision`
  keeps its own lease alive. An idle VM simply stops at lease end — no cost —
  and comes back on the next active command.
- The portal VM card shows *started … ago (auto|manual)*, the *predicted stop*,
  and flags a cached "running" whose lease has passed as probably stopped.

## Provisioning a machine

`mctl provision [name]` installs prerequisites from **recipe lists** under
[provision/](provision/) (override the root with `MCTL_PROVISION_DIR`):

| List | Default for | Recipes |
|---|---|---|
| `lists/machine.list` | `kind=remote` (lab desktops/servers) | `creds git wip copilot btop` (`dirs` is optional: `--only dirs`) |
| `lists/vm.list` | `kind=vm` | `include machine.list` + `octl_update neo rtl` |
| `lists/neo.list` | — | `neo rtl` |

```bash
mctl provision vm --dry-run          # what is missing (nothing changes)
mctl provision vm                    # install the missing ones (in place = skipped)
mctl provision dsk70 --only creds,wip
mctl provision vm --list neo         # or --list /path/to/my.list
mctl provision vm --skip rtl --force # reinstall all but rtl
mctl provision vm --status           # last result per recipe
```

- **Recipe** = `provision/recipes/<name>.sh` defining `check()` (exit 0 when
  already satisfied), `install()`, and optionally `todo()` (echoes a manual
  step, e.g. *copilot login*). Headers: `# desc:`, `# where: remote|local`,
  `# needs: <recipes>` (blocked if one failed), `# timeout: <secs>`.
  `remote` recipes run on the target in a login shell with agent forwarding;
  `local` ones run here with `$MCTL_DEST`, `$MCTL_HOST`, `$MCTL_LOGIN`,
  `$MCTL_REMOTE_HOME` and `r <cmd>` (ssh to the target). Both get
  `$MCTL_TARGET`, `$MCTL_KIND`, `$GIT_NAME`, `$GIT_EMAIL`.
- **List** = one recipe per line, `#` comments, `include <list>`; duplicates run
  once. A list given by path may keep its own recipes next to it
  (`../recipes/` or the same dir) before falling back to the defaults.
- Results: `~/.mctl/provision/<target>.state` (TSV, latest per recipe — shown
  on the portal card) and a full log per run in `~/.mctl/provision/logs/`.
  The portal's **⚙ Provision** button runs it live in a terminal window.
- `mctl setup` is an alias of `provision`; `mctl setup-machine` keeps the old
  credentials-only copy (`setup_machine.sh`).
- To add a prerequisite once and for all: drop a recipe in `recipes/`, add its
  name to the right list, and `mctl provision <target>`.

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
printed each poll. Only poll a verified software-only job whose output is known
to be source-free. For errors, inspect that job's log, never a glob over all
jobs:

```bash
mctl jobs-tail <verified-software-job-tag> build
```

## What `mirror` does (and the force-push caveat)

It is a thin, token-cheap wrapper around the `wip` CLI — a fresh local scan is
snapshotted into a `wip save` file, shipped to the target, and replayed there.
The `.wip` save file **is** the transferred branch scheme (its `# WIP_INFO`
section maps `ws:repo=path:branch` for every repo, using **workspace-relative**
paths so it ports cleanly across machines with different homes):

1. Resolve/register the local source's stable `WS-L-uuid` and the remote slot's
   `WS-R-uuid` using `wm.py identity`. A source under a tracked `slot/fw` maps
   to its tracked ancestor slot; the **slot**, not just `fw`, owns the mirror
   marker. Check the actual remote marker every time: cached portal relations
   are not authoritative after a portal Free. Adopt an existing remote marker
   ID through workspace-manager's guarded status/identity reconciliation;
   never replace an unknown identity.
   Resolve the mapped slot's real path on the target **before** roster lookup
   or registration. Repeated mirrors through a symlink alias reuse the canonical
   slot's existing ID, revision and reservation; they must not create another
   record or displace its holder. Unresolvable paths and conflicting registered
   machine identities fail closed. Workspace action-history lookup uses the same
   canonicalization, so build/test/sh logging cannot recreate alias records.
   First-time remote registration inherits the source roster's manifest
   revision (for example `a1_power_on`), not the remote identity default
   `main`. Existing remote registrations retain their configured revision.
2. Refuse occupied remote reservations, dirty/local-only commits, another
   source's mirror, and nonbaseline independent workspaces. Preserve the
   manifest/pin drift gate. Scan/save the local branch map, then publish the
   allowed development branches (subject to the script's push safety policy).
3. With `--sync`, use the guarded whole-slot runtime sync before replay. Mark
   the remote marker and local registry **syncing before alignment**, then
   `scp` the saved map to the target's `~/.wip/saved/`.
4. Target: `wip load <tag>` (consume the branch map) → `wip ws checkout` (check
   out every recorded branch) → `wip ws hard-reset --yes` (fetch + `reset --hard
   origin/<branch>` so a rebased/force-pushed branch is reproduced *exactly* —
   this is mode-independent, unlike `align`, so the mirror is an exact copy of
   the pushed state regardless of the target's `wip` mode).
5. Verify branch/SHA parity, then mark both records **active**. Alignment,
   transfer, or verification failure marks the relation **failed**, not active.
   Runtime and alignment calls use ordinary `rlogin`: credential provisioning
   must not automatically replay a partially completed mutation.

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
mctl jobs-probe     # EVERY job dir, including last lines: source-free targets only
mctl jobs-sync      # EVERY job log: source-free targets only
mctl jobs-tail <tag> [kind] [n]   # ONE verified source-free software log
mctl cancel <tag> <kind> <pid>    # stop a running job (TERM, then KILL its process group)
```

`mctl cancel` refuses (`MISMATCH`/`NOLOG`, rc 2) unless the slot's live pid is
the one given, so it never kills a newer run. A stopped job's log gains
`__MCTL_DONE__ rc=130` plus `__MCTL_CANCELLED__`, and probes report it as
`CANCELLED` (resolved status `cancelled`). Docker containers it started may
survive; check them before re-dispatching.

`jobs-probe` emits `tag kind STATE rc pid log_mtime lines errors cmd last_line`,
which is what the resolver consumes:

```bash
python3 ~/.copilot/skills/vm-delegate/vd.py list       # history + resolved status
python3 ~/.copilot/skills/vm-delegate/vd.py targets    # per-target freshness + last error
python3 ~/.copilot/skills/vm-delegate/vd.py refresh [target]  # probe + mirror logs
python3 ~/.copilot/skills/vm-delegate/vd.py sync    [target]  # mirror job logs only
python3 ~/.copilot/skills/vm-delegate/vd.py cancel <job-id>   # mctl cancel + re-probe
python3 ~/.copilot/skills/vm-delegate/vd.py log  <target> <tag> [kind]  # stored log
python3 ~/.copilot/skills/vm-delegate/vd.py tail <target> <tag> [kind]  # live log
python3 ~/.copilot/skills/vm-delegate/vd.py payload    # JSON for the portal
python3 ~/.copilot/skills/vm-delegate/vd.py vms        # EC2 power state
python3 ~/.copilot/skills/vm-delegate/vd.py start|stop <instance-id>
```

### Local-runner jobs (kind `flash`)

Some jobs run on THIS machine against a target (today: the portal's Flash FW).
`vd.start_local_job(target, "flash", argv, label=, meta=)` runs argv in a
`systemd-run --user` transient unit `mctl-<id>` (so a portal restart does not
kill it; `journalctl --user -u mctl-<id>`), with its log, pid and events under
`~/.mctl/local/<id>/`, and records it in the delegation history with
`runner:"local"`. `jobs()`, `read_log`, `tail`, `log_view` (mode `local`) and
`cancel` all handle it; `refresh()` never probes it. Status comes from the
wrapper's `__MCTL_DONE__ rc=N` sentinel (0 passed, 130 cancelled, else failed);
cancel is refused during the write/verify/boot phases. One running flash job per
target. Tests: `test_local_jobs.py`.

### Refresh one machine, not the fleet

Only use `refresh` or `sync` when all logs on the target are known source-free;
scoping to a target does not classify the jobs on it. Both take an optional
target name, and that is not a
convenience: **an unreachable target costs a 90 s ssh timeout**, so with two
dead lab desktops registered a fleet refresh runs for minutes while
`vd.py refresh vm` returns in about four seconds. Ask for the machine you care
about.

Scoping is honest about what it did *not* check. Each target carries its own
`checked` timestamp in `~/tasks/delegation.db`, surfaced per target in the payload's
`target_state`, so refreshing one machine never makes the rest look freshly
read; and per-target errors are merged rather than replaced, so a refresh of
one target does not erase another target's recorded error.

```
$ vd.py targets
<target>   running    jobs=<n> running=<n> checked <age> ago       ok
<target>   -          jobs=0   running=0   checked <age> ago        ⚠ jobs-probe failed (rc=<code>)
```

Scoped to a target with no EC2 instance behind it, the `octl vm list` call is
skipped too — it revalidates SSO and tells you nothing about a bare-metal lab
desktop.

The target name is validated against the registered target list before it
reaches a shell, so an unknown or crafted name is refused outright rather than
escaped.

### Log retention — source-free jobs only

Subject to the mixed/unknown-target restriction above, `refresh()` calls
`sync_logs()` for every target it reached, which is
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

`vd.py` writes resolved observations to `~/tasks/delegation.db` and binds each
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
private IP, lease expiry) and matches each instance to an mctl target by the
**instance id bound in `~/.mctl/targets`** (every personal VM shares the same
EC2 Name, so neither name nor IP can tell them apart). Only an unbound legacy
`kind=vm` target falls back to its cached `~/.mctl/<target>.ip`. The target and
`main` flag are attached when the payload is read, so a Main switch shows
without re-querying octl. `vd.py start|stop <id>` shells out to
`octl vm start|stop`; a start clears the IP caches of the targets bound to that
instance (and of unbound VM targets) — a resumed VM usually has a new IP.
`vd.py main [<id> [login]]` prints or switches the Main VM via `mctl vm main`.

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
| **★ Set as main** on a VM card | `POST /api/vm-main {"id": "i-…"}` → `vd.portal_set_main()` → `mctl vm main` | registry rewrite only — no ssh, no octl power action |

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

`mctl release` resolves the registered **whole remote slot**, checks its actual
marker/source identity, then calls guarded runtime `free`. This refuses dirty
working trees and local-only commits, returns repositories to their configured
manifest baseline and syncs them. Only successful free clears the remote
mirror marker; only then is `wm.py mirror-state ... --state released` recorded.
A failed release retains the relation (possibly marked failed) and never prints
success. Do not replace this with unchecked `wip return && octl ws sync`.

### Independent remote workspace transport

```bash
mctl -t NAME workspace status /independent/remote/slot WS-R-uuid main
mctl -t NAME workspace sync /independent/remote/slot WS-R-uuid main
mctl -t NAME workspace free /independent/remote/slot WS-R-uuid main
mctl -t NAME workspace align /independent/remote/slot WS-R-uuid main \
  '{"prs":[{"owner":"ORG","repo":"REPO","branch":"dev/review","pr_number":123}]}'
```

This command does **not** call `find_ws` or `map_remote`: the absolute path is
already the target's path. All calls require a stable remote ID and configured
manifest baseline revision (`main` or a release branch/tag, **not a counter**).
An optional final JSON argument is a mirror object or `null` for `mirror`;
other actions accept a `prs` list. The portal's `free` action also accepts
`{"portal_free":{"approved":null}}` for the initial check or a 64-character
snapshot token after explicit confirmation. The remote runtime rechecks that
snapshot and can return `approval_required` without changing the checkout.
This option is invalid for other actions; ordinary `mctl release` retains its
guarded behavior. PR alignment is passed to the runtime; the transport never
invokes remote `gh`.

Portal `sync` accepts `{"portal_sync":{"approved":null}}` or the confirmed
snapshot token for **Force sync** on free independent workspaces only. It
uses the same safe stashing and revision-return flow without releasing the
workspace; occupancy and mirror guards are not bypassed.

The local portable `workspace-manager/workspace_runtime.py` is streamed as data
to a private temporary file through a login-shell transport, with shell-quoted JSON:
`{action,path,id,rev,mirror?}` (or `prs` for review alignment, `portal_free`
for a portal-confirmed return, and `portal_sync` for confirmed Sync). No remote
installation beyond Python's standard library and the existing `wip`/`octl` for repository
operations is needed, and no remote `rtk` is used. The runtime file is removed
when the command exits. Stdout is the runtime's
single JSON response `{ok,error?,status?,log?}`. Domain failures preserve JSON
and exit zero at this transport layer; callers must check `ok`. SSH/transport
errors retain nonzero exit status and stderr, with no automatic retry.

Remote identity/mirror state and append-only history live under
`~/.mctl/workspaces/<sha256(canonical-path)>.json` and `.jsonl`. A mirror object
contains `source_id`, `source_path`, `source_host`, `state`, and `updated`.
Use `mirror ... null` to clear a relation only after successful free (normally
free itself performs this). Portal Free calls this same `workspace free`
transport directly and reconciles the registry from returned status. A later
`mctl mirror` always reads the live marker instead of assuming the last cached
relation is still active.

Mirror/free/sync/align and occupancy changes have per-workspace action history.
The workspace-scoped `build`, `test`, `run`, `wip`, `sh`, `pull-build`, and
`pull-logs` commands also append outcome events to the local and cached remote
workspace histories. These events contain action, outcome and target only—not
command text, arguments, credentials or output. Build/test/run entries mean
**dispatch outcome**; the existing job history tracks eventual completion.
History logging preserves the command's exit code and warns if the event could
not be persisted.

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

## Pitfalls seen on a reused VM slot (2026-10-26)

Mirroring `a1_power_on_003` to the VM, then running a full build. For the build-side
pitfalls (`twister-out`, external-FW versions, missing guards), see `build-fw`, section
"Full-workspace build on a reused VM slot".

| Pitfall | Symptom | Do this instead |
| --- | --- | --- |
| **`octl ws sync` on the target may not move `.repo/manifests`** | Exited 0 with no output, and the target manifest stayed at an older `main` commit (`a7c7bbd`) while local was at `31d51c5` | If the local commit is a descendant: `mctl sh 'git -C .repo/manifests fetch -q origin && git -C .repo/manifests merge --ff-only <local-sha>'`, then re-check parity |
| **Mirror refused: "remote workspace is missing or unsafe"** | The slot was still on an old experiment's `dev/*` branches with untracked files. `mctl -t vm workspace status <path> <WS-R-id> <rev>` lists the reasons, and `mirror` was `None` | Inspect before freeing. For each repo on a `dev/*` branch, check `ls-remote` + `merge-base --is-ancestor`, and back up the branch map with `wip scan && wip save <name>` **on the target**. Push any commit that is only local, or ask the user. Only then free the slot |
| **Guarded `workspace free` fails on an older target `octl`** | `octl ws sync --no-plugins: ERROR: unknown flag: --no-plugins`. The checkout half had already run, so `status` then showed `safe=True` | Run `octl update` on the target. A partly completed free can still leave the slot safe, so re-check `workspace status` before retrying |
| **`mirror` verification fails on baseline repos that are behind locally** | `REPO … LOCAL_SHA … REMOTE_SHA` mismatch for `infra` and `build_framework`, local one commit behind origin | Either `wip ws pull` locally first, or, to reproduce local exactly, pin the target with `git checkout --detach <local-sha>`. Then diff per-repo `rev-parse HEAD` lists for both sides |
| **Detached build hangs silently on expired AWS SSO** | `build-status` says RUNNING, `build.log` is 0 bytes, and the child is `aws sso login --no-browser --profile devops-cli` | Kill the job. `mctl sh` captures output, so the device URL never shows. Run `ssh -A -tt "$(mctl -t <vm> dest)" 'bash -lc "aws sso login --no-browser --profile devops-cli"'` (async) and hand the URL/code to the user. If the CLI then reports `InvalidGrantException`, check `aws sts get-caller-identity` before re-prompting, because the token may already be valid |
| **`mctl sh` grep over `~/.mctl/jobs/*/build.log` matches other workspaces' logs** | Old `FAILED BUILDS` lines from other slots get mixed into the result | Grep this job's log only: `~/.mctl/jobs/<home-relative-path-with-underscores>_fw/build.log` (for example `projects_iridium_a1_power_on_003_fw`) |
| **Git hooks on the target reject non-`dev/<user>/…` branch names** | `Invalid branch name. Must follow the convention…` on `checkout -B test/…` | The checkout still happens (only a warning), but use `dev/<user>/…` for scratch branches to avoid confusion. Never push scratch branches from the target |

## Script reference

`~/bin/mctl` (on `PATH`; `~/bin/vmctl` is a backward-compat shim). Run `mctl
help` for the built-in usage summary. The script is intentionally
self-contained: local state lives in `~/.mctl/` (per-target IP/home caches +
per-target job dirs + `joblog.jsonl` (external producer) / `delegation.db` (local observations)) so builds/tests keep
running and remain pollable even across disconnects and token-less sessions.

`~/.copilot/skills/vm-delegate/vd.py` is the companion resolver: it merges the
append-only dispatch log with a `jobs-probe` of each live target, mirrors the
remote logs locally, and exposes `list` / `refresh` / `sync` / `log` / `tail` /
`payload` / `vms` / `start` / `stop` (plus `payload()`, `portal_refresh()`,
`portal_machines()`, `portal_power()`, `portal_sync()`, `portal_log()` and
`portal_tail()` as a module, which is how the Team Portal's VM tab is fed).
