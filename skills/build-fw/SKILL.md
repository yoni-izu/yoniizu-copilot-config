---
name: build-fw
description: "Build FW (firmware) Zephyr applications for specific boards/variants/platforms. Use when: user wants to build fw, build primeboot/mng/fla/pwr_thrm/sve app, build for a specific board variant or platform, list supported boards for an app."
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
| primeboot, psoc_primeboot, ewbp      | primeboot      | project.iridium.sw.fw.psoc_primeboot      | zephyr_apps/primeboot/project.iridium.sw.fw.psoc_primeboot.star |
| fla, fla_runtime, ewbf               | fla_runtime    | project.iridium.sw.fw.fla_runtime         | zephyr_apps/fla_runtime/project.iridium.sw.fw.fla_runtime.star  |
| mng, psoc_mng, ewbm                  | mng            | project.iridium.sw.fw.psoc_mng            | zephyr_apps/mng/project.iridium.sw.fw.psoc_mng.star             |
| pwr_thrm, power_thermal, thermal, ewbt | pwr_thrm     | project.iridium.sw.fw.psoc_power_thermal  | zephyr_apps/pwr_thrm/project.iridium.sw.fw.psoc_power_thermal.star |
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

## Additional Notes

- **is_test=false** should be passed explicitly when building a non-test variant to avoid ambiguity when the same variant exists in both test and non-test forms (e.g. primeboot `host/sim`).
- **Commented-out board configs** (lines starting with `#`) are not supported — do not offer them as options.
- If the user says "build all" or gives no filter, omit `--args` entirely to let the build system build all variants.
- The workspace root must be passed as the CWD when running `octl ws build` — always `cd <ws_root>` first.
