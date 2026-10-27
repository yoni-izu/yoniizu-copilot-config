---
name: build-fw
description: "Build FW (firmware) Zephyr applications for specific boards/variants/platforms and pristine ASIC flash images. Use when: user wants to build fw, build an ASIC flash image, build primeboot/mng/fla/pwr_thrm/sve app, build for a specific board variant or platform, list supported boards for an app, run a full workspace build, delegate a build to the VM mirror workspace."
argument-hint: "App name and optionally: platform (host|arm), variant (sim|palladium|onix|asic|...), is_test (true|false)"
---

> **Portal handoff:** For Team Portal/dashboard questions, feature requests,
> UI/API changes, bugs or operations, load
> [portal-maintainer](../portal-maintainer/SKILL.md). Domain records and
> workflows remain owned by this skill; coordinate mixed changes with the maintainer.

# Build FW Environment

## When to Use

When the user wants to build a firmware Zephyr application — optionally targeting a specific platform, variant, or test configuration.

## Overview

The FW build system uses `octl ws build <project_name> --args "..."` to build.

Each Zephyr app lives under `zephyr_apps/<app>/` and has a `.star` file that defines `FW_BOARD_BUILD_LIST` — the list of supported board configurations.

## Step 1: Find the FW Workspace Root

Search upward from CWD for a directory containing `zephyr_apps/`. If not found, check common paths like `~/projects/iridium/*/one/fw` or ask the user.

```bash
# Walk up from CWD looking for zephyr_apps
dir=$(pwd)
while [ "$dir" != "/" ]; do
  [ -d "$dir/zephyr_apps" ] && echo "$dir" && break
  dir=$(dirname "$dir")
done
```

If no workspace is found, also try:
```bash
find ~/projects -maxdepth 5 -name "zephyr_apps" -type d 2>/dev/null | head -5
```

## Step 2: Map App Name to Project Name and Star File

The user may refer to apps by short name, folder name, or alias. Use this mapping:

| User input (any of these)            | Folder         | Project name                              | Star file                                                   |
|--------------------------------------|----------------|-------------------------------------------|-------------------------------------------------------------|
| primeboot, psoc_primeboot, owbp      | primeboot      | project.iridium.sw.fw.psoc_primeboot      | zephyr_apps/primeboot/project.iridium.sw.fw.psoc_primeboot.star |
| fla, fla_runtime, owbf               | fla_runtime    | project.iridium.sw.fw.fla_runtime         | zephyr_apps/fla_runtime/project.iridium.sw.fw.fla_runtime.star  |
| mng, psoc_mng, owbm                  | mng            | project.iridium.sw.fw.psoc_mng            | zephyr_apps/mng/project.iridium.sw.fw.psoc_mng.star             |
| pwr_thrm, power_thermal, thermal, owbt | pwr_thrm     | project.iridium.sw.fw.psoc_power_thermal  | zephyr_apps/pwr_thrm/project.iridium.sw.fw.psoc_power_thermal.star |
| sve, sve_app                         | sve            | project.iridium.val.sve_app               | zephyr_apps/sve/project.iridium.val.sve_app.star                |

If the user doesn't specify an app name but is inside a `zephyr_apps/<app>/` directory, infer the app from the path.

## Step 3: Parse Supported Board Configurations

Read the star file and extract `FW_BOARD_BUILD_LIST`. Parse each `fw_board_config(...)` call (skip commented-out lines starting with `#`) to build a list of supported configs.

Each `fw_board_config` has these fields:
- `platform` — `"host"` or `"arm"`
- `variant` — e.g. `"sim"`, `"palladium"`, `"onix"`, `"asic"`, `"sim_single_die"`, `"nohw"`, etc.
- `board` — the board path (e.g. `"el_host_a1/native/sim"`)
- `is_test` — optional boolean, defaults to `False`

Use a quick Python or grep approach to extract them:

```bash
grep -v '^\s*#' <star_file> | grep 'fw_board_config('
```

## Step 4: Determine What to Build

### If user asks to list supported boards/variants:
Display a formatted table of the parsed `FW_BOARD_BUILD_LIST` for the app(s).

### If user specifies a platform and/or variant:
1. Validate that the combination exists in `FW_BOARD_BUILD_LIST` (ignoring commented-out entries).
2. If not found, show available options and stop.
3. If found, proceed to build.

### If user asks to build without specifying platform/variant:
Ask the user to pick from the available options (show as a table). Or if they said "build all", proceed without `--args`.

