---
name: fw-deploy
description: "Store firmware images locally on WSL first, then copy them to any requested filesystem directory or machine. Use when: deploy firmware, copy an image from a VM to local storage, copy an image to a machine, maintain an explicitly requested NFS mirror, list or verify saved flash images, or write deployment change notes. Uses the fw-deploy CLI, ~/flash_images/<project>/DDMMYYYY_HHMMSS_topic/, unchanged image filenames, mandatory baseline-relative CHANGE_NOTES.md and SHA-256 verification. Does not build, flash or reset hardware."
argument-hint: "Source image, project/topic/platform/variant, and optional destination or explicit NFS mirror"
---

> **Portal handoff:** For Team Portal/dashboard work, load
> [portal-maintainer](../portal-maintainer/SKILL.md). Domain records remain
> owned by their skills.

# Local-First Firmware Image Deployment

Always save the identified image on WSL first:

```text
~/flash_images/<project>/DDMMYYYY_HHMMSS_topic/[variant/]original_image_filename
```

Keep the original image filename and UTC timestamp/topic naming convention.
Use any actual project, not a hardcoded A1/power-on destination. An NFS mirror
is optional and **only runs when explicitly requested**, not after every import.
Remote copies may go to any requested ordinary filesystem directory, on NFS
or machine-local storage. Never write a device node or program hardware.

## CLI First

Use `rtk proxy fw-deploy ...` rather than reconstructing transfer pipelines.
The installed launcher is `~/bin/fw-deploy`; implementation:
[scripts/fw_deploy.py](./scripts/fw_deploy.py).
Without the launcher, run `rtk proxy python3 <skill-directory>/scripts/fw_deploy.py`.
Only standard-library Python, `mctl`, SSH, rsync and (for mirrors) `findmnt` are used.

| Action | Command |
|---|---|
| Save a local image on WSL | `fw-deploy import IMAGE --project PROJECT --topic TOPIC --platform PLATFORM --notes NOTES [--variant VARIANT]` |
| Retrieve one VM/machine image to WSL | `fw-deploy pull IMAGE --target TARGET --workspace LOCAL_WS --project PROJECT --topic TOPIC --platform PLATFORM --notes NOTES [--variant VARIANT]` |
| List and validate local images | `fw-deploy list [--project PROJECT]` |
| Machine-readable listing (Team Portal Flash FW) | `fw-deploy list --json` → `{root, images:[{directory, valid, project, deployment, platform, variant, image, path, size, sha256, created_utc, notes, error?}]}`, newest first |
| Verify saved image, notes and metadata | `fw-deploy verify IMAGE_DIR` |
| Copy to any local/mounted directory | `fw-deploy copy IMAGE_DIR --destination /absolute/directory` |
| Copy to a registered machine | `fw-deploy copy IMAGE_DIR --target TARGET --workspace LOCAL_WS --destination /absolute/directory` |
| Maintain an explicitly requested NFS mirror | `fw-deploy mirror IMAGE_DIR --nfs-root /absolute/nfs/root [--target TARGET --workspace LOCAL_WS]` |
| Verify visibility from another machine | `fw-deploy verify IMAGE_DIR --target TARGET --workspace LOCAL_WS --destination /absolute/directory` |

Prefix all local terminal commands with `rtk proxy`. `--root PATH`, before the
subcommand, overrides the canonical local root when explicitly needed.
`IMAGE_DIR` is the leaf directory containing the image, `CHANGE_NOTES.md` and
`deployment.json`, including the variant subdirectory if supplied.
Projects, topics and variant components use lowercase letters/digits/`_`/`-`.
Use an exact variant/revision/flavour label; do not infer single-die or revision.

## Procedure

1. Resolve the actual source image, project, platform, variant/revision/flavour,
   original baseline and requested destinations. Ask only for unavailable facts.
   Do not build, mirror source workspaces, flash/reset, or publish releases.
2. Inspect source size, timestamp, build/version identity and recorded build
   commits. Successful build status alone is not proof of fresh packaging.
   Do not substitute an older or downloaded baseline image.
3. Prepare baseline-relative notes locally with `apply_patch`; pass `--notes`.
   Import or pull to WSL. The CLI generates the UTC directory name once,
   validates source/local SHA-256 and size, rejects empty images and publishes
   the local bundle only after validation. A failed transfer does not publish
   a final deployment directory. Keep the printed leaf path for later commands.
