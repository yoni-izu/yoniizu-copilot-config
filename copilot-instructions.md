# Always loaded project context instructions

## VM Management
For any questions about VMs, EC2 instances, machine IPs, or SSH access, delegate to the `@vm` agent which manages instances via `octl vm`.

## Multi-repo / workspace git — use the `wip` CLI, never raw git loops
The FW workspaces are multi-repo (repo/west) checkouts. For ANY operation that
spans more than one repo — checkout, push, pull, align, stash, pop, diff,
return-to-baseline, hard-reset, or saving/restoring a composite branch state —
use the standalone **`wip` CLI** (`~/bin/wip`). Do **not** hand-roll a
`for repo in ...; do git ...; done` loop over the worktrees; it wastes tokens and
misses `wip`'s mode/dirty/detached-HEAD handling.

```bash
wip scan                 # discover workspaces + per-repo branches under $PWD
wip status               # print the current composite state
wip ws checkout|push|pull|align|stash|pop|diff|return|hard-reset   # this workspace
wip all <action>         # same, across every workspace in the WIP root
wip save <name>          # snapshot the composite branch map (~/.wip/saved/<name>.wip)
wip load <name>          # restore a saved snapshot, then `wip all checkout`
```

Prefer snapshots for repeatable checkouts: `wip save <name>` once, then re-align
later with a single `wip load <name> && wip all checkout` instead of re-deriving
branches. Destructive actions (`align` in mirror mode, `hard-reset`, `return`)
need `--yes`. See the `wip-workspace` skill for full semantics, and `vm-delegate`
for delegating the same ops to the VM mirror.

<!-- rtk-instructions v2 -->
# RTK — Token-Optimized CLI

**rtk** is a CLI proxy that filters and compresses command outputs, saving 60-90% tokens.

## Rule

Always prefix shell commands with `rtk`:

```bash
# Instead of:              Use:
git status                 rtk git status
git log -10                rtk git log -10
cargo test                 rtk cargo test
docker ps                  rtk docker ps
kubectl get pods           rtk kubectl pods
```

## Meta commands (use directly)

```bash
rtk gain              # Token savings dashboard
rtk gain --history    # Per-command savings history
rtk discover          # Find missed rtk opportunities
rtk proxy <cmd>       # Run raw (no filtering) but track usage
```
<!-- /rtk-instructions -->