## Step 5: Construct and Run the Build Command

The build command format:
```
cd <ws_root> && octl ws build <project_name> [--args "<key=value> ..."]
```

Build args that can be passed:
- `platform=<host|arm>`
- `variant=<variant>`
- `is_test=<true|false>` (include only when explicitly requested; default omits it which builds non-test)

**Examples:**
```bash
# Build primeboot for arm/onix
cd ~/projects/iridium/fb_983/one/fw && octl ws build project.iridium.sw.fw.psoc_primeboot --args "platform=arm variant=onix is_test=false"

# Build mng for host/sim
cd ~/projects/iridium/fb_983/one/fw && octl ws build project.iridium.sw.fw.psoc_mng --args "platform=host variant=sim is_test=false"

# Build fla_runtime for arm/palladium
cd ~/projects/iridium/fb_983/one/fw && octl ws build project.iridium.sw.fw.fla_runtime --args "platform=arm variant=palladium is_test=false"

# Build sve for host/sim with basedon_app=primeboot
cd ~/projects/iridium/fb_983/one/fw && octl ws build project.iridium.val.sve_app --args "platform=host variant=sim basedon_app=primeboot is_test=false"

# Build all variants of primeboot (no --args filter)
cd ~/projects/iridium/fb_983/one/fw && octl ws build project.iridium.sw.fw.psoc_primeboot
```

**SVE special argument:** SVE supports `basedon_app=<primeboot|pwr_thrm|mng>` to build only one sub-app. Include it when the user specifies it.

## Step 6: Show Build Command Before Running

Always print the exact command you are about to run, then execute it. This lets the user verify and re-use the command later.

## Step 7: Report Result

After the build completes (or fails), summarize:
- ✅ Success: app name, platform, variant
- ❌ Failure: show the relevant error lines from the output
- For any image: the per-repo **base freshness** (behind count vs `origin/<base>`
  at build time), image SHA-256 and FPT UUID. Never answer "is it on the latest
  base?" without re-running the check. The base moves.

## Focused proj_infra Image Builds

### ASIC flash images: always pristine

Whenever the user asks to **build an ASIC flash image**, always perform a
pristine build, locally or on the delegated machine. An incremental build or
reusing an existing image does not satisfy this request.

1. Record the intended source revisions and preserve any uncommitted work before
   cleaning or syncing. Use the `wip-workspace` skill and `wip` CLI for
   workspace-wide source-state operations; never discard user changes.
   **Every dev branch must be rebased onto the current `origin/<base>`, and every
   baseline repo fast-forwarded, before building.** `mctl build` refuses
   otherwise. Locally, check with `git rev-list --count HEAD..origin/<base>`
   per repo; anything non-zero means stop and rebase/ff.
2. Run `octl ws clean` in the actual build workspace before building, **and
   wipe `zephyr_apps/*/build` plus every `twister-out*`**. `octl ws clean` leaves
   Twister output behind, and stale syscall headers there broke two rebuilds.
   On the VM, `mctl build --pristine` does this. Follow the
   clean + sync requirement below, restore the intended source state after sync,
   and verify it before proceeding. Pristine refers to generated build outputs;
   it does **not** mean switching the source to `main` or a manifest baseline.
3. Rebuild the image and its required dependencies from the cleaned workspace
   with `octl ws build proj_infra --with-deps --parallel`, using the requested
   ASIC configuration. Do not substitute standalone application builds.
4. Verify successful completion and that the requested image was freshly
   generated by this run before reporting success. Report its path and SHA-256.
   If cleaning fails, inspect the failure and verify the relevant outputs were
   removed; only the documented missing-`common.star` case below may be treated
   as benign after that verification. Stop on other clean failures.

### Dependency-aware image build

`project.iridium.sw.fw.infra` (proj_infra) regenerates the SPI image and other
post-build artifacts. For a flash-image request, do not build every firmware
application individually. Use the dependency-aware proj_infra build:

```bash
cd <ws_root> && octl ws build project.iridium.sw.fw.infra --with-deps --parallel
```

> ⚠ On `iridium_release_0.5.152` (2026-10-15) this name form failed with
> `requested repo is not in the graph`; the repo path form
> `octl ws build proj_infra --with-deps --parallel` works (verified). See
> "Delegated image-build gotchas".

`--with-deps` brings in the required dependency projects, and `--parallel`
keeps the build concurrent. This is the preferred focused image-build path,
including platform/variant selection supplied by the workspace build setup.

