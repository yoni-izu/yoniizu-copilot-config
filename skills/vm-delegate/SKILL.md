---
name: vm-delegate
description: "Delegate FW workspace work to the EC2 VM mirror workspace via the vmctl script. Use when: mirror/align a workspace to the VM, remote build on the VM, run a full parallel workspace build on the strong machine, pull build results/binaries/logs back from the VM, run workspace tests on the VM, release/clean up the remote mirror workspace, run any wip_/ws_ git operation on the VM, execute a command on the VM and pull its output, or delegate long-running work that must survive SSH hangups."
argument-hint: "Operation (mirror/build/build-status/test/test-status/pull-build/pull-logs/release/wip/sh) and optionally a workspace path"
---

# VM Delegate — Mirror-Workspace Work Offloading

Offload heavy FW work to the EC2 VM (192 cores) that hosts **mirror workspaces
at the exact same paths as local**. All operations go through one script,
`~/bin/vmctl`, so token usage stays low and every action can be run by hand with
no AI involvement.

> **Complements the `@vm` agent.** That agent handles VM *lifecycle* (create /
> start / stop / list). This skill handles *delegating work* to an
> already-running VM. `vmctl` auto-discovers the VM's private IP via
> `octl vm list` — **never hardcoded**. The IP is cached in `~/.vm_ctl/ip` but
> the cache is *self-validating*: it is reused only while SSH/22 is reachable, so
> creating a new VM (new IP) or tearing down the old one auto-invalidates it.
> Force a refresh with `vmctl ip --refresh`; override entirely with `VM_IP=`.

## Golden rules (why the script exists)

- **Prefer `vmctl <sub>` over hand-rolled `ssh`.** Do not reconstruct the SSH /
  `nohup` / `rsync` incantations in the model — call the script. It already
  encodes every gotcha below.
- **Home dir is identical local↔VM**, so a local absolute workspace path *is*
  the remote path. The script resolves the workspace by walking up for `.repo`.
- **The VM pulls from GitHub — it does not rsync source.** Branches must be
  pushed before a remote build; `vmctl mirror` does that for you.
- **Long jobs survive hangups.** Builds/tests run under `setsid nohup` on the VM
  with a PID + log file under `~/.vm_ctl/jobs/<tag>/`. No persistent SSH session
  is needed — poll from a fresh connection with `*-status`.

## SSH gotchas the script already handles (do not re-derive)

- Always `ssh -A` — the VM has **no GitHub key of its own**; agent forwarding is
  what makes `git fetch` work on the VM.
- `octl` and the `wip` CLI are **not on the non-login PATH** — every remote
  command runs inside `bash -lc "..."`.
- **The VM must have the `wip` CLI.** All remote git ops (`mirror`, `release`,
  `vmctl wip …`) call `~/bin/wip` on the VM. A freshly created mirror may not
  have it yet — if a remote `wip` command fails with `command not found`, deploy
  it once with the helper script, then retry:
  ```bash
  ~/bin/wip-deploy "$(vmctl ip)"
  ```
- `rtk` does **not** exist on the VM — never prefix remote commands with `rtk`.

## Operations

| You want to… | Command |
|---|---|
| Align the remote mirror to my local branches | `vmctl mirror [ws]` |
| Full parallel build on the VM (detached) | `vmctl build [ws] [extra args]` |
| Check the remote build | `vmctl build-status [ws]` |
| Pull binaries **and** logs back (as if built locally) | `vmctl pull-build [ws]` |
| Pull just logs for analysis | `vmctl pull-logs [ws]` |
| Run workspace tests on the VM (detached) | `vmctl test '<octl ws test args>' [ws]` |
| Check the remote test run | `vmctl test-status [ws]` |
| Clean up: return remote repos to baseline + sync | `vmctl release [ws]` |
| Any `wip` git op on the VM | `vmctl wip <action> [ws]` or `... --wip` |
| Run an arbitrary command in the remote ws root | `vmctl sh '<cmd>' [ws]` |
| Print the VM private IP (auto-discovered) | `vmctl ip` (add `--refresh` to re-discover) |

`[ws]` is optional — omit it to use the workspace containing the current
directory. Run from inside the FW workspace (e.g. `~/projects/iridium/one/fw`)
and the path is inferred.