4. Copy to the requested machine/directory, or explicitly mirror to NFS.
   `copy --destination` is the exact artifact directory; `mirror --nfs-root`
   appends `<project>/<same-deployment-name>/[variant/]`, retaining the local
   hierarchy. Mirrors require an actual `nfs`/`nfs4` mount; ordinary copies do not.
5. Verify the destination from every requested machine, using the local bundle's
   expected checksums. Do not infer cross-machine NFS visibility merely because
   the home paths match. Verify both image and notes; metadata is also copied
   and checked. Report unreachable destinations and pending mirrors explicitly.

### Transfer and overwrite safety

- Each copy sends only the image, notes and `deployment.json`, not a workspace,
  every variant, or unrelated files. No automatic mirror, machine wake, rebuild,
  hardware operation, privilege escalation, `--delete`, or release replacement.
- Existing identical files are accepted and reverified; any different file or
  symlink fails visibly. New imports refuse timestamp collisions. Existing
  deployment paths are never renamed or migrated automatically.
- The CLI deliberately has no force-overwrite option. If an in-place replacement
  is explicitly requested, agree on the exact image/notes changes and a separate
  guarded replacement plan before touching existing files.
- Remote retrieval uses base64, never raw binary through `mctl sh`. Remote
  metadata is captured before transfer and compared with the decoded local file.
- Remote commands use `mctl`, including target-to-SSH destination discovery.
  Upload uses rsync with `--no-owner --no-group --ignore-existing` and checks
  all destination bytes afterward. Never interpret rsync success as verification.
- Remote operations require `--workspace` (a local `.repo` workspace) because
  `mctl sh` maps and guards it. All existing workspace-purpose restrictions
  apply; do not bypass a denied operation. Destination-only machines use the
  home fallback if their mapped workspace is absent; source retrieval requires
  that workspace, or the source must be retrieved through an approved alternative.
  Do not create/mirror a source checkout solely to deploy an image.
- For remote destinations use absolute paths or quoted `~/...` (expanded on
  the target). No local `rtk` prefix is injected into remote payloads.

## Mandatory Change Notes and Provenance

Every image has `CHANGE_NOTES.md` beside it. Preserve user-written content.
The supplied notes must describe **cumulative changes from the agreed original
baseline**, not only the previous deployment:

- Baseline branch/build identity and exact baseline commits for changed repos,
  when recoverable; explicitly state when the baseline build ID is unknown.
- Feature branches and the commits actually built, grounded in build records,
  not today's HEAD. Never infer an old image's contents from an advanced branch.
- Functional changes and useful commands; separate formatting-only changes.
- Experimental behavior, limitations and enabled/disabled differences.
- Builds/tests actually performed, and any verification actually completed.
  Artifact transfer is not hardware qualification.

The CLI prepends deployment identity (directory, UTC time, project, platform,
variant, original filename, size, SHA-256, source mtime) and local verification
status. It preserves the supplied notes after that header. `deployment.json`
records image identity and the notes checksum for future copy/verify operations;
it is an integrity manifest, not proof of build provenance or qualification.
Do not claim remote verification in notes before it has happened.

For notes-only corrections, leave image bytes untouched. Read existing notes
first, preserve their content and refresh the manifest's `notes` identity
(`size`, `sha256`, `mtime_ns`) after an approved focused edit. Re-copying to an
existing destination with old notes intentionally conflicts; agree on a
notes-only replacement before updating it. Do not rebuild.

## Related Tools and Private Setup

- [vm-delegate](../vm-delegate/SKILL.md): configured targets, access failures,
  remote workspaces and builds. Do not change VM lifecycle during a copy.
- [build-fw](../build-fw/SKILL.md): only when a build is explicitly requested.
- [fw-flasher](../fw-flasher/SKILL.md): image-ID interpretation or a separately
  authorized flashing operation, never implicit verification by flashing.

Target names, NFS roots and historical results belong outside tracked skill
files, e.g. `~/.copilot-private/skills/fw-deploy/references/personal-setup.md`.
Discover current targets via `rtk proxy mctl targets`; do not guess hosts or
reuse a historical power-on path as the new default. No NFS root is hardcoded.

## Validation and Final Report

Run isolated tool tests with:

```bash
rtk proxy python3 -m unittest discover -s ~/.copilot/skills/fw-deploy/tests -v
```

Report absolute local image/notes paths and requested destination paths,
project/platform/variant, size, SHA-256, verification targets and pending failures.
State whether existing files were replaced and explicitly state that only
artifacts were copied, with no flash/reset or hardware qualification.
