---
name: build-fw
description: "Build FW (firmware) Zephyr applications for specific boards/variants/platforms. Use when: user wants to build fw, build primeboot/mng/fla/pwr_thrm/sve app, build for a specific board variant or platform, list supported boards for an app, run a full workspace build, delegate a build to the VM mirror workspace."
argument-hint: "App name and optionally: platform (host|arm), variant (sim|palladium|onix|asic|...), is_test (true|false)"
---

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

## Focused proj_infra Image Builds

`project.iridium.sw.fw.infra` (proj_infra) regenerates the SPI image and other
post-build artifacts. For a flash-image request, do not build every firmware
application individually. Use the dependency-aware proj_infra build:

```bash
cd <ws_root> && octl ws build project.iridium.sw.fw.infra --with-deps --parallel
```

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
| `Failed to generate .../fla_rt_progress_bits.h ... gen_progress_bits.py ... os.remove(output_file) FileNotFoundError` | Race in `infra/scripts/gen_progress_bits.py` — parallel board builds generate the same header into the shared `zephyr_apps/<app>/include/` dir and race on delete | **Flake.** Narrowed by `7b742a89 [SW-0] fla_rt: make progress-bits header gen parallel-safe` but not fully closed. Re-run |
| `<rev> is not a valid revision for <proj>. Accepted revisions: ...` | Branch base predates a `revision.cmake` addition on `main` | **Stale base.** Rebase onto `origin/main` |
| DTS errors in bindings/overlays the branch never touched | Base drift / repo skew between IP bindings and the board DTS provider (`proj_infra`) | **Base incoherence.** Bring all repos to the same `main` |

Three of these produce *different* error text for the *same* underlying cause
(an invalid build), so matching on the error message alone will mislead — always
confirm the build was a clean full build before reading the errors at all.

## Additional Notes

- **is_test=false** should be passed explicitly when building a non-test variant to avoid ambiguity when the same variant exists in both test and non-test forms (e.g. primeboot `host/sim`).
- **Commented-out board configs** (lines starting with `#`) are not supported — do not offer them as options.
- If the user says "build all" or gives no filter, omit `--args` entirely to let the build system build all variants.
- The workspace root must be passed as the CWD when running `octl ws build` — always `cd <ws_root>` first.
- **Before an individual proj_infra build**, ensure `ip/d2d` and `ip/pcie/phy` have been built at least once (see "proj_infra (Post-Build) Dependencies"). A full `octl ws build` covers this automatically.
- **If a proj_infra build fails after a recent sync**, clean and sync the workspace first — stale post-build scripts cause the failure.
- **Full workspace builds are slow locally** — offer to delegate to the VM mirror workspace (see "Delegating a Full Workspace Build to the VM"). Always prompt the user before delegating.
- **Never claim "main is broken" from a single-project build.** Reproduce on a pristine all-`main` workspace with a full `octl ws build` first (see "Prove a failure is pre-existing" and "Known false failures").
