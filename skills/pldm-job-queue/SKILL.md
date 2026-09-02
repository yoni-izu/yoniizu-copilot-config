---
name: pldm-job-queue
description: "Schedule and run hardware tests on Palladium (PLDM) EDK machines via the PLDM Job Queue, to test PRs and newly developed features. Use when: test a PR on PLDM, run a feature on Palladium, octl ws build --remote --target pldm, octl ws build --local --target pldm, remote build pldm, local build pldm, submit/monitor/cancel a pldm job, image type availability, tests-config yaml, artifacts_only_manifest.xml, insert_pldm_job.yaml, remote_build.yaml, PLDM job queue portal, from-file pldm, pldm batch, pldm display name."
argument-hint: "What to test on PLDM (a pushed PR branch → remote mode, or local uncommitted changes → local mode) plus image type / group / timeout"
---

# Palladium (PLDM) Job Queue Runner

Schedule hardware tests on Palladium EDK machines through the **PLDM Job Queue**.
The queue is the sharing mechanism that lets many engineers run tests on a small
pool of PLDM stations without manually grabbing a station. Use it to validate
**PRs** and **newly developed features** on real emulation hardware.

> Source of truth (keep this skill in sync if they change):
> - Confluence BKM: "Working with PLDM Job Queue"
>   (SWENG space, page `1377468525`).
> - Platform Engineering docs (repo `Elmnt-Internal/ip.infra.devops.apps`):
>   `docs/how-to/pldm-job-queue.md`, `docs/how-to/pldm-regression.md`,
>   `docs/workflows/portal-queue.md`, `docs/how-to/pldm-fpga-setup.md`.
> - Upstream skill mirror: `octl/agentfiles/skills/pldm-job-queue/SKILL.md`
>   in the same repo.

## When to Use

- User wants to **test a PR** or a **new feature** on Palladium hardware.
- User runs `octl ws build --remote --target pldm` (build on CI, then enqueue).
- User runs `octl ws build --local --target pldm --from-file <yaml>` (enqueue
  against locally-built artifacts, no remote build).
- User asks about EDK image types, submitting / monitoring / cancelling a PLDM
  job, or reusing a tests-config YAML.

## Pick the Right Mode

| I want to test… | Mode | Command | Workspace clean? | TUI? | Workflow dispatched |
|---|---|---|---|---|---|
| A **PR / pushed branch** | Remote | `octl ws build --remote --target pldm` | **Yes** — all commits pushed | Yes (unless `--from-file`) | `remote_build.yaml` |
| **Local, uncommitted feature changes** | Local | `octl ws build --local --target pldm --from-file <yaml>` | No | No (file required) | `insert_pldm_job.yaml` |

`--remote` and `--local` are mutually exclusive.

- **Remote** runs a full CI build on GitHub Actions from your branch, then
  inserts one PLDM job per image type using the freshly-built artifacts. This is
  the right choice for **testing a PR** — it builds exactly what reviewers see.
- **Local** exports a manifest with your **local HEAD revisions**, uploads it to
  S3 as `artifacts_only_manifest.xml`, and enqueues jobs that consume artifacts
  you already built with `octl ws build`. No remote build runs. Use this for
  fast iteration on **work-in-progress feature changes** you haven't pushed.

### One PR or a set of PRs?

Remote mode builds from your **workspace manifest**, not from a single PR. It
exports a **branch-aware manifest** capturing every repo's current branch/HEAD,
so it naturally supports testing a **set of PRs at once**:

- ✅ **Multiple PRs across different repos** — check out each repo to its PR
  branch, commit and push all of them, then run one remote build. The manifest
  holds one revision per repo, so the whole set is built and tested together.
  This is the cross-repo `DEPENDS_ON` scenario.
- ❌ **Two competing PRs to the *same* repo** — not possible in a single build;
  the manifest can only hold one revision per repo.

Remote mode still requires the workspace to be **clean** (all branches pushed),
so every PR in the set must be pushed before you submit.

## Prerequisites

- `octl` is up to date: `octl update`.
- Connected to **VPN** (or office network) — the on-prem `gatestage` and
  `emul-online` services are required to fetch image availability and enqueue.
- `git config user.name` is set.
- `git config user.email` is a valid **`@elmnt.com`** address. octl validates
  both in remote *and* local mode and exits with a clear error otherwise.
- **Remote mode:** workspace is **clean** — every change committed and every
  commit pushed to origin. octl enforces this.
- **Local mode:** you have already run `octl ws build` locally so artifacts exist
  for the current workspace revisions.

