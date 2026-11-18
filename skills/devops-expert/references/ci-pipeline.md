# CI Pipeline (GitHub Actions): architecture and day-to-day use

This file is a reference for the Element Labs CI. Every component repo calls the central reusable workflows in `Elmnt-Internal/ip.infra.devops.workflow`, which are driven by:
- manifests and `config.yaml` (CI config v2) in `Elmnt-Internal/ip.infra.sw.manifests`;
- Portal/gatestage-queued "sensitive" workflows in `Elmnt-Internal/ip.infra.devops.workflow-entry`.

The flow runs L0 (PR) → L1 (merge queue, which promotes artifacts staging→production) → integration → nightly update-manifest → CD.
Source of truth is the Platform docs site (https://elmnt-internal.github.io/ip.infra.devops.apps/), mirrored locally at `~/.cache/devops-expert/apps/docs/`.
FW-specific suites and starfile test dispatch are not covered here. They live in the fw-soc `fw-ci-knowledge` skill.

## Table of contents
1. [Workflow topology and repos](#1-workflow-topology-and-repos)
2. [Triggers: L0, L1, `/ci`, manual dispatch](#2-triggers-l0-l1-ci-manual-dispatch)
3. [PR → delivery journey and "where is my PR"](#3-pr--delivery-journey-and-where-is-my-pr)
4. [config.yaml (CI config v2)](#4-configyaml-ci-config-v2)
5. [Runner labels, pools, kernels, dispatcher](#5-runner-labels-pools-kernels-dispatcher)
6. [Multi-host tests, dynamic test config, multiple CSR tags](#6-multi-host-tests-dynamic-test-config-multiple-csr-tags)
7. [Manifests](#7-manifests)
8. [Artifacts and propagation](#8-artifacts-and-propagation)
9. [DEPENDS_ON (cross-repo)](#9-depends_on-cross-repo)
10. [API compatibility](#10-api-compatibility)
11. [Integration, update-manifest, mirror, feature branches](#11-integration-update-manifest-mirror-feature-branches)
12. [Regression and cron workflows](#12-regression-and-cron-workflows)
13. [Merge queue, rulesets, PR validator, CODEOWNERS](#13-merge-queue-rulesets-pr-validator-codeowners)
14. [Starfiles and onboarding a project](#14-starfiles-and-onboarding-a-project)
15. [Debugging: Sherlock, logs, metrics, local repro, ci-data](#15-debugging-sherlock-logs-metrics-local-repro-ci-data)
16. [Caches, specs, misc how-tos](#16-caches-specs-misc-how-tos)
17. [Troubleshooting / known failure signatures](#17-troubleshooting--known-failure-signatures)
18. [Pitfalls](#18-pitfalls)
19. [Doc contradictions / unclear points](#19-doc-contradictions--unclear-points)
20. [Owner / escalation](#20-owner--escalation)

---

## 1. Workflow topology and repos

| Repo | Role | Key workflow files (verified via `gh api`) |
|--|--|--|
| `ip.infra.devops.workflow` | Reusable workflows called by component repos | `ci.yaml` (`workflow_dispatch` + `workflow_call`), `ci_trigger.yaml`, `ci_manifest.yaml` → `ci_build.yaml` / `ci_test.yaml` / `ci_forbidden_words_scan.yaml`, `runner_tests.yaml`, `lint.yaml`, `promote_artifacts.yaml`, `pr-validator.yaml`, `author-committer-validator.yaml`, `forbidden-words-scanner.yaml`, `forbidden-words-scan-all.yaml`, `depends_on.yaml`, `depends_on_leg.yaml`, `integration_manifest.yaml`, `integration_leg.yaml`, `update_manifest_single.yaml`, `manifests_mirror.yaml`, `multi_branch.yaml`, `regression_generate.yaml` / `regression_launcher.yaml` / `regression_exec.yaml`, `validate_manifests.yaml`, `security_scan.yaml`, `starfile_lint.yaml` |
| `ip.infra.devops.workflow-entry` | Privileged/queued entry workflows, dispatched by Portal/gatestage | `sensitive_workflows.yaml` (actions `L1`, `depends_on`, `update_single_manifest`, `update_manifests`), `manifests_integration.yaml`, `mirror_manifests.yaml`, `remote_build.yaml`, `cd_external.yaml`, `cd_internal.yaml`, `post_release.yaml`, `pldm_job_queue.yaml`, `stale_cleanup.yaml` |
| `ip.infra.sw.manifests` | Manifests and per-revision `config.yaml`, `cd-config.yaml`, `regression.yaml` | `update_manifests.yaml` (cron `0 22 * * *`), `multi_branch.yaml`, `cronwf.yaml` |
| Component repo | Thin callers synced from `TEMPLATE_GITHUB_REPO` | `.github/workflows/ci.yaml`, the CI-trigger caller, `pr-validator.yml` |

Job graph of the central `ci.yaml`:
1. `ci_start` resolves the PR, labels and `MAY_HAVE_PR`.
2. `generate_ci_information` runs `github-runner ci-info`, `changed_files`, `depends-on` / `depends-on-l0` and `scan-info`.
3. `lint` → `lint_done`.
4. `ci` calls `ci_manifest.yaml` once per manifest; that runs `ci_build` then `ci_test` (`runner_tests.yaml`) plus `forbidden_words_scan`.
5. `scan` and `promote_artifacts` run (promotion is L1 only).
6. `ci_done` is the aggregate check.

`ci_done` and `lint_done` are the usual required checks.

AWS auth uses `reusable_steps/aws_auth`, a wrapper around `Elmnt-Internal/configure-aws-credentials`.
- Only role `devops-workflow-oidc` gets an 8h session (`role-duration-seconds: 28800`); every other role gets 1h.
- The last assume in a job wins. In the Build job that is `Save ssh key`.
- If you see `ExpiredTokenException`, check which role was assumed last.
- `The requested DurationSeconds exceeds the MaxSessionDuration set for this role` is fixed in `ip.infra.devops.aws.terraform` `modules/iam/oidc-roles/devops-workflow-oidc.tf`.

Source: `workflows/ci-workflow-overview.md`, `agents/workflow-metrics.md`. gh: `ip.infra.devops.workflow/.github/workflows/{ci.yaml,ci_trigger.yaml,ci_manifest.yaml}`, `ip.infra.devops.workflow-entry/.github/workflows/`.

## 2. Triggers: L0, L1, `/ci`, manual dispatch

| Phase | Trigger | What runs | Artifacts |
|--|--|--|--|
| L0 | CI-trigger dispatches the repo's `ci.yaml` (phase L0) | lint/format/static analysis, build, unit tests, test groups of stage `L0` (else `default`) | uploaded to the **staging** S3 bucket, never promoted |
| L1 | `merge_group` (merge queue) → `ci.yaml` phase L1 | full build and test of PR merged with base | on success promoted to the **production** bucket (`s3-github-action-package`), and gatestage triggers integration. On failure the PR is removed from the queue |
| Manual | Actions → **CI** → **Run workflow** | `phase` (default L0), `manifest_url`, `target_branch`, `source_ref` (branch name like `dev/x`, not `refs/heads/...`), `depends_on` | creates a new artifact from `main` with no code change. The version is shown in the run Summary |

CI-trigger rules (`ci_trigger.yaml`, a reusable workflow; the caller must grant `id-token: write` and `actions: write`):
- `pull_request_review: submitted` fires only when the PR `reviewDecision == "APPROVED"`.
- `issue_comment: created` fires only for a comment that is exactly `/ci`. Other comments are ignored, and the body is not parsed for options.
- Dedup: the trigger searches the last 50 `ci.yaml` runs for a run title containing the HEAD SHA. If one exists, `/ci` or a new approval does **not** start another run. Use Actions → CI → **Re-run failed jobs** instead.
- Concurrency is keyed on PR number (or `source_ref`) plus `depends_on`. A newer run cancels the older one.
- `no-fail-fast` label:
  - applies to L0 of PRs only, and must be present before approval or `/ci`;
  - if added mid-run it takes effect from the next commit;
  - the merge queue always fails fast.
- A PR created before `DEPENDS_ON:` was added to its description needs a re-trigger.
- `merge_group` + L1 + DEPENDS_ON is rejected. L1 for a depends-on set must go through the Portal (see §9).

Source: `workflows/ci-workflow-overview.md`, `how-to/trigger-l0-l1-manually.md`, `how-to/run-l0-l1-on-demand-workflow-dispatch.md`, `how-to/create-new-artifact-for-manifest.md`, `workflows/faq.md`. gh: `ci_trigger.yaml`, `ci.yaml`.

## 3. PR → delivery journey and "where is my PR"

```
PR approved/ci → L0 → merge queue L1 → promote staging→production
  → gatestage sees new version (≤30 s) → integration (integration-ci.xml)
  → nightly update-manifest 22:00 UTC (consumers with strategy integration)
  → CD (internal/external) on its own schedule
```
Propagation is multi-level: a consumer of a consumer updates on the **next** nightly cycle, which can take 2–3 days end to end.

Trace a PR:
```bash
gh pr view <PR-URL>                     # state OPEN until merged; queue position only via --web
gh pr checks <PR-URL> --required        # L0
octl artifacts versions --pr <PR-URL> -p <project> -m <manifest> --top 5 -o json \
  | jq -c '.[] | {Version, IntegrationStatus, CDManifests}'
octl artifacts versions --pr <PR-URL> -p <project> -m <manifest> --reverse --top 1   # earliest version
```

| Result | Meaning |
|--|--|
| no lines | L1 has not promoted a version containing the PR. **An empty result is definitive.** |
| a line | L1 done; that version contains the PR |
| `IntegrationStatus` `Passed` (values: Pending/Testing/Passed/Failed) | integration-validated; ready for update-manifest and CD |
| a CD manifest `true` | that CD manifest delivers it. CD status reflects only the **current** pins |

Matching rule: a version contains the PR if its pinned commit descends from the PR merge commit. The S3 `pr_number` tag is only a fast path.
Reverts are not detected. Check with `gh search prs --repo <o>/<r> --merged Revert "#<n>"`.

Source: `workflows/pr-to-delivery-journey.md`, `how-to/where-is-my-pr.md`, `workflows/artifact-propagation.md`.

## 4. config.yaml (CI config v2)

Location: `ip.infra.sw.manifests/projects/<project>/<revision>/config.yaml`. Each revision is independent.
- Changes apply on the next run after the PR merges.
- The DevOps codeowner must approve.
- An unknown key is **silently ignored**.

| Top-level key | Notes |
|--|--|
| `org.aws.on_demand` | list of run types: `normal_ci`, `update_manifest`, `depends_on`, `cd`, `regression`, `integration`. Labels for these runs get the `.on-demand` suffix, so a matching on-demand pool must exist or the job stays queued. Schema also lists `org.aws.spot`. |
| `enable_integration` | default `false` per the reference (see §19) |
| `disable_integration_bug_report` | bool |
| `default_csr_tag` | seen in real `projects/iridium/main/config.yaml` (e.g. `a1/3_0_11_3p0_1389`). Feeds CD `proj_tag`. |
| `default:` | required; holds a manifestConfig |
| `manifests: { <name>.xml: {...} }` | per-manifest override. It **must redeclare `stages.default`** (and its own `supported_csr_versions` if used). |
| `lint.required_pr_labels.one_of` | PR-validator label policy (§13) |
| `org.vcs`, `org.cd` | **moved to `cd-config.yaml`** (older docs still show them in config.yaml) |

manifestConfig keys:
- `disable_update_manifest`
- `disable_integration`
- `update_manifest_strategy`: `integration` or `latest` (default `latest`)
- `update_manifest_cron`: minimum 3h interval
- `image`
- `skip_tests`
- `supported_csr_versions: {build: [...], test: [...]}`
- `scans`: present in iridium main
- `stages`

**Stages:** valid names are `default`, `L0`, `L1`, `update_manifest`; `how-to/ci/ci-config.md` also lists `depends_on`. Resolution is "stage or default".
- `default` must exist, contain at least one job, and every build must have at least one label.

**Build fields:**

| Field | Notes |
|--|--|
| `kernel` | `"6.17"`, `"6.12"`, `"wsl6.6"`, `"wsl6.18"`, or `""` (host) |
| `labels` | at least one required |
| `timeout_minutes` | default 180, range 1–1440 |
| `build_type` | `Release` |
| `disable_unit_tests` | |
| `continue_on_error` | |
| `cmd` | replaces `octl workspace build`; disables CSR fan-out |
| `pre_build` / `post_build` | |
| `octl.args` / `octl.extra_flags` | e.g. `--artifacts-filter stage=build` |

**Tests** (each runs `octl workspace test --group <group>`):

| Field | Notes |
|--|--|
| `group` | |
| `labels` | |
| `timeout_minutes` | default 100 in the reference (180 in how-to schema), max 1440 |
| `replica` | 1–6 |
| `flavor` | |
| `cmd` | |
| `pre_test` / `post_test` | |
| `octl.*` | |
| `datacenter.pod_options` | |

**Extra jobs:** `pre_build_job`, `pre_test_job`, `post_build_job`, `post_test_job` each run on their own runner.
- Default timeout is 60.
- Pre jobs accept `continue_on_error`; post jobs accept `skip_on_failure`.

**`test_state`:** a dir at the workspace root (mode 0o777), shared via a GitHub artifact across pre-test → test → post-test and regression setup/teardown.
- Starfiles see it as `context.test.state_dir`.
- Uploads are namespaced per job; file permissions are yours to manage.

**`scan`** (in a job next to `build`): `type: coverity` runs `octl ws sec cov --mode build --submit`.
- `blackduck` fails with `blackduck scan type not yet implemented`.
- A scan-only job (no build) fails on older `github-runner` with `build must have at least one label`.

```yaml
default:
  update_manifest_strategy: integration
  stages:
    default:
      jobs:
        - build: {kernel: "6.17", labels: ["self-hosted","cxi.8xlarge","x64","ubuntu-24-6-17"], timeout_minutes: 60}
          tests:
            - {group: "default", labels: ["self-hosted","cxi.8xlarge","x64","ubuntu-24-6-17"]}
            - {group: "hw-validation", labels: ["self-hosted","hw-lab-setup-a"], timeout_minutes: 120}  # custom lab runner
```

Source: `workflows/ci-config-reference.md`, `how-to/ci/ci-config.md`, `how-to/enable-new-os-kernel-arch.md`, `how-to/pr-validator-required-labels.md`. gh: `ip.infra.sw.manifests/projects/iridium/main/config.yaml` (keys only).

## 5. Runner labels, pools, kernels, dispatcher

Write labels as `[self-hosted, <pool>, x64, <os-label>]`. All pools run Ubuntu 24.04.

| Pool label | Instances | Notes |
|--|--|--|
| `cxi.4xlarge(.on-demand)` | c8i/c7i/c6i.4xlarge | kernel 6.17 or WSL |
| `cxi.8xlarge(.on-demand)` | c7i/c8i.8xlarge | kernel 6.17 or 6.12 |
| `cxi.8xlarge-base` | | base image (`ubuntu-24-6-8`) |
| `cxi.12xlarge` | | |
| `cxi.16xlarge(.on-demand)` | | |
| `mxi.16xlarge(.on-demand)` | m7i/m8i | |
| `mxi.48xlarge(.on-demand)` | m7i/m8i | EFA |
| `mxi.96xlarge` | m8i | EFA |
| `mxg.48xlarge` | m8g | arm64 Graviton4. Labels `[self-hosted, mxg.48xlarge, linux, arm64, ubuntu-24-6-17]` |
| `m8i.48xlarge(.on-demand)` | | only for full RMA on efa-direct |
| `m8id.{8,48,96}xlarge(.on-demand)` | | NVMe |
| `kvm` | c7i.8xlarge | |
| `efa(.on-demand)` | c5n.9xlarge, c6i.32xlarge | |
| `inf2` | | |
| `regular` | t2/m4.2xlarge | |

All pools are spot unless suffixed `.on-demand`. Use on-demand when a spot interruption is unacceptable (e.g. long depends-on builds) or via `org.aws.on_demand`.

| `kernel` | OS label | Where |
|--|--|--|
| `""` | `ubuntu-24` | **not registered on cxi/mxi pools**, so a job requesting it never matches them |
| `"6.12"` | `ubuntu-24-6-12` | cxi.8xlarge |
| `"6.17"` | `ubuntu-24-6-17` | |
| `"wsl6.6"` / `"wsl6.18"` | `ubuntu-24-wsl-6-6` / `ubuntu-24-wsl-6-18` | cxi.4xlarge |

If you omit the OS label, the job runs on any kernel variant of that size.

**Data-center (runner-dispatcher) labels** (`key=value`):
- `dc=ocean|kfs|csr|aws` (selects the backend)
- `resource=vm|pod`
- `flavor=` (required for non-aws; e.g. `a1.small`…`a1.4xlarge`, `dgx.*`)
- `namespace=`
- optional `pod-options=<json>`
- for AWS: `instance=spot|on-demand`, `flavor=<ec2 type>`, `replica=`

Never set `group_uuid`, `group_size`, `workflow_id` or `workflow_attempt` yourself:
- config parse creates the base `group_uuid` and `group_size = replica`;
- `runner_tests.yaml` appends `-<run_id>-<run_attempt>` inline in the Test job `runs-on:`. If that suffix were computed in an upstream job output, a partial re-run would freeze it at a stale attempt.

Other dispatcher rules:
- The router rejects a job unless its flavor exists **and** the flavor's `location == dc`.
- Flavors are defined in `core/infra/config.jsonnet` `supported_flavors`; changing them needs a dispatcher redeploy.
- Put the flavor in a **label**. The `flavor` field defaults to an AWS type (c7i.8xlarge), which `dc=ocean` rejects.
- `replica>1` on a DC test fans out into N jobs and Kueue gang-schedules them. On AWS a single job carries `replica_count` instead.
- An invalid or missing label means the job silently stays **Queued** until GitHub times it out.
- Debug with the Grafana **Job Lookup** dashboard (`job_link` variable).

Dispatcher internals:
- The router (2+ replicas, management cluster) receives `workflow_job` webhooks via platform-webhooks and writes to a shared RDS. It reconciles missed events against GitHub every minute.
- One dispatcher runs per DC. It polls `dc_job_info` every 10 s and creates KubeVirt VMs `runner-<job_id>` (ephemeral, actions/runner v2.335.1, 10 vCPU/38 Gi).
- A completed job's VM is reaped by `reapCompletedJobs`.

**Dynamic AWS labels** (`ghr-ec2-instance-type:<type>`, `ghr-ec2-ebs-volume-size:<GB>`, `ghr-ec2-image-id:<ami>`, custom `ghr-<k>:<v>`) are **Platform-internal**.
- Teams and AI agents must not add them without documented Platform approval.
- They bypass warm pools.
- Pools are defined in `ip.infra.devops.aws.github-runners` `base/templates/runner-configs/<pool>.yaml`.

Source: `workflows/ci-config-reference.md`, `how-to/ci/ci-config.md`, `workflows/runner-dispatch-labels.md`, `architecture/runner-dispatcher-flow.md`, `architecture/runner-dispatcher.md`, `how-to/dynamic-aws-runner-labels.md`.

## 6. Multi-host tests, dynamic test config, multiple CSR tags

**Multi-host:**
- Set `replica: N` (max 6). Each runner gets `RANK_ID` (0..N-1).
- Peer IPs are in `context.build.system_context.github.runners`, or pass them manually with `--additional-context '{"github":{"runners":[...]}}'` (`OCTL_CONTEXT`).
- `wait-for-peers` times out after 300 s with `context deadline exceeded`.
- EFA: labels `[self-hosted, efa, linux, x64, ubuntu-24]`, plus `cluster-placement-group` for same-rack placement.
- `InsufficientInstanceCapacity` is retried automatically: first with on-demand, then the job is re-queued up to 3 times.
- Static `runners_ips` skips SSM peer discovery (temporary mechanism).

**Dynamic test config:** the starfile `tests()` returns `with_config(suites, test_config=[{labels, group, octl/elmcli...}])`.
- It must honour `context.build.kernel`, otherwise no tests or the wrong tests run.
- `octl workspace test --list-configuration --output-config tests.json` evaluates it.
- Cron legs evaluate with `phase=L1` and `run_type="Schedule"`.

**Multiple CSR tags:** set `supported_csr_versions: {build: ["proj/tag",...], test: [...]}`.
- **Build:**
  - With 2+ build tags, the Build job fans out one matrix leg per tag (`fail-fast: false`). Each leg uploads `tag_<project>_<tag>_eot_<ws>.tar.zstd` intermediates to staging.
  - `combine_builds` (`github-runner workspace-combine-upload`) overlays them into one un-prefixed artifact, which is the one promoted. Later tags win on file collisions.
  - With 0–1 tags, or with a custom `build.cmd`, there is no fan-out and no combine.
  - Leftover `tag_*` files are never promoted.
  - Starfiles should iterate `context.build.csr_tags`, which honours a local `--tags`. `context.supported_csr_versions` holds the config default.
- **Test:**
  - Use `octl ws test|exec --tag <PROJECT/TAG>`; starfiles see it as `context.test.csr_tag`.
  - CD uses `elbuilder build --tag`, with `--tag ""` to disable.

Source: `how-to/add-multi-host-tests-ci.md`, `how-to/dynamic-test-configuration.md`, `how-to/build-test-multiple-csr-tags.md`, `workflows/ci-config-reference.md`.

## 7. Manifests

**Repo layout** (`ip.infra.sw.manifests/projects/<project>/<revision>/`):
- `config.yaml` and `cd-config.yaml` (which may be empty);
- `*-ci.xml` component manifests;
- `integration-ci.xml` (artifacts only, generated, **never hand-edit**);
- `*.tmpl` templates and `*.xml.delivery` (generated).

`regression.yaml` sits at the repo root. Revision naming is `main`, `fb-<project>-<ver>`, `rb-<name>`.

```xml
<manifest version="1.0.0-23032025154403" name="cool-app-ci" owner_email="user@elmnt.com">
  <remote name="origin" fetch="ssh://git@github.com/Elmnt-Internal/"/>
  <default remote="origin" revision="main" sync-j="4" image="..."/>
  <project name="ip.infra.sw.fw" path="infra" depends_on="ip.infra.sw.sim"><sparse>dir1/</sparse></project>
  <artifact name="ip-infra-sw-fw-ci" version="1.201.0-21032025-210339" dep_type="build"/>
  <artifact name="drv-ci" version="..." revision="release_1.0" update_manifest_strategy="latest"/>
  <artifact name="ext" dep_type="external" source="s3://s3-sw-external-artifacts/<PROJECT>/x.tar.gz" extract_prefix="tc"/>
  <artifact name="big" version="..."><extract><include>*path1*</include><exclude>*/qual_venv/*</exclude></extract></artifact>
</manifest>
```

**Manifest rules:**
- `owner_email` becomes the Jira assignee for failure tickets.
- When **creating** a manifest, remove `version`; CI initialises it to `1.0.0`. When **editing**, never touch `version`.
- A repo may appear in only one CI manifest per project+revision. Otherwise `github-runner validate-manifests` fails with `appears more than once as a project in manifests with the same revision`. Use `link_to` to share.
- Validation manifests are prefixed `val`.
- Do not depend on artifacts that don't yet exist in that project/revision. S3 is namespaced `<project>/<artifact>/<revision>/<version>/`, so one project cannot consume another project's artifacts.
- New repos need an approved DRR ticket first.

**Artifact rules:**
- `dep_type` values: `build`, `runtime` (the default), `infra` (e.g. `devops-configuration-ci`), `external`.
- `environment="ci,pldm,fpga"` filters downloads, for runtime/external only.
- `revision=` downloads from another revision:
  - update-manifest always takes `latest` for it, and `update_manifest_strategy="integration"` on it is a validation error;
  - depends-on fails if the requested artifact or repos span revisions.
- With integration enabled, no artifact in a regular manifest may exceed its version in `integration-ci.xml`.

**Tools:**
- `octl manifest changes -p <proj> -m <m> -r <rev> --from <v1> --to <v2> [--pr-only|--skip-pr-details]` uses git ancestry via the compare API.
- `octl manifest export [--upload] [-o]` and `octl manifest download -p -m -r --version --bucket s3-collab-artifacts`.
- `octl manifest find`.

Source: `workflows/manifest-details.md`, `workflows/manifests-repo-structure.md`, `workflows/manifest-selection.md`, `how-to/ci/manifest-file.md`, `how-to/create-edit-manifest.md`, `how-to/create-and-use-artifacts.md`, `how-to/view-changes-between-manifests.md`, `how-to/import-export-local-manifest.md`, `how-to/new-project-onboarding.md`.

## 8. Artifacts and propagation

| Stage | Location |
|--|--|
| L0 / per-PR build | staging bucket (`s3-github-action-package-staging`). Never promoted. |
| L1 success | promoted to production `s3://s3-github-action-package/<project>/<artifact>/<revision>/<version>/`. Version format `semver-DDMMYYYY-HHMMSS` in all examples (see §19). |
| Integration manifest | `s3://s3-github-action-package/<project>/integration-ci/<revision>/<version>/manifest.xml` |
| Hotfix share (`octl ws package --all`) | separate short-lived bucket. Consume with `octl ws init -p P -m M --version V --artifacts-only` |
| Local patch | `octl ws upload-patch`, stored in `s3-collab-artifacts` |
| External | upload `.tar.gz` (zstd recommended) to `s3-sw-external-artifacts/<PROJECT>/` with `role-project-<project>`. Extracted to `build_artifacts/opt/elmnt/<extract_prefix>` |

- Use `octl artifacts download` to fetch all artifacts unfiltered.
- `octl artifacts versions -p -m [--verbose --revision --top --bucket --summary]` lists versions with their integration status.

Source: `workflows/artifact-propagation.md`, `how-to/create-and-use-artifacts.md`, `how-to/share-artifacts-with-vlsi.md`, `how-to/build-and-test-remotely.md`, `workflows/faq.md`.

## 9. DEPENDS_ON (cross-repo)

Syntax: a line in the PR description reading `DEPENDS_ON: repo:branch,repo2:branch2,artifact-name:1.5.0-...`.
- Separate entries with commas and **no spaces**.
- PR URLs are accepted (Portal and octl).
- An artifact pin also pulls `integration-ci.xml` into L1.

| Phase | How |
|--|--|
| L0 | add the line to every PR in the set, then re-trigger (`/ci`) if the PR predates the line |
| L1 | **Portal → Depends On** once every PR passes L0 and is approved. Never merge-queue it; the merge queue rejects it (`Dependencies not allowed` / "L1 not allowed to have depends on"). |
| Local | `octl depends-on -p <proj> --deps a:br,https://.../pull/123 --override-path <dir> [--init|--build]` (alias `octl do`) |

Validations:
- all PRs target the same base branch;
- the same `kernel` across manifests (from the `depends_on` stage, else `default`);
- approvals present and not BLOCKED. If the only failed check is `call-ci-workflow / ci_done`, the run continues with a warning. `sw-maintainers-SG` skip the review/blocked checks;
- PRs that are behind base are rebased.

L1 flow (in `workflow-entry` `sensitive_workflows.yaml` action `depends_on`, typically 30–60 min):
1. add blocking rulesets and cancel in-flight L1 runs;
2. prepare manifests and run L1;
3. run a CD-external build check if the integration manifest changes (Portal "Skip cd-external test" skips the test, not the build);
4. promote, admin-merge the manifests and PRs, unblock.

Source: `workflows/depends-on-feature.md`, `how-to/run-depends-on.md`, `how-to/trigger-workflows-from-portal.md`, `how-to/ci/manifest-file.md`, `agents/ci-debugger.md`.

## 10. API compatibility

- Declare API files in the repo's `.github/devops/api_files.yaml` with `build:` and `runtime:` glob lists.
- A PR touching them must carry a DEPENDS_ON naming at least one repo per affected consumer manifest. Otherwise it fails with `Error: missing manifests from depends on`; `integration-ci` is exempt.
- Bypass with label `api/non-breaking`.
- `octl manifest find` locates consumer manifests.

Source: `workflows/api-compatibility.md`.

## 11. Integration, update-manifest, mirror, feature branches

**Integration** (`workflow-entry/manifests_integration.yaml`, queued by gatestage):
- Groups are defined in `ip.infra.devops.apps` `zarf/k8s/eks/gatestage-app/gatestage/cm.yaml`. Auto-integration is limited to `integration.supported_projects`, which is currently `iridium` only.
- Latest group versions are resolved **when the run starts**, not when it was enqueued.
- **On success:** the `integration-ci.xml` PR is admin auto-merged and uploaded to S3.
- **On failure:** a Jira ticket "Integration manifest workflow failed" (listing in-between artifacts) plus email, and propagation is blocked.
- A CD-external build job `cd_external_build` gates `merge_manifest`. Its timeout is `integration_build_check.timeout_minutes` in cd-config (default 60).
- **Why it didn't run:**
  - `enable_integration` is false;
  - it is waiting for group members;
  - one is already running;
  - depends-on has priority;
  - there is no revision dir.

**Update manifest:**
- **Triggers:** nightly 22:00 UTC (`update_manifests.yaml`), or from the Portal (**Update Single Manifest** → `sensitive_workflows.yaml` `update_single_manifest`, sub-workflow `update_manifest_single.yaml`).
- **Flow:**
  1. create merge-block rulesets on all repos of the manifest ("Update manifest is in progress");
  2. cancel in-flight L1 `ci.yaml` runs for that branch;
  3. compute upgrades (strategy `integration` uses `integration-ci.xml`; `latest` uses newest);
  4. run L1 (bisection manifests test one upgrade each);
  5. auto-merge with a version bump.
- **On failure:** Jira to `owner_email`.
- Error `failed to read integration manifest file: no such file or directory` means strategy `integration` is set without an `integration-ci.xml`.

**Mirror** (`workflow-entry/mirror_manifests.yaml`, `github-runner manifests-mirror [--tag --dry-run]`, Portal **Mirror manifests**):
- Copies the S3 artifacts and manifests of the base branch to the target branch and rewrites `default revision` and config.
- Inputs: project, base/target branch, `disable_integration`, `disable_tests`, `disable_unit_tests`, `with_L1`, `vcs_release_version`, `package_delivery`, `source_delivery`, `tag`, optional Manifest Name (branch from that manifest's SHAs) and Manifests tag.
- Commit message: "Mirror manifests for project …", auto-merged.

**Feature branch** (`ip.infra.sw.manifests` `multi_branch.yaml`, "Create new manifest for branch"):
- Only `sw-devops-sg` and `sw-maintainers-SG` may run it.
- Inputs:
  - `project`, `target_branch` → branch `fb-<project>-<target>`;
  - `base_branch`, `manifest` (raw XML, no project revisions), `manifest_name` → `fb-<project>-<target>[-<name>]-ci.xml` at `1.0.0`;
  - `branch_out: true` branches all repos with custom property `sw`; it is mutually exclusive with `manifest`.
- The new branch is added to `merge_queue_branch_ruleset`.
- Add `config.yaml` by hand (FB: `enable_integration: false`, strategy `latest`, CD off).
- To add repos later, re-run with the updated manifest.

Source: `workflows/integration-testing.md`, `workflows/manifest-update.md`, `workflows/manifest-management.md`, `workflows/manifest-mirror.md`, `workflows/feature-branch-manifest.md`, `how-to/trigger-multi-branch-creation.md`, `how-to/trigger-workflows-from-portal.md`.

## 12. Regression and cron workflows

**Regression:**
- Runs nightly at 22:00 UTC, configured by `regression.yaml` at the manifests repo root.
- Fields: `name`, `project`, `revision`, `max_setups`, `timeout_minutes` (default 360, cap 600), `tags`, `octl.extra_flags`.
- Pipeline:
  1. `github-runner regression` writes `regression.json`;
  2. `regression_launcher` fans out a matrix to `regression_exec.yaml` (phase L1, max-parallel 1 per group);
  3. setup/ci/teardown steps all run with `if: always()`.
- Concurrency group is `Regression`, with no cancel.
- Tags are matched to EDK image tags via the emulation status API.
- Manual inputs: manifest, project, tags, runner.

**Cron workflow** (Portal → GitHub Workflow → Cron, or gatestage `POST /api/v1/cronworkflows/create`):
- Stored in Postgres and fired by `pg_cron`. Do **not** add `on: schedule` to repo workflows.
- Each Data block becomes a `cronwf.yaml` matrix leg in `ip.infra.sw.manifests`, calling `ci_manifest.yaml`. Default runner `[self-hosted, cxi.8xlarge, x64, ubuntu-24-6-17]`.
- Fail Fast is workflow-level and defaults to on.
- Tests are "Defined here" or "Dynamic" (`tests: []`, then `--list-configuration`).
- Cron rules: at most hourly; no `@daily`, `TZ=` or Quartz syntax.
- Lifecycle states: `new → idle → processing → failed`.
- Optional notify (start and conclusion emails) and best-effort Jira, which also triggers the AI Investigator (`wf_cron` issue).
- Permissions:
  - create: `sw-platform-sg`, `project-iridium-sw-sg` or `project-iridium-sys-production-sg`, plus the owning team;
  - edit/activate/delete: platform, maintainers or team;
  - policy file: `core/policy/access.rego`.
- Dashboard: `grafana.elmnt.local/d/cron-workflows-sw/cron-workflows`.

**On-demand cronwf** (Portal → Dispatch Workflow → On Demand Cronwf, or `octl wf run cronwf --ci-data`):
- Paste a `ci_data` JSON array.
- Fixed inputs: revision `main`, bucket `s3-github-action-package`, fail-fast true, no Jira.
- The run is named `workflow-dispatch-<user>-<uuid>`.

Source: `workflows/regression.md`, `how-to/create-cron-workflow.md`, `how-to/run-on-demand-cronwf.md`.

## 13. Merge queue, rulesets, PR validator, CODEOWNERS

**Merge queue:**
- Fires `merge_group`.
- Required-check **job names must be identical** for `pull_request` and `merge_group` runs, because GitHub has a single required-check list. Here those checks are `ci_done` and `lint_done`.
- Enable it under Settings → Branches → "Require merge queue".
- Feature branches join via `merge_queue_branch_ruleset`.
- Platform workflows add temporary **rulesets** to block merges during update-manifest and depends-on L1. Rulesets stack and can be org-wide; branch protection is per repo.

**PR validator** (`ip.infra.devops.workflow/.github/workflows/pr-validator.yaml`, caller `pr-validator.yml` from `TEMPLATE_GITHUB_REPO`, synced by `ip.infra.devops.iac` `template-repos-sync.yml`):

| Check | Rule | Bypass / fix |
|--|--|--|
| Required labels | `lint.required_pr_labels.one_of` in the base-branch `config.yaml` of the project whose manifest contains the repo. **Exactly one** label must match, case-insensitive. Enforced today only on `a1_power_on_branch`: `PO-PORT-TO-MAIN` / `PO-ALREADY-ON-MAIN`. | Add exactly one label, then re-run the PR Validator job (it does not refresh on its own). Confirm the log line `Required PR labels defined in <path>`. |
| Author/committer email (`author-committer-validator.yaml`) | every commit's author and committer must match `@elmnt.com`, `@users.noreply.github.com` or `noreply@github.com` | Label `skip-email-check` (Terraform-provisioned; don't create it by hand). A plain re-run does **not** see a new label: push a commit, or toggle draft. Rewrite with `git commit --amend --author=...` and `git push --force-with-lease`. Custom `allowed_email_patterns` input is available. |
| Forbidden words (betterleaks image `general/apps:betterleaks-scanner`) | whole-word, case-insensitive: `lotus`, `neon`, `ocean`, `argon`, `sifive`, `accton`, `elmnt`, `element`, `elemnt`, `elment`, `elementlabs`, `elmcli*`, and `author:` | Optional `.elmnt/betterleaks-local.toml`: CEL `filter` or extra `[[rules]]`. **No `prefilter`** allowed. Anchor paths with `(?:^|/)`. Excluded: `.git/`, `.github/`, `.elmnt/`, `.vscode/`, `CODEOWNERS`, license files, copyright header lines. Local run: `docker run --rm -v "$PWD:/repo" $IMAGE tree|range a..b|stdin`, after assuming `role-project-<p>` (otherwise `AccessDenied`). |
| Org audit | `forbidden-words-scan-all.yaml` (workflow_dispatch; inputs `repo_filter`, `repos_per_job`=15, `max_parallel`=20; ~7 min for ~190 repos) | report-only, never gates |
| Lint | PR title format `type(scope): description` (per ci-debugger) | — |

**CODEOWNERS:**
- Owners must have write access, and team owners must be visible teams.
- CODEOWNERS teams are created and edited only by SW Maintainers SG, via Portal **GitHub Settings** (`portal.ai.elmnt.local/catalog/github-settings`).
- Attach a team to a repo through Jira project `DRR`, issue type "Modify GitHub Codeowners Members".
- PRs choose their target in the "base" dropdown. The base branch selects the manifests and policy.

Source: `how-to/ci/merge-queue.md`, `how-to/ci/rulesets-branch-protection.md`, `how-to/pr-validator-required-labels.md`, `how-to/author-committer-email-validation.md`, `how-to/forbidden-words-scanner.md`, `how-to/run-forbidden-words-scan-all.md`, `how-to/github-codeowners.md`, `how-to/select-base-branch-for-pr.md`, `agents/ci-debugger.md`.

## 14. Starfiles and onboarding a project

**Starfiles:**
- The repo starfile is `<repo-full-name>.star` at the repo root and defines `artifacts()`, `unit_test()`, `build()`, `lint()` and `tests()`.
- The project starfile comes from the `module.devops.configuration` artifact (`devops-configuration-ci`, which must be `dep_type="build"`). It is symlinked as `build_artifacts/opt/elmnt/devops/project.star` on `octl ws init/sync`. To add a project, create `<PROJECT>.star` there.
- `load()` paths resolve relative to the loading file, then the workspace root. Escaping the root fails with `path escapes from parent`.

**Context fields:**
- `context.build.system_context.github.{run_type, changed_files, pr_labels, pr_branch, integration, depends_on.*, runners}`.
  - `pr_labels` and `pr_branch` are filled only for `normal_ci`; use `depends_on.pr_labels` on depends-on L1.
  - A `'` in a label name breaks the JSON quoting.
- `context.test.{phase (l0|l1), group, state_dir}`.
- Only `EL_*` env vars propagate into containers.
- Custom images must derive `FROM` the project base image and set no `ENTRYPOINT`. `OCTL_SKIP_IMAGE_CHECK=1` skips the image check.

**New project checklist** (manual; DEV-698/DEV-685):
1. Entra groups `project-<p>-*-SG`, then `ip.infra.devops.iac` `teams.tf` and repos.
2. AWS `base/iam.tf` `project_roles` and ECR repos.
3. `docker-images/software/<p>/Dockerfile`, `docker-bake.hcl` `PROJECTS`, `determine_build_script.sh` mapping. The test image `<p>/ubuntu:<os>-test` is mandatory.
4. Manifests `projects/<p>/main/{config.yaml,cd-config.yaml,*-ci.xml}` and CODEOWNERS.
5. octl `project_install_dirs` in `core/infra/config.jsonnet`. Without it: `no install dir configured for project`.
6. Gatestage `supported_projects` and Herod `--projects`.
7. Forbidden-word lists.

Verify with `github-runner validate-manifests --manifests-dir . --github-event pull_request`.

**New arch/kernel:** add a build with `continue_on_error: true` and `octl.extra_flags: "--artifacts-filter stage=build"`. Needs DevOps codeowner approval.

Source: `how-to/ci/starfile.md`, `how-to/add-new-project-starfile.md`, `how-to/new-project-onboarding.md`, `how-to/enable-new-os-kernel-arch.md`.

## 15. Debugging: Sherlock, logs, metrics, local repro, ci-data

| Need | Command / place |
|--|--|
| Infra cause of a failed/cancelled job | `octl workflow sherlock <JOB-URL>` (a **job** URL `.../runs/<id>/job/<id>`). Output: `job timed out after 1h30m0s in step "..."`, `instance i-… was killed by spot interruption`, or `could not detect any infra or external reason for the failure`. |
| Runner syslog/elmnt logs | `octl workflow runner-logs --log-type syslog\|elmnt <JOB-URL>` prints a VictoriaLogs link. VMUI `https://vmlogs.elmnt.local/select/vmui/`, e.g. `instance_id:"i-…"` or `namespace:"elmnt"`. |
| Runner metrics | the Grafana link in the Build/Test job annotation (VPN required) |
| Workflow metrics | `github_exporter` → VictoriaMetrics: `workflow_info`, `job_info`, `runner_wait_seconds`, `step_info`, `test_results`. E.g. `count(job_info{workflow_name="ci.yaml",phase="L1",job_name=~"Build .*",conclusion="failure"})` |
| Repro a test job | `octl ws init --github-job-url <JOB-URL>`, then the `octl ws build` from the "Download artifacts" step, then the "test commands" step |
| Repro a build | copy the target and dependency manifests from the `Init` step, run `octl ws init -p P -m m.xml --dir <d>`, then the Build-step command |
| Repro depends-on | `octl do -p P --deps ... --override-path <dir> --init` then `--build` |
| Rerun / modify via CI (no PR) | `octl ci-data download --github-run-url $RUN -o - \| octl wf run on-demand --ci-data -`. Edit with `ci-data select <i>`, `set-manifest --from-file x.xml [--skip-build]`, `set-tests --from-file t.yaml\|--skip`, `set-dependencies`, `set-additional-context`, `validate`. `octl ws generate-ci-data`, `octl ws remote-build [--from-source]`. Add `--wait` to block. |

Source: `how-to/diagnose-failed-workflow-sherlock.md`, `how-to/get-runner-logs-metrics.md`, `how-to/find-and-read-logs.md`, `agents/workflow-metrics.md`, `agents/ci-debugger.md`, `how-to/reproduce-ci-failures-locally.md`, `how-to/build-and-test-remotely.md`, `how-to/use-octl-ci-data.md`. octl: `octl workflow --help`, `octl ci-data --help`.

## 16. Caches, specs, misc how-tos

**Caches:**
- **sccache** (S3-backed, shared):
  - CMake: `CMAKE_{C,CXX}_COMPILER_LAUNCHER=sccache`.
  - Rust: `RUSTC_WRAPPER=/usr/local/bin/sccache`.
- **Bazel remote cache:**
  - Server `https://bazel-cache.elmnt.local/`, backed by S3 `s3-elmnt-build-cache`.
  - Flags: `--remote_cache=… --remote_upload_local_results=true --tls_certificate=/usr/local/share/ca-certificates/elmnt-local-ca.crt`.
  - No cache hits usually means CI is missing `--remote_upload_local_results`, or the Bazel version or flags differ.

**Specs:**
- A VLSI tag in `project.<X>.csr.manifest` triggers header generation (`pt_data.py -pt TOP -hier_mode --hex_offset`).
- To run it manually: Actions → **Create release based on tag**. The tag must match `([\d]+p.*?)_t`; a custom manifest creates a `manual_specs_<name>` branch.
- A `verify_new_specs` failure is a spec YAML problem; route it to the component owner.
- Diagnosis rule: if the value is in TOP, ask the VLSI architect or CAD. If it is not in TOP, or it is a generation/offset mismatch, ask DevOps.

**GitHub known issues:**
- If a required check (`ci_done`, `lint_done`) is stuck "Waiting for status", close and reopen the PR.
- If git operations time out, check githubstatus.com first.

Source: `how-to/configure-build-shared-cache.md`, `how-to/use-bazel-remote-cache.md`, `how-to/diagnose-specs-issues.md`, `how-to/generate-specs-file-manually.md`, `how-to/handle-github-known-issues.md`.

## 17. Troubleshooting / known failure signatures

| Symptom / error text | Cause | Fix |
|--|--|--|
| `/ci` or a new approval does nothing | a run for the HEAD SHA already exists among the last 50 `ci.yaml` runs, or the comment was not exactly `/ci` | Actions → CI → Re-run failed jobs, or push a commit |
| CI never runs on PR | no manifest for the base branch, or the repo is in no manifest of that revision | check `manifest-selection`; add the repo to a manifest |
| `generate_ci_information` fails | manifest not found or DEPENDS_ON syntax error (e.g. spaces) | fix the manifest or DEPENDS_ON line |
| Job stuck **Queued** forever | labels match no pool: bare `ubuntu-24` on cxi/mxi, `.on-demand` added by `org.aws.on_demand` without a pool, bad DC flavor/location, or AWS `flavor` field on `dc=ocean` | fix labels per §5; Grafana Job Lookup |
| Job failed or `cancelled` with no error; log stops | spot interruption or timeout (`The job has exceeded the maximum execution time`) | `octl workflow sherlock <job-url>`; re-run, use `.on-demand`, or raise `timeout_minutes` |
| `ExpiredTokenException: The security token included in the request is expired` | job ran past a 1h role session (the last assume wins) | use `devops-workflow-oidc` (8h) for long steps |
| `The requested DurationSeconds exceeds the MaxSessionDuration set for this role` | role max session < requested | change `devops-workflow-oidc.tf` in `ip.infra.devops.aws.terraform` |
| `Not all PRs target the same base branch` / `not all involved manifests have the same kernel set` | depends-on validation | align base branches / `kernel` |
| `requires code owner approval`, `is blocked from merging (merge state status BLOCKED)` | depends-on approval check | get approvals; maintainers bypass |
| `Dependencies not allowed` / "L1 not allowed to have depends on" | PR with DEPENDS_ON went to the merge queue | use Portal → Depends On |
| `artifact <n> comes from revision <r> while the manifest revision is <r>, depends-on doesn't support artifacts from a different revision` | foreign-revision artifact requested | pin within the same revision |
| `Error: missing manifests from depends on` | API files changed without consumers in DEPENDS_ON | add consumer repos, or label `api/non-breaking` |
| PR can't merge, ruleset "Update manifest is in progress" | update-manifest or depends-on running on that manifest | wait 1–2 h; if >2 h contact Platform |
| `failed to read integration manifest file: no such file or directory` | strategy `integration` but no `integration-ci.xml` | enable integration or use `latest` |
| `build must have at least one label` | scan-only job on old `github-runner`, or a build with no labels | co-locate `scan` with a `build`; add labels |
| `blackduck scan type not yet implemented` | `scan.type: blackduck` | use `coverity` |
| `context deadline exceeded` in `wait-for-peers` | multi-host peers not up within 300 s | retry; check capacity / labels |
| `InsufficientInstanceCapacity` | placement group or spot capacity | auto-retried; drop `cluster-placement-group` |
| `reading module … path escapes from parent` | starfile `load()` outside the workspace | fix the load path |
| `no install dir configured for project` | new project missing in `project_install_dirs` | onboarding step 6 |
| `appears more than once as a project in manifests with the same revision` | duplicate repo in a project+revision | keep it in one manifest; use `link_to` |
| `invalid ci_data: [i]: json: unknown field "…"` / `cannot unmarshal …` | bad hand-edited ci_data | fix, then `octl ci-data validate` |
| `CI data has <N> entries; pick one with octl ci-data select <index>` | multi-entry ci_data | insert `octl ci-data select <i>` |
| `workspace requires all changes to be committed and pushed.` | `generate-ci-data` / `remote-build` with local changes | push, or `remote-build --from-source` |
| Test-only replay rebuilds | `set-manifest` without `--skip-build` clears `skip_build` | add `--skip-build` |
| PR Validator still fails after adding a label | status doesn't refresh; email check ignores re-runs | re-run the PR Validator job (labels); push or toggle draft (email) |
| Required label policy not applied | typo key (`required_labels`, `oneof`) is silently ignored | fix the key; look for `Required PR labels defined in` |
| betterleaks `docker pull` `AccessDenied` | base SSO role has no ECR pull | assume `role-project-<p>` first |
| `could not detect any infra or external reason for the failure` | genuine app/test failure | read the step logs and runner logs |

Source: all sections above; `workflows/faq.md`, `agents/ci-debugger.md`.

## 18. Pitfalls

- `source_ref` takes a branch name, not `refs/heads/...`. "Use workflow from" selects the workflow definition, not your code.
- A `manifests:` override replaces the whole default stages. Redeclare `stages.default` (and `supported_csr_versions`).
- Unknown config keys are ignored silently. Always verify the effect in the run log.
- The test `flavor` field defaults to an AWS type. For DC tests put the flavor in labels.
- Never hand-set `group_uuid`, `group_size`, `workflow_id` or `workflow_attempt`. Never compute the run-identity suffix in an upstream job output, because partial re-runs freeze it.
- `kernel: ""` (`ubuntu-24`) never lands on cxi/mxi pools.
- Merge-queue required checks must have the same job names as the PR checks.
- DEPENDS_ON entries are comma-separated with **no spaces**. The L1 set merges only via the Portal.
- Artifact versions resolve at integration **start**, so a queued run tests the newest versions.
- Do not hand-edit `integration-ci.xml` or any manifest `version`.
- Multi-level consumers update one nightly cycle per level. "Not delivered yet" is often just the schedule.
- A starfile filtering on `run_type == integration` returns nothing for cron (`Schedule`) runs.
- `pr_labels` and `pr_branch` are empty except on `normal_ci`. Avoid `'` in label names.
- `extra_flags` is inserted into the shell unquoted, and is ignored when the test sets `cmd`.
- The `ci_data` artifact of a run expires, after which `ci-data download` fails. Use recent runs.
- Dynamic `ghr-*` labels require Platform approval. AI agents must not suggest them by default.

Source: as cited in §2–§16.

## 19. Doc contradictions / unclear points

1. `enable_integration` default is `false` in `workflows/ci-config-reference.md` and the `how-to/ci/ci-config.md` schema, but `true` in `workflows/manifest-details.md`.
2. Update-manifest keys: `workflows/faq.md` and `workflows/manifest-update.md` show `ci-config.yaml` with nested `update_manifest: {disable, strategy, stage}`. The reference and the real iridium `config.yaml` use flat `disable_update_manifest` / `update_manifest_strategy`. **Flat is current.**
3. Multi-host `labels` vs `flavor`: the reference says labels pick the pool per replica and `flavor` is informational. Its example and the FAQ say labels are "required but ignored" and `flavor` decides the machine.
4. `kernel` values: the build-field table lists `wsl6`, `6.8`; the kernel table lists `6.17`, `6.12`, `wsl6.6`, `wsl6.18`, `""`. `cxi.8xlarge-base` mentions `ubuntu-24-6-8`.
5. `org.vcs`, `org.cd`, `org.aws.region` still appear in config.yaml examples (`manifest-details`, `manifests-repo-structure`, `feature-branch-manifest`, the `how-to/ci/ci-config.md` schema), but the reference says vcs/cd moved to `cd-config.yaml`.
6. Merge blocks on update-manifest failure: they "remain active until resolved" per `manifest-update`/`artifact-propagation`, but the FAQ says they are removed when the workflow ends. Depends-on removes them on failure.
7. Regression status API: `nc_portal.elmnt.local:8888/api/status` (doc text) vs flag default `https://emul-online.elmnt.local/api/status`.
8. Sherlock: the FAQ says `octl sherlock`, which errors with `unknown command "sherlock"`. The correct command is `octl workflow sherlock <job-url>` (verified with `--help`).
9. Version date format: `manifest-details` says `YYYYMMDD`, but every example (integration manifest, `where-is-my-pr`, `manifest changes`) uses `DDMMYYYY-HHMMSS`.
10. Test `timeout_minutes` default: 100 (reference, multi-host how-to) vs 180 (`how-to/ci/ci-config.md` table).
11. Trigger file name: the docs say `ci-trigger.yaml`; the reusable file in `ip.infra.devops.workflow` is `ci_trigger.yaml`.
12. Stage list: `depends_on` is a valid stage in `how-to/ci/ci-config.md` and is used for the kernel check, but the reference lists only default/L0/L1/update_manifest.
13. `scan`: the docs place it per job; the real iridium config has `default.scans`. Treat the schema as in flux.

## 20. Owner / escalation

- All cited docs have front-matter `owner: Elmnt-Internal/sw-platform-sg` (Platform team), `status: current`.
- **Help path** (`how-to/contact-us.md`):
  1. Ask PlatformDevBot first.
  2. Then the Teams channel **Platform - hub**.
  3. Then mention `@Platform-Team`.
- **Requests:**
  - DRR (`elementlabs.atlassian.net/jira/core/projects/DRR`) for service requests: repos, CODEOWNERS, manifests.
  - DEV for features and bugs.
- A merge block lasting more than 2 h, or persistent Queued jobs, means escalate to Platform.
- Portal: `https://portal.ai.elmnt.local/catalog/github-workflow` (VPN required).
- FW test suites, starfile test dispatch for PSoC FW, and FW CI triage belong to the fw-soc `fw-ci-knowledge` skill and the `fw-ci-expert` agent.

Source: `how-to/contact-us.md`, front matter of all cited docs.
