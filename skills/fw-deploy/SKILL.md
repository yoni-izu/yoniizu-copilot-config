---
name: fw-deploy
description: "Deploy verified firmware build artifacts to the user's shared NFS home on lab machines. Use when: deploy firmware to NFS, copy a flash image to machines, publish build outputs to a shared lab directory, transfer an ASIC image from a VM, write deployment change notes, or verify that several machines see the same deployed artifact. Covers mctl target/home discovery, binary-safe VM transfers, NFS-safe rsync, isolated destinations, mandatory baseline-relative CHANGE_NOTES.md files and SHA-256 verification. This is artifact deployment, not source mirroring, building, flashing or resetting devices."
argument-hint: "Source image or build workspace, destination target/NFS directory, and optional machines to verify"
---

# Firmware Artifact Deployment to NFS

Copy an identified build artifact to a shared lab filesystem and prove that the
destination contains the exact source bytes. A shared NFS home usually needs
one copy, followed by verification from the relevant machines.

## Scope and Related Skills

- Use this skill for copying files to NFS, not programming hardware.
- Use [vm-delegate](../vm-delegate/SKILL.md) for target discovery, mirror workspaces,
  remote builds and result retrieval.
- Use [build-fw](../build-fw/SKILL.md) only when a build is explicitly requested.
- Use [fw-flasher](../fw-flasher/SKILL.md) for image identification or a separate,
  explicitly authorized device-flashing operation.
- Do not mirror source repos, rebuild, publish releases, flash or reset devices
  merely because the user asks to deploy an existing artifact.

## Inputs and Safety Rules

Resolve the source artifact, its project/variant/flavour, destination target and
directory, and the machines on which to verify visibility. Ask only for missing
information that cannot be obtained from configured targets or established paths.

1. Require the actual image file. A successful build exit code alone does not
   prove that packaging ran or that the image is fresh.
2. Check file size, timestamp, build/version provenance when available, and SHA-256.
   Do not silently use a downloaded baseline or an older image from another run.
3. Copy the requested artifact and its mandatory `CHANGE_NOTES.md`, plus any
   explicitly requested companion files.
   Do not rsync an entire source workspace or every variant by default.
4. Name every new deployment directory `<date>_<time>_<topic>` using the format
   `DDMMYYYY_HHMMSS_topic` described below. Preserve existing images; never use
   `--delete`, overwrite a known release, or replace an existing different image
   without approval.
5. Do not elevate privileges to work around NFS ownership restrictions.
6. Prefix local terminal commands with `rtk`; commands inside `mctl sh` execute
   remotely and must not assume that `rtk` is installed there.

## 1. Discover Targets and the Shared Destination

Run `rtk proxy mctl targets`. Choose a named target and take its SSH login/host
from the registry, not from a guessed IP. Use `mctl` for remote command execution;
consult `vm-delegate` for machine lifecycle and access handling.

Confirm the remote home, existing firmware directories and filesystem mount:

```bash
rtk proxy mctl -t dsk61 sh ': || cd "$HOME"; printf "HOME=%s\n" "$HOME"; findmnt -T "$HOME" -n -o SOURCE,FSTYPE,TARGET; ls -ld "$HOME/a1_power_on_images"' "$ws"
```

`ws` is the local absolute firmware workspace path. Set it explicitly before
running examples. `mctl sh` prepends `cd <mapped-workspace> &&` to the command.
When that mapped workspace is absent on a destination-only machine, the verified
`: || cd "$HOME"; ...` prefix falls back to its home. The initial missing-directory
warning is expected in this case; still require successful subsequent checks.
Do not create or mirror a whole workspace just to deploy an artifact.

Confirm the destination is on the same NFS export from a second target before
assuming one copy is visible everywhere. If mounts differ, deploy and verify
each destination separately. Do not write to a board's local flash or filesystem.

## 2. Verify and Stage the Source

For a local artifact, use `rtk proxy stat` and `rtk proxy sha256sum` directly.
For a VM artifact, inspect and hash the exact source through `mctl sh`:

```bash
rtk proxy mctl -t vm sh 'stat -c "%s bytes  %y  %n" build_artifacts/opt/atl/device_fw/arm/asic/flash_bf/spi_flash_image_arm_asic.bin; sha256sum build_artifacts/opt/atl/device_fw/arm/asic/flash_bf/spi_flash_image_arm_asic.bin' "$ws"
```

The example is standard ASIC with no revision qualifier. Select the actual
requested project/variant/flavour; do not infer a single-die or other revision.
For image naming and build-ID interpretation, consult the project's `fw-flasher`
reference. Record the expected hash before copying.

### Binary-Safe Retrieval Through mctl

`mctl sh` captures output using shell command substitution. Raw binary output
loses NUL bytes: **never pipe raw tar or image bytes directly through it**.
Use `mctl pull-build` when the full artifact tree is wanted. For a single image,
the verified selective transfer uses a base64-encoded archive:

```bash
rtk proxy mctl -t vm sh 'tar -czf - -C build_artifacts/opt/atl/device_fw/arm/asic/flash_bf spi_flash_image_arm_asic.bin | base64' "$ws" | rtk proxy base64 -d | rtk proxy tar -xzf - -C "$local_stage"
rtk proxy sha256sum "$local_stage/spi_flash_image_arm_asic.bin"
```

Create `local_stage` first using the directory-creation tool. Prefer a new local
staging directory if extraction would replace an existing artifact. Configure
pipeline failure handling when available, and independently require the decoded
file's hash to match the source; a successful final pipeline command is not
sufficient. Any unexpected remote stdout or checksum mismatch is a failed transfer.

## 3. Publish to NFS Without Changing Ownership

### Mandatory Folder Naming

Every **new deployment directory** must be named `<date>_<time>_<topic>`:

- `date`: `DDMMYYYY`.
- `time`: `HHMMSS`, using UTC for both date and time.
- `topic`: a short descriptive, lowercase filesystem-safe name using letters,
  digits, underscores or hyphens; no spaces or path separators.

Example: `04102026_142033_pcie_diag_d3cold`. Generate the timestamp at deployment
time, not from this example or the image's build time. Generate the name once
and reuse it for directory creation, copying, notes and verification. Record
the chosen name and UTC deployment time in `CHANGE_NOTES.md`.

Variant subdirectories such as `asic` may remain below this timestamped
deployment directory. If the name already exists, do not overwrite it without
approval; choose a fresh timestamp instead. Never silently revert to an undated
topic-only folder name.

This rule applies to **new folders**, not retroactive renaming. Preserve existing
deployment paths. When the user explicitly requests an in-place replacement,
reuse that existing directory and update its image and notes; do not invent a
new folder or rename it merely to satisfy this convention.

### Mandatory Change Notes

Every deployment artifact directory must contain `CHANGE_NOTES.md` beside its
image. Create it locally with `apply_patch`, then transfer it with the artifact.
For a directory containing several variant subdirectories, put notes beside
each independently deployable image and describe the corresponding variant.

The notes must describe the cumulative changes relative to the agreed original
baseline, not just the previous deployment. An additional section may explain
the incremental difference from the previous deployment.

Include:

- Baseline branch/build identity and exact baseline commits for changed repos,
   when recoverable. State explicitly if the baseline build ID is unknown.
- Deployment directory name and UTC date/time, project, platform,
   variant/revision/flavour, image filename,
   size, SHA-256, feature branch and built commit IDs for changed repos.
- Functional changes and useful new commands, grounded in the actual commits
   included in this image. Separate formatting from behavior changes.
- Experimental changes, known limitations, and whether behavior is enabled or
   disabled compared with the baseline.
- Build/test and cross-machine checksum verification actually performed. Do not
   present a successful build or NFS copy as hardware qualification.

Do not infer historical source state from today's HEAD or invent provenance for
an older deployment. Consult recorded build/commit information and the actual
deployed image hash. Ask when the intended baseline is ambiguous. Never say a
previous image contains a newer change simply because its branch has advanced.