## Workflow: Test a PR (Remote)

1. Check out / have the PR branch locally with all commits **pushed**:
   ```bash
   git status          # must be clean, no unpushed commits
   ```
2. From the workspace dir, dispatch the remote build + queue:
   ```bash
   octl ws build --remote --target pldm
   ```
3. Fill in the interactive TUI (see [Interactive Form](#interactive-form)).
4. octl saves the answers to `remote-build-tests-config.yaml` and prints the
   **GitHub Actions run URL** plus a summary. CI builds your branch; on success
   it inserts one PLDM job per image type.
5. Track progress in the [Portal](#portal--pldm-job-queue-page). You'll get an
   **email** with the workflow link (including execution logs) when the job's
   turn in the queue is reached.

## Workflow: Test Local Feature Changes (Local)

1. Build your feature locally first (over a stable base, e.g. a `regression-ci`
   workspace with your changed repo overridden):
   ```bash
   octl ws build
   ```
2. Create a tests-config YAML (see [YAML Format](#tests-config-yaml-format)) —
   `--from-file` is **required** in local mode; there is no TUI.
3. Enqueue against the local artifacts:
   ```bash
   octl ws build --local --target pldm --from-file tests.yaml
   ```
4. octl uploads `artifacts_only_manifest.xml` to S3, dispatches
   `insert_pldm_job.yaml`, and prints the run URL + S3 location + tests-config
   path. No remote build runs; jobs point at your local artifacts.

## Interactive Form

`octl ws build --remote --target pldm` (without `--from-file`) walks a TUI:

1. **Display name** — optional human-readable label shared by all jobs in this
   batch (e.g. `ippciesw-516-smoke`). Shown in the Portal for tracking.
2. **Number of test entries** — how many PLDM jobs to create from this build.
3. **Per entry** (repeated):
   - **Image type** — pick from the live list. Only use ones annotated
     `(currently available for pldm job queue)` for a prompt start
     (see [Image Type Availability](#image-type-availability)).
   - **Group** — test group filter; `default`, a custom group, or empty = all
     groups.
   - **Timeout** — minutes, `1`–`120` (default `20`).
   - **Extra flags** — extra `octl` flags for this specific test (optional).

Recommended: use a star (`.star`) file function that accepts gtest/pytest
filters and invoke it as a **custom command** with the right filter, rather than
a plain regular test.

## Tests-Config YAML Format

Shared by remote (`--from-file`, optional) and local (`--from-file`, required)
modes. Every interactive remote run also saves this file automatically.

```yaml
display_name: "my-regression-run"   # optional: batch label shown in the Portal
tests:
  - image_type: "1die_w_gmem"       # required: EDK image label
    group: "default"                 # test group filter; "" = all groups
    timeout_minutes: 20              # required: 1–120
    args: "smoke"                    # optional: per-test octl args
    extra_flags: ""                  # optional: extra octl flags for this test
  - image_type: "2die_w_gmem"
    group: ""
    timeout_minutes: 60
    args: ""
    extra_flags: "--show-summary"
```

Validation rules:
- `tests` must contain at least one entry.
- `image_type` is required for every entry.
- `timeout_minutes` must be between `1` and `120`.

A ready-to-copy example (single-test and multi-test batch) lives at
[references/tests.yaml](./references/tests.yaml).

## Reusing a Tests Config

```bash
# First run (remote): save under a descriptive name
octl ws build --remote --target pldm --save-as iridium-smoke.yaml

# Reuse later — remote build + PLDM jobs (skips the TUI)
octl ws build --remote --target pldm --from-file iridium-smoke.yaml

# Or reuse against local build artifacts
octl ws build --local --target pldm --from-file iridium-smoke.yaml
```

Useful for recurring regression batches or sharing a standard config with
teammates.

## All Flags

| Flag | Description |
|------|-------------|
| `--remote` | Dispatch a CI build on GitHub Actions. Requires `--target`. Mutually exclusive with `--local`. |
| `--local` | Skip the remote CI build: export the manifest with local revisions to S3 as `artifacts_only_manifest.xml` and insert a PLDM job. Requires `--target pldm` and `--from-file`. Mutually exclusive with `--remote`. |
| `--target pldm` | Post-build job type: PLDM hardware queue. Required with `--remote` or `--local`. |
| `--from-file <path>` | Path to a tests-config YAML. **Required** with `--local --target pldm`; optional with `--remote --target pldm` (skips the TUI). |
| `--save-as <name>` | Filename for the saved tests-config YAML (only with `--remote --target pldm`; default `remote-build-tests-config.yaml`). |
| `--kernel <version>` | Kernel version for the build (remote mode). |
| `--build-type <type>` | `Release` (default), `Debug`, or `Sanitizer` (remote mode). |
| `--extra-flags <flags>` | Extra `octl` flags for the **remote build step** only. Per-test flags belong in the tests-config YAML, not here. |

## Image Type Availability

Image types are fetched live from `gatestage` (which proxies the `emul-online`
hardware status service). In the picker each is annotated:

| Label | Meaning |
|-------|---------|
| `(currently available for pldm job queue)` | ≥1 EDK loaded with this image is free and not in debug mode. Job will likely start promptly. |
| `(currently unavailable)` | No EDK is currently free for this image. You can still submit — it queues and starts when an EDK frees up. |

> Currently ~1 station/day is reserved for the job queue with a **1-die w/ GMEM**
> image. If your job seems to never take a queue position, the most common cause
> is choosing an **image type that isn't the one currently loaded** on a job-queue
> station.

## Submitting from the Team Portal

The [`task-manager`](../task-manager/SKILL.md) portal can drive this whole flow from a button.
Each feature card in the **PRs** section has **▶ PLDM test**, which reserves a free workspace
slot, checks it out to the feature's PR branches, verifies everything is committed and pushed,
writes a tests-config to `~/tasks/pldm/<display-name>.yaml`, runs
`octl ws build --remote --target pldm --from-file …`, and records the run URL on the
[`pr-monitor`](../pr-monitor/SKILL.md) memoir. It refuses rather than disturbing an occupied
slot or a dirty repo, and releases the slot when the job ends. Only the number-named slots
(`one`…`six`) are offered — the other directories under the project root are scratch dirs, not
buildable checkouts.

Use it for the common case — "test this feature set as a batch". Drop to the CLI for local
mode, multi-entry batches, custom `extra_flags`, or anything needing the TUI's live image
availability list.

## Monitoring the Job

### GitHub Actions run
octl prints the run URL after dispatch (remote → `remote_build.yaml`; local →
`insert_pldm_job.yaml`). If it can't resolve the URL within ~15s it prints a
`gh run list` command to find it. All runs:
https://github.com/Elmnt-Internal/ip.infra.devops.workflow-entry/actions

### Portal — PLDM Job Queue page
**Portal:** https://retool.elmnt.local/app/portal/PLDM_job_q

| Column | Description |
|--------|-------------|
| **Display Name** | Batch label (shared across all jobs from one build) |
| **Image Type** | EDK image label for this job |
| **Status** | `queued` → `running` → `completed` / `failed` / `cancelled` |
| **Assigned EDK** | Palladium EDK hostname assigned when the job goes `running` |
| **Created At** | When the job was inserted |

Filter by **status** or **image type**. Terminal-status rows auto-clean after
~30 days.

## Canceling a Job

From the Portal PLDM Job Queue page: find the job → click **Cancel**.
- **Who can cancel:** the job owner (submitter) or a member of `sw-maintainers-SG`.
- Cancelling a **running** job releases the assigned EDK back to the pool for the
  next queued job.

## Troubleshooting

- **`git user.name is not set` / `git user.email is not set`** — set with
  `git config --global user.name <name>` and
  `git config --global user.email <you>@elmnt.com`. Required by both modes.
- **`git user.email "…" is not a valid @elmnt.com address`** — the validator only
  accepts `@elmnt.com`. Fix your git config.
- **`--local --target pldm requires --from-file`** — local mode has no TUI; pass
  a YAML tests-config.
- **`--local only supports --target pldm`** — local mode is PLDM-only.
- **Workspace not clean (remote mode)** — commit and push, or use `--local` to
  test local revisions.
- **Job never takes a queue position** — the chosen image type isn't the one
  currently available on a job-queue station. Pick an
  `(currently available for pldm job queue)` image.
- **Job fails on missing `pldm_setup` command** — you're missing the artifact
  `module-shared-emulation-utils-ci`.

## Related

- **PLDM Regression** (nightly suite / add a manifest to `regression.yaml` in
  `ip.infra.sw.manifests`): trigger via the
  [regression workflow](https://github.com/Elmnt-Internal/ip.infra.sw.manifests/actions/workflows/regression.yaml).
- **Physically operating a station** (VNC/EDK connect, `pd_load`, waves, UART/JTAG):
  use the sibling `pldm` skill.
- **Portal queue internals / run types** (`job-queue` run type → KubeVirt VMs via
  Kueue gang scheduling, vs `normal_ci`): `docs/workflows/portal-queue.md`.