Use an individual application command only when the user explicitly asks to
build or validate that application, for example:

```bash
cd <ws_root> && octl ws build project.iridium.sw.fw.psoc_mng \
  --args "platform=arm variant=asic_single_die is_test=false"
```

> **Clean + sync required after post-build script updates.** The proj_infra
> post-build scripts were recently updated. After syncing, **clean and sync the
> workspace before building** — without a clean, the proj_infra build will fail.

## Delegating a Full Workspace Build to the VM

A full `octl ws build` is slow on the laptop. The user keeps a **mirror workspace
on their EC2 VM at the exact same path** as the local one, so full-workspace
builds can be delegated there (192 cores).

**When to offer this:** any time the request implies a full workspace build, a
multi-project build, or "build everything". **Prompt the user first** — do not
delegate unilaterally. Ask: *"Full workspace build — want me to run it on the VM
mirror workspace instead? It's much faster."*

### Use the `vm-delegate` skill / `mctl` — do not hand-roll SSH

The entire delegation flow is encoded in `~/bin/mctl` (formerly `vmctl`; see the
**`vm-delegate`** skill). It handles target/VM discovery, `ssh -A`, login-shell
exec, home-relative path mapping, branch alignment, detached (hangup-proof)
builds, and pulling results back. It targets any machine (a VM or a lab desktop)
via `-t <target>`. Do **not** reconstruct the raw `ssh`/`nohup`/`rsync` commands
here.

```bash
cd <ws_root>
mctl mirror         # push local dev branches + align the mirror workspace
mctl build          # detached parallel `octl ws build --parallel`
mctl build-status   # poll (STATE=RUNNING/DONE, ERRORS=, log tail)
mctl pull-build     # rsync binaries + logs back to the local path
```

Poll `mctl build-status` between turns (never `sleep`-loop). For the full
deduplicated error list after completion, use the `vm-delegate` skill's grep
recipe. The remaining sections below (build-abort behaviour, proving a failure
is pre-existing, known false failures) apply to **both** local and VM builds.

### `octl ws build` aborts at the first failing project

A bare `octl ws build` stops the whole workspace at the first failed project, so
later projects never build. If an early project (e.g. `fla_runtime`) is broken
for unrelated reasons, build the rest individually so the change still gets
coverage:

```bash
for p in project.iridium.sw.fw.psoc_primeboot \
         project.iridium.sw.fw.psoc_power_thermal \
         project.iridium.val.sve_app \
         project.iridium.sw.fw.fla_rom; do
  octl ws build $p
done
```

Per-board logs live at `zephyr_apps/<app>/build_logs/<NN>_<variant>_<tag>.log`
(the path printed in the summary can be wrong — `ls` the directory).

> **Caveat:** per-project builds only work once a **full** `octl ws build` has
> populated `build_artifacts` in that workspace, and their results are only
> meaningful for projects whose dependencies were built. Never use a per-project
> build as evidence about `main` — see "Prove a failure is pre-existing".

### Prove a failure is pre-existing before blaming the change

**NEVER establish a baseline with `octl ws build <project>` (single project).**
That is the single biggest source of wrong conclusions — it produces errors that
look like real breakage but are artifacts of an incomplete/mismatched build tree.
A baseline is only valid as a **full `octl ws build` on a CLEAN tree in a
workspace whose repos are ALL on `main`**.

Preferred: use a **separate pristine workspace** (e.g. `~/projects/iridium/five`)
where every repo is already on clean `main` — never the workspace you have been
checking branches in and out of.

```bash
# verify EVERY repo is on clean main
for g in $(find . -maxdepth 4 -name .git -not -path "*/.venv/*"); do d=$(dirname $g);
  b=$(git -C $d rev-parse --abbrev-ref HEAD); dirty=$(git -C $d status --porcelain | wc -l)
  [ "$b" != "main" -o "$dirty" != "0" ] && echo "$d branch=$b dirty=$dirty"; done
octl ws build                      # FULL build, no project argument
```

Then re-test the branch with `octl ws clean && octl ws build` so both sides are
clean full builds. Also confirm with `git diff --name-only origin/main...HEAD`
that the failing file really is untouched.

### Known false failures — recognise before investigating

