---
name: rtl-vm-policy
description: "Route EVERY RTL/HDL question to Neo (neo-brain on the VM) via ~/bin/neo-ask — never local/VM RTL checkouts or GitHub. Use when locating, reading, reviewing, comparing, bisecting or researching RTL/HDL, A1-vs-A2 RTL deltas, RTL PRs, generated RTL, or historical skill notes."
---

# RTL questions: Neo only

This policy applies to RTL/HDL wherever it lives, including generated RTL in
mixed repos and RTL PRs. Local firmware, non-RTL CSR specs and generated
software register headers keep their normal workflows. Repo names, commit
IDs and PR path metadata may be handled locally as long as no source,
patch or diff hunk comes back.

## The only RTL source: Neo

[Neo](https://elementlabs.atlassian.net/wiki/spaces/AI/pages/1162805286/Neo+-+Getting+started)
(`Elmnt-Internal/ip.infra.vlsi.arch.neo`, plugin `neo-brain`) is installed on
the `vm` mctl target. It answers A1 (Iridium) and A2 (Atlantic2) questions
grounded in its own RTL mirrors, plus specs and uarch notes. Ask it from WSL:

```bash
~/bin/neo-ask "How is PERST source selected in PSOC on A1?"
~/bin/neo-ask --chip a2 "What changed in the D2D PHY wrapper vs A1?"
echo "long question…" | ~/bin/neo-ask -
NEO_ASK_TIMEOUT=1800 ~/bin/neo-ask "…"        # long deep-dives (default 900 s)
```

`neo-ask` SSHes to the VM and runs `copilot -p` (Copilot CLI, the
experimental Neo surface) from `~/neo`, with write/push/rm denied. It
instructs Neo to answer in paraphrase with citations: repo, path,
symbol, line range and mirror commit. It also redacts any HDL-looking
lines before output reaches WSL.

1. **Ask Neo, nothing else.** For RTL behaviour, signals, register
   implementation, resets/clocks, A1-vs-A2 deltas, which repo/file/module
   implements something, RTL PR review and RTL bisection, call `neo-ask`.
   Never read RTL from a local checkout, from the VM worktrees under
   `~/projects/iridium/rtl/ip/*`, or from Neo's mirror directories directly.
   Never use the GitHub content/search/compare APIs or `gh pr diff` on RTL,
   and never use `mctl sh`/ssh grep on RTL. Neo handles its own mirrors.
2. **RTL PRs**: give Neo the repo, PR number and head branch, and ask it to
   fetch that ref into its mirror and assess the change. It returns
   paraphrased findings only. Local helpers may still classify PRs from path
   metadata (`rtl_pr_guard.py`), but they never fetch hunks.
3. **Output stays source-free.** Relay only paraphrased behaviour plus
   citations. Never ask for, store or reproduce verbatim HDL, patches, diffs
   or source-equivalent snippets. That includes HDL excerpts already present
   in old local skill notes: do not read or quote them. Treat knowledge
   indexes as unverified, and consult one only when it's known to be
   excerpt-free.
4. **Mark confidence.** Anything Neo tags `[unverified]`, or that it didn't
   ground in RTL, stays unverified in your answer. Quote the mirror commit
   when it matters (A1 is frozen; A2 is `main` and moves).
5. **No fallback.** If `neo-ask` fails, report the prerequisite and stop. The
   usual causes are a stopped VM (`mctl -t vm start`), a logged-out Copilot
   CLI on the VM (`ssh -t <vm> 'copilot login --device-code'`), or stale
   mirrors. Do not improvise local, GitHub or VM-worktree reads.

## Neo deployment on the VM (for maintenance)

| Item | Value |
| --- | --- |
| Clone | `~/neo` (`git pull` to update; plugins load live) |
| Copilot CLI | `~/.local/bin/copilot`; marketplaces `neo` → `~/neo`, `ai-toolkit` → `~/ai-toolkit`; plugins `neo-brain`, `neo-poc`, `neo-ingest`, `hwarch-specs` |
| Claude Code (optional) | `neo-dev` launcher in `~/bin`; needs a Claude license + `/login` (`NEO_ASK_BACKEND=claude`) |
| Mirrors | `NEO_MIRRORS=~/projects/iridium/rtl/mirrors/a1`, `NEO_A2_MIRRORS=…/mirrors/a2` (in `~/.neo/config` and `~/.bashrc`) |
| Refresh mirrors | `bash ~/neo/plugins/neo-brain/scripts/mirror_repos.sh [--chip a2] --fetch -j 8` on the VM |

Preserve the provenance of historical findings. Old local paths or GitHub
comparisons record past observations; they do not authorize repeating
those workflows.
