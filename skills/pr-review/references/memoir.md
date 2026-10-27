# PR Review Memoir

> **Note (2026-10-26):** The previous memoir was lost when the `pr-review` skill directory was
> re-synced around 20:14–20:27 (it wiped untracked `drafts/` and `references/`). The content below
> is only what was learned in the MODINFRASW-176 review; earlier accumulated lessons are **not**
> reproduced here because they could not be recovered without inventing them. Restore from backup
> if one exists and merge.

## Dedup / cleanup PRs

- **Verify the duplication is gone, not just that the deletions were safe.** The natural failure mode
  is to confirm every removed line is inherited from the base (necessary, and reassuring) and stop
  there. That only proves the deletions don't regress anything — it says nothing about what remains.
  Always re-scan the *end state* of each touched file against its full layer stack. On MODINFRASW-176
  this flipped three `approve` recommendations to `comment`: 26 duplicates survived in files the PRs
  had actively cleaned.
- **Check the scope dimensions the PR never enumerates.** The four PRs cleaned A1 revision `.conf`
  files. Silently untouched: all A2 revision files, and (for mng) every overlay — despite the title
  saying "conf/overlay". Enumerate the full matrix (SoC rev × variant × file type) and diff it against
  what the PR touched; the gap is the finding.
- **"No-op" is the wrong word for a duplicate.** Inert in today's generated output ≠ harmless. The
  duplicate pins the value locally, so a later change to the base is silently shadowed — which is the
  whole rationale for the cleanup. Lead comments with the shadowing hazard, not with "this has no
  effect", which argues against the PR's own goal.

## Build-system layering (Zephyr)

- **Derive the layer stack from the build system source; never assume it.** For Zephyr 3.7:
  `BOARD_DEFCONFIG → prj.conf → boards/<board>.conf → boards/<board>_<rev>.conf`
  (`cmake/modules/kconfig.cmake` `merge_config_files`), with base-before-revision guaranteed by
  `zephyr_build_string(... MERGE REVERSE)` in `cmake/modules/extensions.cmake`. Overlays follow the
  same two-level pattern on top of the board `.dts`.
- **There is no intermediate variant layer.** `MERGE` emits exactly two names (with and without the
  revision), so `el_arm_a1.conf` is never consulted — don't assume a SoC-wide tier exists.
- **Remember the Kconfig `default` tier.** Beyond `_defconfig` there are `Kconfig.defconfig` /
  `Kconfig.mem_defaults` defaults, which an app's own `Kconfig.mem_budget` can pre-empt ("first
  default parsed wins"). A `.conf` line restating a default is redundant too — but it's a weaker,
  nit-level finding than duplicating an explicit base assignment, and conditional defaults need the
  board context evaluated before claiming redundancy.
- **Devicetree properties merge; nodes are not replaced.** A revision overlay restating base
  properties is redundant, and *omitting* a property from a node it re-opens does **not** unset it.
  A `chosen`/`aliases` block that lists a subset of the base's properties therefore contributes
  nothing at all and can be deleted whole.

## Scanning technique

- **Separate exact repeats from conflicting repeats.** Same key twice with the same value is noise;
  same key with *different* values is a latent bug (last wins, silently). A first-pass script that
  reports "duplicate of line N" without comparing values conflates them — and the conflicts are the
  findings that actually matter (e.g. `EL_MW_SECURITY=y` then `=n`; a duplicated `# Interrupts` block
  flipping `GEN_ISR_TABLES` n→y).
- **Validate the scanner before trusting its volume.** 437 hits demanded a spot-check against raw
  files first. The check that earned confidence was finding a node where the scanner flagged only
  `status` as duplicated while correctly ignoring a genuine override (`axi_sec_die_count` 4→1).
- **Normalise before comparing**: quoted vs bare values, `# CONFIG_X is not set` ≡ `CONFIG_X=n`, and
  tabs vs spaces (several "duplicates" differed only in indentation).
- **Preserve line numbers when stripping comments** — replace block comments with equivalent newlines,
  otherwise reported line numbers drift and every anchor is wrong.

## Posting mechanics

- **Compute inline-comment reachability before promising inline anchors.** A review comment can only
  attach to a line present in the PR diff. On deletion-heavy PRs, surviving duplicates are often
  context lines *outside* the hunks — those must go in the review body instead. Parse the `-U3` diff
  into the set of new-side line numbers and test each finding against it.
- **Group per file, anchored at one reachable line.** 26 findings became 5 inline comments plus body
  text. Separate nits for each property would have buried the signal.
- **Reply in an existing thread instead of re-raising it.** A teammate already owned the stale-comment
  nit; a `+1 — still open, and here's why it's now actively misleading` reply adds weight without
  creating a duplicate the author has to triage twice.
- **Keep pre-existing issues off the PR.** Problems in base files the diff never touches (conflicting
  assignments, defconfig duplication) belong in a follow-up ticket; posting them on the PR is scope
  creep and dilutes the actionable findings.
- **Re-verify every head SHA immediately before posting.** Cheap, and it's the one check that
  invalidates an entire review if skipped.

## Durability

- **Skill-local `drafts/` and `references/` can be wiped by a skill re-sync.** Both vanished
  mid-session here. GitHub is the durable record — post (or otherwise persist outside the skill dir)
  rather than assuming local drafts survive.