When adding notes to an existing deployment, leave image bytes untouched. Check
for existing notes first and preserve user-written content; use a focused update
when correcting or extending them. No rebuild or device operation is needed.

Create the isolated destination through `mctl sh`. Use the mandatory timestamped
name, generated once locally. The following uses the verified PCIe transfer
pattern with the naming convention applied:

```bash
deployment="$(rtk proxy date -u +%d%m%Y_%H%M%S)_pcie_diag_d3cold"
rtk proxy mctl -t dsk61 sh ": || cd \"\$HOME\"; mkdir -p \"\$HOME/a1_power_on_images/$deployment/asic\"" "$ws"
rtk proxy rsync -az --no-owner --no-group --checksum --ignore-existing "$local_stage/spi_flash_image_arm_asic.bin" "$local_stage/CHANGE_NOTES.md" "yoni.izutskiver@SA-DSK61-CSR:/users/home/yoni.izutskiver/a1_power_on_images/$deployment/asic/"
```

This SSH login comes from the configured `dsk61` target. Discover it again for
future deployments; it is not a general default for every user or machine.
Use an existing dedicated upload command if one becomes available. Otherwise,
direct `rsync` is the file-upload fallback because `mctl` currently has no upload
subcommand; keep target discovery and remote checks in `mctl`.

`--no-owner --no-group` is required on the shared home: archive-mode rsync can
otherwise fail with `chgrp ... Operation not permitted` and exit 23. Do not use
`sudo`, change the NFS export, or assume that an attribute failure proves the
image contents transferred correctly. Verify the destination bytes.

`--ignore-existing` protects existing files but does not prove they match. If an
existing destination has a different hash, stop and choose a fresh directory or
obtain explicit replacement approval. A successful rsync can also have skipped
the file, so always perform the checks below.

## 4. Verify From Each Relevant Machine

Compare against the recorded source SHA-256 on every requested target, not only
the upload host. Put the expected hash into the check rather than merely printing
a new hash. For example, inside `mctl sh`, use:

```bash
printf '<expected_sha256>  %s\n' "$HOME/a1_power_on_images/<deployment>/asic/spi_flash_image_arm_asic.bin" | sha256sum -c -
```

The command above is a **remote payload**, so it intentionally has no `rtk`
prefix. Invoke it with `rtk proxy mctl -t <target> sh '<payload>' "$ws"`, using
the home fallback from step 1 when needed. Verify size and readability as well.
Also verify that `CHANGE_NOTES.md` is readable and matches the locally prepared
file from each verification target. Success means source, local staged copy and
all destination checks agree, for both the image and its notes.

Do not mark deployment complete if any required target is unreachable, the
file is absent, or a hash differs. Report the exact partial outcome and blocker.
Do not flash or reset devices as a verification shortcut.

## Verified Personal Setup

Verified on 2026-10-04; rediscover before relying on these facts in another setup:

- Targets `dsk61`, `dsk70`, and `nuc74` share `/users/home/yoni.izutskiver`.
- The shared home is NFSv4; the image area is `a1_power_on_images`.
- The PCIe diagnostic candidate was published under
  `a1_power_on_images/pcie_diag_d3cold/asic/spi_flash_image_arm_asic.bin`.
- This is a historical deployment path predating the naming rule above, not a
   template for new deployment folders. Do not rename it automatically.
- All three machines verified the same bytes as the VM source. No devices were
  flashed and existing images were preserved.
- On that workspace, a parallel dependency build returned success without
  packaging the image. A subsequent direct `octl ws build proj_infra` without
  `--parallel` produced it. Always verify packaging output, not just job status;
  do not generalize this observation to every octl version or build setup.

## Final Report

Report the deployed absolute NFS paths for the image and `CHANGE_NOTES.md`,
project/variant/flavour, size, checksum
verification targets, and whether any existing files were replaced. Explicitly
state that deployment copied artifacts only and did not flash/reset hardware.