## Standard delegated-build flow

```bash
cd ~/projects/iridium/one/fw
vmctl mirror            # push local dev branches + align the VM mirror
vmctl build             # detached parallel `octl ws build --parallel`
vmctl build-status      # poll (repeat from a fresh shell; no session held)
# ...when STATE=DONE...
vmctl pull-build        # rsync binaries + logs back to the identical local path
```

When reporting to the user, poll with `build-status` between turns (never
`sleep`-loop). `STATE=RUNNING`/`DONE`, an `ERRORS=` count, and a log tail are
printed each poll. For the full deduplicated error list after completion:

```bash
vmctl sh 'grep -E "error:" ~/.vm_ctl/jobs/*/build.log | sed "s#.*/fw/##" | sort -u | head'
```

## What `mirror` does (and the force-push caveat)

It is a thin, token-cheap wrapper around the `wip` CLI — a fresh local scan is
snapshotted into a `wip save` file, shipped to the VM, and replayed there. The
`.wip` save file **is** the transferred branch scheme (its `# WIP_INFO` section
maps `ws:repo=path:branch` for every repo), so the VM never has to be told the
branch list separately:

1. Local: `wip scan` (fresh scan of the workspace) → `wip ws push --yes`
   (force-push every non-baseline branch to origin) → `wip save <tag>` (save the
   branch map to `~/.wip/saved/<tag>.wip`).
2. `scp` that one saved map to the VM's `~/.wip/saved/`.
3. VM: `wip load <tag>` (consume the branch map) → `wip ws checkout` (check out
   every recorded branch) → `wip ws hard-reset --yes` (fetch + `reset --hard
   origin/<branch>` so a rebased/force-pushed branch is reproduced *exactly* —
   this is mode-independent, unlike `align`, so the mirror is an exact copy of
   the pushed state regardless of the VM's `wip` mode).

Because step 1 **force-pushes** your dev branches, only run `mirror` when you
intend to publish the current local state. After a local rebase, re-run
`vmctl mirror` to bring the VM back in sync.

## Testing on the VM

`vmctl test '<args>'` runs `octl ws test <args>` detached. Pass whatever
`octl ws test` accepts, e.g.:

```bash
vmctl test 'ip/pcie/ep --group fast --show-summary'
vmctl test-status          # STATE + tail
vmctl pull-logs            # bring test logs local for analysis
```

## Releasing the mirror

`vmctl release` returns every repo in the remote workspace to its manifest
baseline (`main` / the project fb tag) via `wip ws return` and runs `octl ws
sync`. Use it when you're done with a delegated task so the mirror is clean for
the next one.

## wip/ws operations on the VM

Any action supported by the `wip` CLI (`checkout`, `push`, `pull`, `align`,
`set-upstream`, `stash`, `pop`, `diff`, `return`, `hard-reset`) runs on the VM.
Legacy underscore names (`hard_reset`, `set_upstream`) are still accepted:

```bash
vmctl wip align            # ws-scope: rebase this workspace's branches on the VM
vmctl wip diff             # generate diff patches on the VM
vmctl wip return --wip     # wip-scope: return ALL VM workspaces to baseline
```

Default scope is the single workspace (`ws_*`); pass `--wip` for all-workspaces
(`wip_*`). See the `wip-workspace` skill for the semantics of each action.

## Failure-triage reminders (shared with `build-fw`)

- `octl ws build` (even `--parallel`) **aborts at the first failing project**.
  If an unrelated early project is broken, build the rest individually with
  `vmctl sh 'octl ws build <project>'` to still get coverage.
- **Never call `main` broken from a single-project build.** A valid baseline is
  a **clean full `octl ws build`** on an all-`main` workspace. See the
  `build-fw` skill's "Prove a failure is pre-existing" and "Known false
  failures" sections before blaming a change.
- Deduplicate errors — the same `error:` repeats per translation unit.

## Script reference

`~/bin/vmctl` (on `PATH`). Run `vmctl help` for the built-in usage summary. The
script is intentionally self-contained: state lives in `~/.vm_ctl/` (IP cache +
per-workspace job dirs) so builds/tests keep running and remain pollable even
across disconnects and token-less sessions.