| Signature | Cause | Verdict |
| --- | --- | --- |
| `undefined reference to 'fla_rom_*'` (or any other project's symbols) at link | Built one project standalone; the providing project was never built in this workspace | **Invalid build**, not a defect. Run a full `octl ws build` first |
| `el_reset_common.h: error: 'el_reset_*' declared 'static' but never defined [-Werror=unused-function]` | Zephyr `__syscall` bodies come from generated `zephyr/syscalls/*.h`; a partial/stale tree never generated them | **Build-tree artifact**, not a source defect. Clean + full build |
| `zephyr/syscalls/<api>.h: error: conflicting types for 'z_impl_<fn>'` (e.g. `el_c2c_lut_write_addr_sel` after a rebase) | Generated syscall header predates a `__syscall` signature change; lives in `zephyr_apps/*/build` or `*/twister-out` | **Stale tree.** `mctl build` auto-wipes on HEAD change; locally `rm -rf zephyr_apps/*/build */twister-out* */*/twister-out*` and rebuild |
| `Failed to generate .../fla_rt_progress_bits.h ... gen_progress_bits.py ... os.remove(output_file) FileNotFoundError` | Race in `infra/scripts/gen_progress_bits.py` — parallel board builds generate the same header into the shared `zephyr_apps/<app>/include/` dir and race on delete | **Flake.** Narrowed by `7b742a89 [SW-0] fla_rt: make progress-bits header gen parallel-safe` but not fully closed. Re-run |
| `<rev> is not a valid revision for <proj>. Accepted revisions: ...` | Branch base predates a `revision.cmake` addition on `main` | **Stale base.** Rebase onto `origin/main` |
| DTS errors in bindings/overlays the branch never touched | Base drift / repo skew between IP bindings and the board DTS provider (`proj_infra`) | **Base incoherence.** Bring all repos to the same `main` |

Three of these produce *different* error text for the *same* underlying cause
(an invalid build), so matching on the error message alone will mislead — always
confirm the build was a clean full build before reading the errors at all.

### Delegated image-build gotchas (seen 2026-10-15, VM flash-image build)

Hit in sequence while delegating a proj_infra ASIC flash image to the VM. Each
one costs a wasted round trip; check them up front.

| Gotcha | Symptom | Do this instead |
| --- | --- | --- |
| `mctl build` takes the **workspace** as its first positional arg | `mctl build proj_infra --with-deps` → `cd: proj_infra: No such file or directory`, then a stray `octl ws build --parallel --with-deps` launches outside any ws and dies with `must be run from within a workspace directory` (harmless, but logged under a bare `~/.mctl/jobs/build.*`) | Always pass the ws first: `mctl build . proj_infra --with-deps` |
| `octl ws build` rejects the starfile/project name for proj_infra | `octl ws build project.iridium.sw.fw.infra --with-deps` → `building projects: unexpected error, requested repo is not in the graph` | Use the repo **path**: `octl ws build proj_infra --with-deps --parallel` (verified: resolves the graph and builds deps) |
| `octl ws clean` deletes `build_artifacts` **then** runs each starfile's clean task, which `load()`s `build_artifacts/opt/atl/devops/common.star` | `ERROR: executing clean: ip.infra.val.sve: ... cannot load build_artifacts/opt/atl/devops/common.star: no such file` | Benign — the artifacts are already gone. `octl ws sync` does **not** restore `common.star`; the next `octl ws build` re-extracts all CI artifacts (incl. `devops-configuration-ci`) first, so just build |
| `octl ws sync` resets every repo to its manifest baseline | After the mandatory clean+sync, the dev branches are gone and the build would silently use baseline source | Re-apply branches **after** sync (`mctl mirror`, or `git checkout -B <br> origin/<br>` per repo) and re-check `git log -1` on the target |
| `mctl mirror` can print `✔ mirror aligned` while the target repos stay on the baseline branch | `mctl sh 'git -C ip/se log --oneline -1'` shows the release head, not your commit | Always verify the target's HEAD per changed repo after mirroring; fix with `mctl sh 'git -C <repo> fetch origin <br> && git -C <repo> checkout -B <br> origin/<br>'` |
| `.repo/manifests` commit differs local vs target after `octl ws sync` | The parity `diff` fails | Not necessarily wrong: if the target commit is a descendant (`merge-base --is-ancestor <local> HEAD`) **and** the `fw_external/*.tar.gz` pins in `projects/<proj>/<rev>/psoc-fw-ci.xml` are identical, the build consumes the same external FW. Only pins matter |

### Rebase / stale-source pitfalls (2026-10-18, IPPCIESW-695): now enforced by `mctl build`

> ⚠️ **2026-10-26: these guards are NOT in the current `~/bin/mctl`.** The 2026-10-25 rewrite
> dropped them, and the last version with them is `~/bin/mctl.bak.20261018185316`. Apply each
> rule below by hand until they are restored. See "Full-workspace build on a reused VM slot".

An ASIC image was built from **pre-rebase heads** and flashed to all 8 cards of a
tray; the rebuild after the rebase then failed **twice** on stale generated
syscall headers. These are now **hard guards in `~/bin/mctl build`**. Do not
bypass them, and do not hand-roll `octl ws build` over `mctl sh`/`mctl run`
for an image (that skips the guards).

| Pitfall | Symptom | Guard / rule |
| --- | --- | --- |
| **Image built on a stale base.** Dev branches were behind `origin/<base>` (here, 20 SE, 11 proj_infra and 1 mng commits) | The build is green, but the image lacks merged power-on fixes. It was flashed and debugged as if it were current | `mctl build` fetches every manifest project's base (from `.repo/metadata.json` `manifest_revision` + the manifest XML) and **refuses** when any dev branch is `STALE-BASE`. Rebase, re-push, then re-mirror. `--allow-stale-base` exists only for deliberate repros, and the report must then say "NOT on latest base" |
| **Local baseline checkout behind origin** (`ip/mng`, `infra` on `a1_power_on_branch`) | `mctl mirror` fails "remote branch verification" (`local X vs remote Y`) | Same check reports `BASELINE-BEHIND` with the exact `git -C <repo> merge --ff-only origin/<base>` fix. Use `wip ws pull` for the whole ws |
| **Stale generated syscall headers after a rebase.** `zephyr/syscalls/*.h` in `zephyr_apps/*/build` and `*/twister-out` keep the old `__syscall` signature | `error: conflicting types for 'z_impl_el_c2c_lut_write_addr_sel'` (mng app, then again in `ip/mng` Twister `pmci.module.standalone`). Also `declared 'static' but never defined` | The `mctl build` remote prelude fingerprints every repo HEAD. If anything changed since the **last successful** build, it wipes `zephyr_apps/*/build` and every `twister-out*` before `octl` runs (`[mctl-guard]` lines at the top of the log). Use `--pristine` to force it. `octl ws clean` alone was not enough here: Twister output lives outside `build_artifacts` |
| The image build string does **not** identify the image | Two different images both report `1.729.0-18102026-113045` (manifest-pinned version stamp) | Identify images only by **SHA-256 + FPT UUID** (`xxd -s 0x40000 -l 0x30`, bytes 0x14-0x23). Put both in `CHANGE_NOTES.md` and verify the UUID after flashing |
| The base can move again **after** the build | 18:48 (+03): four reverts landed on `a1_power_on_branch` minutes after the image was built | Before flashing, or before reporting "based on latest", re-run the freshness check: `mctl build` dry state, or `git rev-list --count HEAD..origin/<base>` per changed repo. State the base SHAs in the notes |
| On the VM, `~/projects/iridium/a1_power_on` **is a symlink** to `a1_power_on_branch` | `readlink -f` paths point to the other workspace name | It is the **same tree**, not cross-workspace leakage. Mirroring a dev branch into `a1_power_on` also changes `a1_power_on_branch` on the VM |

Mandatory order for a delegated image (no exceptions):

```bash
wip ws pull                                      # baseline repos current (or ff per repo)
# dev branches rebased onto origin/<base> and pushed
mctl mirror --allow-manifest-drift              # only if pins are identical
mctl build . <target> --args "..."              # guards: freshness + stale-tree wipe
mctl build-status                                # confirm "[mctl-guard]" + rc=0
# after pull: record sha256 + FPT UUID + per-repo base SHAs in CHANGE_NOTES.md
```

Correct order for a delegated flash image:

```bash
mctl sh 'octl ws clean; octl ws sync'          # clean error above is benign
mctl mirror                                     # or per-repo checkout -B
mctl sh 'for r in <changed repos>; do git -C $r log --oneline -1; done'   # verify!
mctl build . proj_infra --with-deps             # ws arg FIRST
mctl build-status
```

### Full-workspace build on a reused VM slot (2026-10-26, LTSSM logger + MNG #441)

A full `octl ws build --parallel` on a VM slot reused from an old FW experiment
took **four** launches to get green. None of the failures were source defects:

| Pitfall | Symptom | Do this instead |
| --- | --- | --- |
| **The `mctl build` stale-tree and stale-base guards above are no longer in the script.** `~/bin/mctl` was rewritten on 2026-10-25 and has no `[mctl-guard]`, `STALE-BASE` or `twister-out` logic. Only `mctl.bak.20261018185316` predates the rewrite | Old trees survive the build and cause the syscall errors listed in "Known false failures" | Until the guards are restored, wipe the trees by hand before building (next rows), and check freshness yourself with `git rev-list --count HEAD..origin/<base>` |
| **`octl ws clean` does not remove `twister-out`.** It clears `build_artifacts` and the app `build/` dirs, but Twister output under `ip/*/twister-out`, `ip/*/*/twister-out` and `infra/el_utils/twister-out` survives | After a clean, the build still failed: `ip.pcie.sw.fw.ep` Twister `el_piw.host_test` reported `el_reset_config_comp_reset_mask_die declared 'static' but never defined`. The generated `syscalls/el_reset_common.h` was dated 2026-10-11, while `syscalls.json` was fresh | After `octl ws clean`, also run `find . -maxdepth 5 -type d -name 'twister-out*' -not -path './build_artifacts/*' -not -path './.repo/*' -prune -exec rm -rf {} +`. Compare the generated header's mtime against `syscalls.json` to confirm |
| **An old external-FW extract sits next to the pinned one.** `build_artifacts/opt/atl/fw_external/d2d/phy/` held `eland_pshaper_cal_1009_*` (old) and `eland_regression_1021_*` (pinned) | `<command-line>: error: "D2D_PHY_VER_STR" redefined [-Werror]` in pwr_thrm, d2d and pcie phy. MNG then fails across every board, which looks like a code break | Make sure each `fw_external/<ip>/phy/` holds **only** the version the manifest pins. Delete the others, or run `octl ws clean` |
| **A single-board failure hides behind a whole-app failure.** The ITCM overflow was quad-die only, but the D2D/syscall pollution failed all 28 MNG ARM/host boards | Every board fails, so you can't tell whether the change under test helped | First get a pristine tree in which the other boards pass. Only then read the board you care about |
| **ITCM headroom is in the per-board log, not the summary** | — | `grep -E '^\s*(ITCM\|DTCM):' zephyr_apps/mng/build_logs/<NN>_arm_<board>_*.log`. On 2026-10-26, with #441, `palladium_aw_quad_die` used 252,992 B of 256 KB ITCM (96.5%, about 3 KB free) |
| **A PR branch is older than the slot base.** MNG #441 was 14 commits behind `a1_power_on_branch` and lacked the IPSESW-392 per-die SE board nodes that the rebased SE needed | Building the PR branch raw would test an MNG that doesn't match the SE/EP under test | Cherry-pick the PR's commit(s) onto the current base on a scratch branch (`git checkout -B … <base-sha>; git cherry-pick <pr-sha>`), and report it as "PR + current base", not "PR as-is" |

Pristine full-build order for a reused slot:

```bash
mctl sh 'octl ws clean'
mctl sh "find . -maxdepth 5 -type d -name 'twister-out*' -not -path './build_artifacts/*' -not -path './.repo/*' -prune -exec rm -rf {} +"
mctl sh 'ls build_artifacts/opt/atl/fw_external/*/phy/ 2>/dev/null'   # exactly one pinned version each
mctl build
```

## Additional Notes

- **is_test=false** should be passed explicitly when building a non-test variant to avoid ambiguity when the same variant exists in both test and non-test forms (e.g. primeboot `host/sim`).
- **Commented-out board configs** (lines starting with `#`) are not supported — do not offer them as options.
- If the user says "build all" or gives no filter, omit `--args` entirely to let the build system build all variants.
- The workspace root must be passed as the CWD when running `octl ws build` — always `cd <ws_root>` first.
- **Before an individual proj_infra build**, ensure `ip/d2d` and `ip/pcie/phy` have been built at least once (see "proj_infra (Post-Build) Dependencies"). A full `octl ws build` covers this automatically.
- **If a proj_infra build fails after a recent sync**, clean and sync the workspace first — stale post-build scripts cause the failure.
- **Full workspace builds are slow locally** — offer to delegate to the VM mirror workspace (see "Delegating a Full Workspace Build to the VM"). Always prompt the user before delegating.
- **Never claim "main is broken" from a single-project build.** Reproduce on a pristine all-`main` workspace with a full `octl ws build` first (see "Prove a failure is pre-existing" and "Known false failures").
