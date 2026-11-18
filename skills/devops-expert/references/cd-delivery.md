# CD & Delivery — Continuous Delivery, packages, signing, releases

Element CD takes validated artifacts, pinned by manifests in `Elmnt-Internal/ip.infra.sw.manifests`, and delivers them in one of two ways:

- **CD internal**: artifact-only promotion to a "stable" manifest, for internal and datacenter use.
- **CD external**: a from-source rebuild with `elbuilder` that produces a docker image and `.deb` packages for customers.

After CD external, **delivery sync** mirrors source, manifest, elbuilder and images into customer GitHub orgs (`aqua-external`, `tahoe-external`, `atlas-external`). `projects/<project>/<revision>/cd-config.yaml` drives all of this. The Platform docs (`ip.infra.devops.apps/docs`) win over Confluence; conflicts are listed in §19.

## Contents
1. Mental model and repos
2. Manifests and naming
3. `cd-config.yaml`
4. CD internal
5. CD external pipeline
6. The cd-external check during integration
7. CD fast lane (docs-only delivery)
8. Delivery sync, stability gate and nightly sync
9. Delivery stubs and FW release assets
10. CD source: editing what is delivered
11. Packages (elbuilder/nfpm, deb/rpm, DKMS)
12. Docker images and delivery to Atlas/Accton
13. Artifact signing with production keys (KMS/KMSS)
14. Release branches, versioning and post-release
15. Customer environments: Aqua and Tahoe
16. Testing CD locally
17. Troubleshooting / failure signatures
18. Pitfalls
19. Contradictions and unclear points
20. Owner / escalation

---

## 1. Mental model and repos

```
PR → L0/L1 (CI artifacts in S3) → integration (integration-ci.xml) ─┬─> CD internal (artifact-only, *-cd.internal.xml)
                                   └─ cd-external build check (gate) └─> CD external (*-cd.external.xml: elbuilder build → docker + .deb)
                                                                           └─> delivery_sync → customer org (staging gate first) → -stable
```

| Repo | Role in CD |
|---|---|
| `ip.infra.devops.workflow-entry` | Dispatch entry points: `cd_external.yaml`, `delivery_sync_external.yaml`, `kms_production_sign.yaml`, the delivery stubs |
| `ip.infra.devops.workflow` | Reusable workflows: `cd_external.yaml`, `cd_build_source.yaml`, `cd_elbuilder_test.yaml`, `cd_package_test.yaml`, `cd_single_machine_build_and_test.yaml`, `infra_ai_build.yaml` |
| `ip.infra.devops.apps` | CLIs: `github-runner` (`cd-external`, `cd-internal`, `code-porting`), `elbuilder`, `octl`; gatestage (Portal backend, crons) |
| `ip.infra.sw.manifests` | Manifests, `cd-config.yaml` and `config.yaml`; also the merge target for resolved manifests |
| `module.devops.configuration` | `packages-definitions/`, `scripts/entrypoint.sh`, `packages-entrypoint.sh`, and the starfile `cd_stable_*` groups (shipped as artifact `devops-configuration-ci`) |

| | CD internal | CD external |
|---|---|---|
| Input | `type="cd-internal"` artifact-only manifest | `type="cd-external"` manifest (projects as source + artifacts as binaries) |
| Upstream | `integration-ci.xml` (or another internal tier) | `upstream_manifest_path`, usually `integration-ci.xml` |
| Builds from source | No. Re-runs CI at L1 on the pinned artifacts | Yes, `elbuilder build` |
| Output | Artifacts promoted in S3 plus the merged stable manifest | ECR docker image, `release-packages.tar.gz` and the merged resolved manifest |
| Trigger | Gatestage cron `cd-internal-<project>-<revision>-<file>` | Per-manifest cron, `workflow_dispatch`, or a manifest PR (`manifests_ci.yaml`) |
| Typical runner | `cxi.8xlarge` | `mxi.48xlarge` (image assembly) |

Source: how-to/cd-at-element-overview.md, workflows/cd-workflow.md, workflows/pr-to-delivery-journey.md, workflows/faq.md

## 2. Manifests and naming

Manifests live in `ip.infra.sw.manifests/projects/<project>/<revision>/`.

| Manifest | Type | Delivered to |
|---|---|---|
| `atlantic-a1-sw-cd.internal.xml` (upstream `integration-ci.xml`) | cd-internal | internal |
| `production-cd.internal.xml` (upstream: the SW internal manifest; chained tier) | cd-internal | internal |
| `atlantic-a1-sw-cd.external.xml`, `atlantic-a1-system-cd.external.xml` | cd-external | `aqua-external`, `elmnt-platform-staging` |
| `tahoe-a1-sw-cd.external.xml` | cd-external | `tahoe-external` |
| `atlas-system-validation-cd.external.xml` | cd-external | `atlas-external` |
| `<product>-docs-cd.external.xml` | cd-external-docs (fast lane) | per `delivery_sync` |

- The customer sees a renamed manifest. For example, `atlantic-a1-sw.xml` exists only in `aqua-external/infra-ai`. Renaming is controlled by `manifest_rename` and `project_rename` (iridium → atlantic).
- An artifact name is not the XML/repo name. For example, repo `module.arch.sw.specs` becomes artifact `module-arch-sw-specs-ci`.
- In an external manifest:
  - `<project>` means the repo is delivered as **source**, optionally filtered with `<sparse>`.
  - `<artifact dep_type="build">` means a **binary** baked into the image, optionally filtered with `<extract><include>/<exclude>`.
- Never hand-edit `version=`, `revision=`, `external_image=` or `proj_tag`. Automation writes them.

Source: how-to/cd-at-element-overview.md, how-to/cd-external/cd-external-user-guide.md, workflows/faq.md

## 3. `cd-config.yaml`

Location: `projects/<p>/<rev>/cd-config.yaml`. Top-level keys are `org`, `cd_external`, `cd_internal`, `cd_package` (legacy) and `delivery_sync`. Every CD manifest needs a matching key; without one the run fails with `manifest <name> not found in cd_external config`.

**`org`**

| Key | Meaning |
|---|---|
| `org.vcs.release_version` | Version used in tags and package versions. Default `v1.0.0` |
| `org.cd.source_delivery`, `org.cd.package_delivery`, `org.cd.external_org` | Delivery switches and target org |

`org.aws` stays in `config.yaml`. Whether `org.cd` lives in `cd-config.yaml` or `config.yaml` is disputed; see §19.

**`cd_external.<file>`**
```yaml
cd_external:
  atlantic-a1-sw-cd.external.xml:
    upstream_manifest_path: integration-ci.xml   # required
    enable: true                                 # required
    schedule: "0 */3 * * *"                      # required; min interval 3h; "0 0 31 2 *" = manual-only
    integration_build_check: {timeout_minutes: 60, test: atl_cd_smoke}   # optional
    docker:                                      # required (CDExternalJob)
      os: linux
      arch: x86_64
      build: {kernel: "6.17", labels: [self-hosted, cxi.8xlarge.on-demand, x64, ubuntu-24-6-17], timeout_minutes: 120}
      tests: [{labels: [...], group: atl_cd_tests, replica: 1, flavor: c7i.8xlarge}]
    packages: {...}                              # optional; omit for a docker-only delivery
    # scan: {type: coverity}
```

| Block | Fields (default) |
|---|---|
| `CDExternalJob` | `os`, `arch` (required), `build`, `tests`. S3 path `<bucket>/<project>/<name>/<revision>/<version>/<os>/<kernel>/<arch>/<build_type>`. `docker` and `packages` are independent; neither inherits from the other. These were previously hardcoded to linux/x86_64/6.8/Release. |
| `Build` | `kernel`, `labels` (required); `build_type` (`Release`); `timeout_minutes` (180, max 1440); `continue_on_error`; `disable_unit_tests`; `pre_build`/`post_build` (shell); `pre_build_job`/`post_build_job`; `octl {args, extra_flags}` |
| `Test` | `labels`; `timeout_minutes` (100); `group`; `replica` (1, max 6); `flavor` (`c7i.8xlarge`); `cmd` (replaces the default test); `pre_test`/`post_test`; `pre_test_job`/`post_test_job` |
| `Scan` | `type: coverity` only, which runs `octl ws sec cov --mode build --submit`. `blackduck` is not implemented |
| `AdditionalJob` | `labels`, `cmd` (required), `timeout` (60). `PreJob.continue_on_error`, `PostJob.skip_on_failure` |
| `cd_internal` | Ordered list of jobs plus `schedule` (see §19 on whether it is required) |
| `cd_package.manifests.<*.cd_package.tmpl>` | Legacy: `{enable, upstream[], jobs (must include build)}` |

**`delivery_sync`**: `project_rename`, then `orgs.<org>.delivery_configs[]` (flat list; no defaults, overrides or templates). Entry fields: `manifest_name`, `manifest_rename`, `docs`, `enable`, `schedule`, `staging{org, overrides, build: bool|{test, package}}`, `sync_repos`, `sync_manifest`, `sync_elbuilder`, `sync_test_report`, `sync_docker_images`, `sync_fw`, `post_build`, `stability_check`, `stability_check_interval_days`, `stability_jira_assignee`, `hidden_projects`.

**Validation errors (verbatim):** `cd_external manifest <name> <block>: <field> must be set` · `delivery_sync org <org> references manifest <name> which is not configured under cd_external` · `... schedule is not a valid cron: <v>` · `... schedule min interval is 3h, user provided lower than that: <v>` · `job must have a build with at least one label` · `test must have at least one label` · `invalid timeout_minutes format, expected uint` · `blackduck scan type not yet implemented, only coverity is currently supported`

Source: workflows/cd-config-reference.md, workflows/faq.md

## 4. CD internal

- **CLI:** `github-runner cd-internal --manifests-repo-path <p> --project <p> --revision <r> --manifest-name <f> --out result.json`
- **Jobs:** `run_cd_internal` → `ci` (`ci_manifest.yaml`, phase L1) → `promote_artifacts` (`aws s3 cp --recursive`, excluding `*/dependencies/*`) → `merge_stable_manifest`.
- **Candidate version:** `1.0.0-<timestamp>`. The `upstream_manifest` and `upstream_version` attributes are recorded but are not used for change detection.
- An artifact missing upstream keeps its old value and logs a warning.
- **Tiers:** any `type="cd-internal"` xml with a config entry becomes a tier. Tiers can chain, e.g. `production-cd.internal.xml` is fed by `atlantic-a1-sw-cd.internal.xml`.
- **Scheduling:** gatestage `GithubManager.Sync` upserts a cron named `cd-internal-<project>-<revision>-<file>`. There is no GitHub `schedule:` trigger. The overview shows a nightly `"0 0 * * *"`.

Source: workflows/cd-internal.md, how-to/cd-internal/cd-internal-guide.md, how-to/cd-at-element-overview.md

## 5. CD external pipeline

**Entry:** `workflow-entry/.github/workflows/cd_external.yaml` (`workflow_dispatch`) calls `workflow/.github/workflows/cd_external.yaml`. It is also triggered from `manifests_ci.yaml` on a manifest PR. Portal queue category is `overlap_only`.

**Inputs:** `project`, `revision`, `manifest_name`, `devops_ref`, `merge_manifest`, `manifests_repo_ref`, `docker_image_url`, `single_machine`, `force`, `blackduck_scanner`.

| # | Job | What it does |
|---|---|---|
| 1 | `build_docker_image_with_upstream_artifacts` (`mxi.48xlarge.on-demand`) | Runs `github-runner cd-external --push --force --out result.json`. Resolves the manifest, downloads ~20 artifacts (~18 GB), generates the Dockerfile and pushes **delivery** `image_tag` = `891377201015.dkr.ecr.eu-central-1.amazonaws.com/<project>/cd/delivery:<manifest>_<release_version>-<DDMMYYYY>-<HHMMSS>`. Outputs `changed`, `docker_ci_info`, `package_ci_info`. |
| 2 | `build_and_package` (`cd_build_source.yaml`) | `elbuilder init` → `sync` → `build` (optional BlackDuck), then pushes **test** image `test_image_tag` (`…-test`). Runs `elbuilder package generate-config --packages-definitions … --out configs.json` and `elbuilder package create --from-file configs.json --target dist/ --archive`. Uploads `release-packages.tar.gz` to `s3://s3-github-action-package-staging/...`. |
| 3 | `cd_docker` matrix (`cd_elbuilder_test.yaml`) | Test manifest `<default image="test_image_tag">`, then `elbuilder test`. With `single_machine: true` it uses `cd_docker_single` (`cd_single_machine_build_and_test.yaml`), where only the `tests[0]` hooks apply. |
| 4 | `cd_package` matrix (`cd_package_test.yaml`) | Installs on the host via `packages-entrypoint.sh` (a local apt repo built with `dpkg-scanpackages`, then `apt-get install atl-sw`, `--no-wa` in CI). Runs `package_install_test`. |
| 5 | `merge_manifest` | `gh pr merge --rebase --admin` into `ip.infra.sw.manifests`, retried 5×. |
| 6 | `promote_packages` | Copies from staging to `s3://s3-github-action-package/{project}/{manifest}/{rev}/{ver}/{os}/{kernel}/{arch}/release-packages.tar.gz`. |
| 7 | `create_jira` | Runs on failure. |

**`github-runner cd-external` flags**

| Flag | Notes |
|---|---|
| `--manifests-repo-path` | Manifests checkout |
| `--project`, `--revision`, `--manifest-name` | Target manifest |
| `--push` | Push the image |
| `--force` | Build even if `changed=false` |
| `--resolve-only` | Resolve the manifest only |
| `--candidate-manifest-file` | Requires `--resolve-only`; used by the integration check |
| `--tag PROJECT/TAG` | Overrides `default_csr_tag` from `config.yaml`; written to the manifest attribute `proj_tag` |
| `--out` | Output file |

Code: `apps/tools/github-runner/internal/app/cd_external.go`.

**Image layout:** artifacts go to `/ws/build_artifacts/` and are hard-linked (`cp -al`) to `/opt/atl/{bin,lib}`. `elbuilder build --tag <P/T>` flattens `{bin,lib}/<project>/<tag>/*` back into `build_artifacts/<arch>/<build_type>/{bin,lib}`. Pass `--tag ""` to disable that flattening.

**Test harness:** `module.devops.configuration/scripts/entrypoint.sh` is the default CMD for every `elbuilder test` mode. It:
1. copies FW `/opt/atl` → `/lib/firmware/atl`;
2. loads `el_pci`, then `el_accel`;
3. waits for `/dev/accel/accel*`;
4. boots `sim_runner` (multi-sled capable).

Sub-commands: `drivers`, `sim`, `perf-sim`, `test <name>`, `list`, `all`. Test groups include `atl_cd_tests` and `atl_gpt_oss_multisled_nightly_tp32_pp4`.

**Starfile reduction:** during `elbuilder build`, every `<ip>.star` is rewritten in place before the build. It keeps only:
- `load()` of `utils.star`;
- `def build` and `def artifacts`;
- private `_defs`;
- private `_X =` assignments (tuple assignments only if every target is private).

Everything else is dropped. The rule is lexical and does no reachability analysis. A public global (`vars = {...}`) used by `build` therefore fails with "not defined" under elbuilder but works under octl. Code: `apps/tools/elbuilder/internal/app/app_workspace_build.go` (`cleanStarfiles`, `filterStmts`).

**Portal "CD External Validation"** (`trigger_cd_external_with_override.yaml`) dry-runs repo:branch overrides against a cd-external manifest. `merge_manifest` is always false.

Source: workflows/cd-external.md, how-to/cd-external/cd-external-pipeline-flow.md, how-to/cd-external/cd-external-user-guide.md, architecture/elbuilder_starfile_reduction.md, how-to/build-test-multiple-csr-tags.md, how-to/trigger-workflows-from-portal.md, workflows/portal-queue.md

## 6. The cd-external check during integration

Every integration run (`manifests_integration.yaml`) adds a **build-only** gate per cd-external manifest of the project:

```
generate_integration_manifest ─┬─ ci (matrix) ─────────────────────────────┐
                               └─ cd_external_discover → cd_external_build ┴─> merge_manifest
cd_external_build:
  extract candidate manifest
  → github-runner cd-external --resolve-only --candidate-manifest-file
  → octl ws init / octl artifacts download (candidate artifacts)
  → elbuilder build --local-artifacts
  → [elbuilder test ... entrypoint.sh test <mark>]  (only if integration_build_check.test is set)
```

- No image is built and nothing is uploaded or promoted.
- If the build or the short test fails, `merge_manifest` is blocked, so `integration-ci.xml` does not advance.
- The time limit is `cd_external.<m>.integration_build_check.timeout_minutes`, default 60. An overrun blocks the merge.
- A project with no cd-external manifests gets `skipped`, and `merge_manifest` accepts `success|skipped`.
- The check runs only when `integration_disabled != 'true'` and `manifest_updated == 'true'`.
- Manual runs can tick **Skip cd-external test** (`skip_cd_external_test`) in the Portal under Advanced settings. That run still builds; only the short test is skipped.

Source: workflows/integration-testing.md, workflows/cd-config-reference.md, how-to/trigger-workflows-from-portal.md

## 7. CD fast lane (docs-only delivery)

- **Manifest:** `<product>-docs-cd.external.xml`, `type="cd-external-docs"`.
- **`<sparse>`:** must be exactly `'documentation/external/'` (anchored), plus optional `'/LICENSE'`. Allowed paths are in `core/repo/sparse_guard.go` (`docsSparseDirs`).
- **Wiring:** only a `delivery_sync` entry with `docs: true` and `sync_repos: true`. There is no `cd_external` entry and no build.
- **Revision:** no commit pinning. It resolves from `<default revision>` at sync time.
- **Version:** stamped `1.0.0-DDMMYYYY-HHMMSS`.
- **Stripped on delivery:** `target_name`, `revision`, `sparse`, `fork_commit`.
- **Sync:** `rsync --delete`, then `--squash --admin` merge. A sparse pattern that matches nothing deletes the customer's docs.
- **Prerequisites:** the destination repo must already exist, and an `--ext-org-token` is needed per org. `docs: true` is not cross-checked against the manifest `type`.

**Errors:** `sparse pattern "external/" is not anchored...` · `...is not allowed; permitted are [documentation/external/] and LICENSE` · `...is an exclusion...` · `...uses a wildcard...` · `<sparse> is empty, which would deliver the whole repository` · `<sparse> must list exactly one of [documentation/external/], found 0` · `Repository does not exist in external organization, create it before merging` · `no client for external org "…" (pass --ext-org-token …)` · `unknown manifest type: …`

Source: workflows/cd-fast-lane.md

## 8. Delivery sync, stability gate and nightly sync

- **Workflow:** `workflow-entry/.github/workflows/delivery_sync_external.yaml`.
- **Target orgs:** `aqua-external`, `tahoe-external`, `atlas-external`; also `aqua-staging` / `elmnt-platform-staging` as staging orgs.
- **Scope:** one run handles one `(org, project, revision, manifest)`.
- **Scheduling:** there is no GitHub `schedule:` trigger. Gatestage `CreateDeliverySyncCron` (`apps/services/gatestage/internal/github_manager/github_manager.go`) upserts one cron row per entry from its `schedule`. This is the "nightly CD sync" mechanism; the cadence is whatever the entry's cron says.
- **Job chain:** `resolve` (`resolve-delivery-entry.sh`) → `staging-sync` → `staging-build-test-and-package` (**the gate**) → `delivery-sync` → `post-build`.
  - Builds call `infra_ai_build.yaml`.
  - A `post-build` failure turns the run red even though delivery already happened.
- **Staging:**
  - The staging pass inherits the entry and overrides only the fields set under `staging`.
  - `staging.org` is required, must differ from the target, and an org can gate a given manifest for only one target. `ParseCdConfig` and `resolve-delivery-entry.sh` enforce this.
  - Dispatching directly to a staging org needs that org's own entry.
- **Manual inputs:** `target_organization`, `project`, `revision`, `manifest_name`, `manifests_branch`, `devops_ref`, `force_stability_check`. The entry must exist and have `enable: true`; otherwise `resolve` emits `::error::`.
- **Testing the gate without delivering:** set every `sync_*: false` on the entry and `true` under `staging`.
- **What a pass does:** composite action `delivery-sync-pass` runs fetch-manifest-data → hide-projects → clean starfiles → sync repos → docker → manifest → elbuilder → platform tools. Runners are `cxi.8xlarge.on-demand`, ephemeral.
- **Tags:**
  - The source manifests repo gets `<target_org>-<DELIVERY_VERSION>`. The tag is force-moved, so it is **mutable**.
  - The target `infra-ai` repo gets the bare version.

**Stability gate** (opt-in with `stability_check: true`; also requires `sync_manifest: true`)
1. Rebuilds the internal upstream manifest and runs the `cd_stable_*` groups (`run_type: cd-stable`) from its starfile in `module.devops.configuration`. The gate discovers them with `--list-configuration`.
2. A new suite goes live only after `devops-configuration-ci` is promoted and integration bumps its pin.
3. On green, `<version>-stable` is aliased in the org repos, the infra repo, a manifests marker tag and the Platform Tools release.
4. Throttle: measured from the newest `<org>-<version>-stable` tag in `ip.infra.sw.manifests` against `stability_check_interval_days`. An org with no such tag is always gated.
5. Log line when skipped: `last stable delivery is Nd old, interval is Md`. Override with `force_stability_check: true`, which is a dispatch input, not a config key.

Source: how-to/cd-external/delivery-sync.md, workflows/cd-config-reference.md, workflows/faq.md

## 9. Delivery stubs and FW release assets

- **Stubs:** `workflow-entry/.github/workflows/gh-scripts/delivery_sync/stub/<project>/[<branch>/]{pre,post}-sync-stub.sh` (branch level wins). Env: `PROJECT`, `BRANCH` (pre-rename), `MANIFEST_NAME`, `TARGET_ORG`, `TARGET_TOKEN`/`GH_TOKEN`, `MANIFESTS_REPO`, `AWS_REGION`, `SYNC_FW`. Set `sync_fw: true` on exactly one entry per org.
- **`sync_config.json`:** `build_artifacts/opt/atl/sync_config.json` (from `project.iridium.sw.fw.infra/build_infra/sync_config.json`, read with `jq`). Keys `delivery-scheme.{mandatory-sign, dest, signed[], unsigned[], extras[]}`; local override `SYNC_CONFIG_FILE`.
- **iridium pre-sync stub:** fails the delivery if `mandatory-sign` is set and no `signed-key-*-<version>` folder exists in S3. Production signing (§13) must therefore finish first.
- **Post-sync stub:** publishes a GitHub release in `dest` (e.g. `ip.mng.sw.fw`) tagged `<branch>-v-<version>` (e.g. `accton-rc8-v-1.662.0-01082026-220241`). Assets: `*_key_0` (unsigned), `*_key_<N>` (signed), extras verbatim.
- **S3 paths:** unsigned `.../psoc-fw-ci/<rev>/<ver>/linux/<kver>/x86_64/Release/psoc-fw-ci.tar.zstd`; signed `.../signed-key-<N>-<ver>/`.

Source: how-to/cd-external/delivery-sync-stubs.md

## 10. CD source: editing what is delivered

- **Steps:** edit `*-cd.external.xml` in the revision dir, validate with `xmllint --noout`, open a PR to `ip.infra.sw.manifests`. Merging triggers CD via `manifests_ci.yaml`.
- **Prerequisite:** a repo must already be in `integration-ci.xml` to be deliverable.
- **`<sparse>`:** single-quoted gitignore-style patterns. Allowlist: `'/docs/' '/src/'`. Denylist: `'/*' '!tests/'`.
- **Always stripped:** `CODEOWNERS`, `.github`, `.gitignore`, `.elmnt`.
- **Destination branch:** in the customer org it is the 3rd path segment of the manifest path, i.e. `<revision>`.
- **Legacy model** in older how-tos: `<component>.xml.tmpl` → generated `.xml.delivery` / `external.xml.tmpl`, regenerated by a separate `cd_source` flow. Treat it as historical; current delivery manifests are `*-cd.external.xml`.
- **FM/FB → release branch:** use `github-runner code-porting --source-revision <rev> --target-revision <tgt>` with exactly one of `--manifest-folder`, `--manifests` or `--projects`.
  - It creates `porting/<rev>` per repo (recreated if it exists), rebases onto the target and force-pushes.
  - It opens a PR titled `[SW-0] Code porting` (skipped if there is no diff) and links all PRs with `DEPENDS_ON:`.
  - Output: `code_porting_result.json`.
  - On conflict the rebase is aborted and the PR shows the conflicts.
  - The docs describe no dedicated "FM/FB CD source" workflow beyond this porting tool and release branches; see §19.

Source: how-to/cd-external/cd-external-user-guide.md, how-to/modify-cd-source.md, how-to/cd-at-element-overview.md, architecture/code_porting.md

## 11. Packages (elbuilder/nfpm, deb/rpm, DKMS)

**Definitions** are in `module.devops.configuration/packages-definitions/`. Cited paths vary: `atl/*.yaml`, `iridium/sw-cd.yaml`, `iridium.yaml`; see §19.

```yaml
packages:
  - name: elmnt-runtime
    description: "Runtime"
    depends: [elmnt-dkms]               # also provides/replaces/conflicts/recommends/suggests, meta
    manifests:
      - name: project-iridium-sw-runtime-ci      # ARTIFACT name, not repo name
        contents:
          lib:     [{src: libruntime.so}]
          configs: [{src: wheels/my_tool-*.whl, preserve_path: true}]
    scripts: {postinstall: post.sh}     # under bin path; exit 0, non-blocking, quiet in containers; not on deb-only pkgs
  - name: elmnt-dkms
    manifests: [{name: drv-ci, contents: {deb: {name: dkms, src: "dkms/elmnt-dkms*.deb"}}}]  # prebuilt .deb passthrough
```

- **Content types:** `bin`, `lib`, `include`, `specs`, `configs`, `fw`, `dkms`, `deb`.
- **Source and install locations:** `lib` is searched at `build_artifacts/<machine>/Release/lib/<src>`. `configs` is searched at `build_artifacts/opt/<prefix>/<src>`.
- **Install prefix:** the docs disagree between `/opt/elmnt/*` + `/lib/firmware/element` and `/opt/atl/*` + `/lib/firmware/atl`; see §19.
- **DKMS:** the kernel-module package is the prebuilt `.deb` produced by the driver artifact (`drv-ci`), passed through as-is. `octl` build/test commands accept `--skip-dkms`.

**elbuilder package** (mirrors `octl package`)

| Command | Key flags |
|---|---|
| `elbuilder package generate-config` (alias `generate`) | `-d/--packages-definitions` (required); `-p/--packages` (repeatable, or positional); `-V/--pkg-version` (default `0.0.1`); `-O/--output-dir`; `-o/--out` (JSON list); `-t/--nfpm-template`; `-k/--key-file` (PGP signing of the deb/rpm) |
| `elbuilder package create` | `-F/--from-file` or `-f/--config` (mutually exclusive); `-t/--target` (`generated/`); `-p/--packager` `deb\|rpm` (default `deb`); `--hashes-file` (`files_sha256.txt`); `--archive`; `--archive-name` (`packages.tar.gz`); `--nfpm-download-url` (auto nfpm v2.43.4) |

The octl equivalents are `octl package generate-config ... --out configs.json` and `octl package create --from-file configs.json --target out/ --packager deb`. Inspect the result with `dpkg-deb -c <pkg>.deb`.

**Aqua-style workspace for package testing:**
- External: `elbuilder init -u git@github.com:aqua-external/infra-ai.git -m atlantic-a1-sw.xml [-b <tag>] [--skip-image-pull]`
- Internal: `-u git@github.com:Elmnt-Internal/ip.infra.sw.manifests.git -m atlantic-a1-sw.xml.delivery`

**Errors:** `package must define manifests: <pkg>` · `package <pkg> must define manifest name` · `invalid artifact type` · `failed to find relevant content in source ...` · `no matches could be found to the requested contents in <pkg>`

Source: how-to/edit-package-configurations.md, how-to/create-edit-cd-package-definitions.md, architecture/elbuilder_package.md, octl/octl_workspace_build.md

## 12. Docker images and delivery to Atlas/Accton

| Image | Tag | Where |
|---|---|---|
| Delivery (artifact-assembled) | `<project>/cd/delivery:<manifest>_<release_version>-<DDMMYYYY>-<HHMMSS>` | ECR `891377201015.dkr.ecr.eu-central-1.amazonaws.com` (eu-central-1) |
| Test (from-source) | `...-test` (`test_image_tag`) | ECR |
| Customer copy | `ghcr.io/<org>/...` (e.g. `ghcr.io/aqua-external/infra-ai`) | GHCR, via delivery sync `sync_docker_images` |

- **Finding the image for a run:** the `build_docker_image_with_upstream_artifacts` job output (`result.json`), or ECR repo `<project>/cd/delivery` sorted by push date.
- **Delivering a docker image to Atlas (Accton):** Portal → **Delivery sync** → choose the branch/manifest (`atlas-system-validation-cd.external.xml` → org `atlas-external`) → submit. This dispatches `delivery_sync_external.yaml`.
- The image must already exist from a green CD external run.
- FW release assets for Accton come from the post-sync stub (§9), e.g. the tag `accton-rc8-v-<ver>`.

Source: how-to/deliver-docker-image-atlas.md, workflows/faq.md, workflows/cd-external.md, how-to/cd-external/delivery-sync-stubs.md

## 13. Artifact signing with production keys (KMS/KMSS)

1. **Request:** Portal `https://portal.ai.elmnt.local/catalog/github-workflow` (VPN required) → **New KMS Production Key Signing**. Fields: Project, Component (e.g. `psoc-fw`), Key Index, Version, Manifest Name (required, e.g. `psoc-fw-ci.xml`), Revision, Environment (`production` or `staging`).

2. **Ticket:** gatestage `POST /api/v1/kms/sign-requests` opens a ticket in the **KMS** Jira project for the approvers.
3. **Dispatch:** after approval, gatestage verifies the key scope and queues `kms_production_sign.yaml` ("KMS signing") through the Portal queue (category `allow_parallel`). The run name is the Jira key.
4. **Approval token:** HMAC-SHA256 from KMS `GenerateMac`, sent as header `X-KMSS-Token`.
   - Scoped to `project/component/keyIndex`.
   - TTL is **20 min** (previously 1 h).
   - Not renewable or revocable.
   - An expired token gives `403 Invalid or expired approval token`. Re-running an old run fails the same way, so file a new request and get a new Jira approval.
5. **Output:** signed artifacts land under `signed-key-<N>-<ver>/` in S3. The iridium pre-sync stub requires them when `mandatory-sign` is set (§9).

- `octl` containers auto-mount `EL_KMSS_*` env vars as secrets (`--secrets`).
- The Confluence design (856784900) adds the following: approvals check requestor identity, artifact immutability, environment and key scope; the request is locked against concurrent runs; the secret is single-use and never logged.

Source: how-to/cd-external/kms-production-signing.md, workflows/portal-queue.md, octl/octl_workspace_build.md; Confluence 856784900

## 14. Release branches, versioning and post-release

**Release branch (current docs)**
- Bootstrapped from `sw-cd.xml`, the last passing internal CD, not from `main` HEAD or `integration-ci.xml`.
- Per-repo commits are recovered from `s3://<bucket>/<project>/<artifact>/<branch>/<ver>/manifest.xml`.
- Created with the **Mirror Manifests** workflow:
  ```
  project=<p> base_branch=main target_branch=rb-<project>-<version> revision_source=sw-cd
  disable_integration=false disable_tests=false with_L1=true vcs_release_version=<vX.Y.Z> package_delivery=true
  ```
- Phases: Phase 1 stabilization (fixes only), then Phase 2 feature porting (`code-porting`, §10).
- Checklists exist for before cutting and before Phase 2.
- `main` continues independently; teams cherry-pick fixes back.
- Intended order: internal CD first, then external CD sourced from the internal stable output. This is not fully implemented; see §19.

**Versioning**

| Item | Value |
|---|---|
| Release version | `org.vcs.release_version` (default `v1.0.0`). Injected into builds via `octl ws build --additional-context '{"vcs": {"release_version": "...", "artifact_version": "..."}}'` (Confluence) |
| CD delivery version | `<release_version>-<DDMMYYYY>-<HHMMSS>`, e.g. `1.0.0-11032026-143000`. CD internal candidate: `1.0.0-<timestamp>` |
| Stable alias | `<version>-stable`, plus the manifests tag `<org>-<version>-stable` |
| Delivery tags | `<target_org>-<version>` (manifests repo, mutable); bare `<version>` (customer infra repo) |
| Bump policy (Confluence 692781319, Portal release form) | hot-fix → patch; new-features → minor; api-changes → major. The latest tag is read from the manifests repo |

**Release protection (Confluence 692781319)**
- The manifests repo is tagged.
- Release branches are protected in all repos: no force-push, no delete, PR review required.
- S3 packages are tagged with `integration_manifest_version`, `release_version`, `artifact_version`, `created_timestamp`.
- Failures at any stage create a Jira ticket for the manifest owner.

**Post-release** (Portal-triggered, Confluence 666042370):
1. Tag the revision's files in GitHub.
2. Delete the revision's manifests from `ip.infra.sw.manifests` and `regression.yaml`.
3. Block push and delete on the old revision.

Source: workflows/release-branch-workflow.md, workflows/cd-config-reference.md, workflows/faq.md, how-to/cd-external/delivery-sync.md; Confluence 692781319, 666042370

## 15. Customer environments: Aqua and Tahoe

| | Aqua | Tahoe |
|---|---|---|
| Org / infra repo | `aqua-external` / `aqua-external/infra-ai` | `tahoe-external` / `tahoe-external/infra-ai` |
| Manifest | `atlantic-a1-sw.xml` | `tahoe-a1-sw.xml` |
| Registry | `ghcr.io/aqua-external/...` | `ghcr.io/tahoe-external/...` |
| Default test | `entrypoint.sh` groups (`atl_cd_tests`, ...) | `run_large_moe_simulator`. Perf sim: `elbuilder test bash` → `torch_atl` + `start_perf_sim` |
| Access | DRR ticket + @ogabbay approval | Request from the Platform team |
| Host | Ubuntu 24.04, kernel 6.8. Tests need ≥512 GB RAM and ≥192 vCPUs | Ubuntu 24.04, kernel 6.8 |

**Setup:** udev → Docker → GitHub PAT with `read:packages` (GHCR login) → install elbuilder → init/sync/build/test:
```bash
elbuilder --version                       # same release tag as `octl version`
elbuilder init -u git@github.com:aqua-external/infra-ai.git -m atlantic-a1-sw.xml [-b <TAG>]
elbuilder sync && elbuilder build && elbuilder test   # tests need AWS creds via `aws sts assume-role`
```

- To reproduce a customer version, use `-b <TAG>` with a delivery tag (bare version or `-stable`).
- If you need an EC2 host, use `octl vm create`.

Source: agents/aqua-env.md, agents/tahoe-env.md, how-to/edit-package-configurations.md

## 16. Testing CD locally

**CD external**
1. Get the failing job id.
2. Extract the resolved manifest from the logs: `gh api repos/<org>/<repo>/actions/jobs/<id>/logs | awk '/target manifest:/ ...'` and save it as `<dir>/default.xml`.
3. Initialise: `elbuilder init -u file://<dir> -m default.xml`.
4. Log in to ECR. Tokens last 12 h:
   ```bash
   octl login
   # assume arn:aws:iam::891377201015:role/role-project-iridium with profile devops-cli
   aws ecr get-login-password --region eu-central-1 | docker login --username AWS --password-stdin 891377201015.dkr.ecr.eu-central-1.amazonaws.com
   ```
5. Get the latest `elbuilder_linux_amd64.tar.gz` release from `ip.infra.devops.apps`.
6. Run, as needed:
   - `elbuilder sync --depth 1`
   - `elbuilder build`
   - `elbuilder test [--image <img>|--packages <archive>]`
   - `elbuilder package generate-config ... && elbuilder package create ...`

- The image is about 16 GB.
- The log line `Skipping on copying build artifacts from image` explains missing headers or libs.
- `elbuilder test --packages <archive>` unpacks the archive with `dpkg-deb -x` inside the image.

**CD internal**
1. Write a temporary manifest that copies the artifact versions from upstream. Use `source` for external artifacts.
2. Run:
   ```bash
   octl workspace init -p <proj> -m <file> --dir <abs> [--depth 1]   # or: octl workspace init --github-job-url <url>
   octl workspace build [-O <art>:<ver> | -O <art>:file://<path>] [--skip-artifacts]
   octl workspace test --phase L1
   ```

**Artifact-only workspaces** (VLSI, FPGA, Palladium): `octl ws init -p <P> -m <M> --version <V> --artifacts-only`. Local patches are shared with `octl ws package --all` + `octl ws upload-patch` (`artifacts_only_manifest.xml`).

Source: how-to/cd-external/testing-cd-external-locally.md, how-to/cd-internal/testing-cd-internal-locally.md, architecture/collaboration_workflow.md

## 17. Troubleshooting / failure signatures

| Symptom / string | Cause | Fix |
|---|---|---|
| CD external did not run after integration | `enable: false`, no `cd_external` entry, nothing changed (`changed=false`), or the cron has not fired | Enable or add the entry; dispatch with `force=true`; wait for the cron |
| `manifest <name> not found in cd_external config` | Manifest added or renamed without a config key | Add `cd_external.<file>` |
| `delivery_sync org <org> references manifest <name> which is not configured under cd_external` | Sync entry without a CD entry | Add the `cd_external` entry (docs fast-lane manifests are the exception) |
| `schedule min interval is 3h ...` / `not a valid cron` | Cron too frequent or malformed | Use ≥3h; use `"0 0 31 2 *"` for manual-only |
| `artifact not found` while building the image | Name mismatch or casing, artifact not yet integrated, or wrong revision dir | Compare against `integration-ci.xml` `name=` |
| `no matches` (sparse/extract) | Glob matches nothing | Fix the pattern |
| Docker push 403 | IAM / ECR permissions | Check the role (`role-project-iridium`) and ECR login |
| `merge_manifest` conflict | Concurrent manifest merges | 5× auto-retry; otherwise re-run |
| Integration not merging; `cd_external_build` red | Candidate does not build from source, the short test failed, or the timeout was exceeded | Fix the build; raise `integration_build_check.timeout_minutes`; use `skip_cd_external_test` only to triage |
| `X is not defined` under elbuilder only | Starfile reduction dropped a public global, a public helper or a non-`utils.star` load | Make helpers and constants `_private`; move shared code to `utils.star` |
| `no matches could be found to the requested contents in <pkg>` / `failed to find relevant content` | Wrong artifact name, content type or src glob | Use the artifact name (`*-ci`) and the right type folder |
| Missing headers/libs in a local `elbuilder build` | `Skipping on copying build artifacts from image` | Pull the right image / ECR login |
| `::error::` in `resolve` (delivery sync) | No entry for the org/manifest, or `enable: false` | Add or enable the `delivery_configs` entry |
| Delivery green but run red | `post-build` failed after the delivery | Inspect `post-build`; the delivery already happened |
| Pre-sync stub fails (iridium) | `mandatory-sign` set and no `signed-key-*-<ver>` in S3 | Run KMS signing first (§13) |
| No stability check ran | Not opted in, interval not elapsed (`last stable delivery is Nd old, interval is Md`), or `sync_manifest: false` | `stability_check: true`, `force_stability_check: true`, `sync_manifest: true` |
| `403 Invalid or expired approval token` | KMSS token older than 20 min, or a re-run of an old signing run | New Portal request plus approval |
| Fast-lane errors (`not anchored`, `found 0`, `Repository does not exist...`, `no client for external org`) | Bad `<sparse>`, missing destination repo, or missing token | See §7 |
| GHCR pull denied (customer env) | PAT lacks `read:packages`, or no org membership | New PAT; DRR / Platform access |

Source: workflows/faq.md, how-to/cd-external/cd-external-user-guide.md, workflows/cd-config-reference.md, workflows/integration-testing.md, how-to/cd-external/delivery-sync.md, how-to/cd-external/kms-production-signing.md, architecture/elbuilder_starfile_reduction.md

## 18. Pitfalls

- **Wrong layer.** `integration-ci.xml` is never a delivery target. Curate through `*-cd.external.xml`, and add a repo to integration before you add it to delivery.
- **Hand-pinning.** Editing `version=`, `revision=` or `external_image=` by hand is overwritten or causes conflicts. Leave them empty.
- **Package names.** Package definitions use **artifact** names (`module-arch-sw-specs-ci`), not repo names.
- **`docker` vs `packages`.** They do not inherit from each other. Specify `os`, `arch` and `build.kernel` in both.
- **Fast-lane sparse.** A typo in a fast-lane `<sparse>` can wipe the customer's docs (`rsync --delete`).
- **Mutable tag.** `<target_org>-<version>` in the manifests repo is force-moved. Do not treat it as an immutable release marker; use `-stable` and the customer infra tags.
- **Staging overrides.** Staging overrides only the fields you set under `staging`. Everything else, including `sync_*`, is inherited. A misconfigured staging org can therefore deliver for real.
- **Starfiles.** A starfile that works with `octl` can break under `elbuilder` (§5 reduction rules). Test with `elbuilder build` locally.
- **Signing order.** Signing tokens last 20 min and are single-scope. Do not queue them behind long waits, and sign before a delivery that needs `mandatory-sign`.
- **`sync_fw`.** `sync_fw: true` on more than one entry per org duplicates FW releases.
- **Stability suites.** Changing the CD-stable suites means editing the starfile in `module.devops.configuration`. The change does not take effect until the `devops-configuration-ci` pin is bumped by integration.
- **Release-branch base.** Release branches start from `sw-cd.xml` (last internal CD green), not `main` HEAD. A commit that landed after the last internal CD is not on the branch.
- **`/tmp` in doc examples.** Several local-testing examples in the docs use `/tmp`. Use a workspace directory instead where policy forbids `/tmp`.

Source: how-to/cd-external/cd-external-user-guide.md, workflows/cd-fast-lane.md, how-to/cd-external/delivery-sync.md, workflows/release-branch-workflow.md, workflows/faq.md

## 19. Contradictions and unclear points

GitHub docs win over Confluence. Flag these points when answering.

1. **Release-branch CD ordering.**
   - `how-to/cd-at-element-overview.md` and `workflows/release-branch-workflow.md` say internal CD runs first and external CD then sources from the internal stable output.
   - `workflows/cd-workflow.md` says the two are independent triggers, that external upstream is `integration-ci.xml` on the branch, and that release-specific selection is "not fully implemented".
   - The release-branch doc's own historical example uses `cd_package`/`sw-cd.xml`.
2. **Delivery image overwrite.** The overview says the source build overwrites the artifact-assembled image. `workflows/cd-external.md` and `cd-external-pipeline-flow.md` say a separate `-test` image is pushed and the delivery `image_tag` is not overwritten. Trust the latter.
3. **Location of `org.cd`.** `cd-config-reference.md` says it moved to `cd-config.yaml`. Its own Relationship table, `pr-to-delivery-journey.md` and `modify-cd-source.md` say `config.yaml`.
4. **`cd_internal.schedule`.** `cd-internal.md` and `cd-internal-guide.md` say it is required with a minimum of 3h. `cd-config-reference.md` says it is optional, with blank meaning manual-only.
5. **Legacy source model.** `modify-cd-source.md` and `edit-package-configurations.md` describe `<component>.xml.tmpl`/`.xml.delivery`/`external.xml.tmpl`. The current model is `*-cd.external.xml`.
6. **Package paths and names.**
   - Install prefix: `/opt/elmnt/*` + `/lib/firmware/element` vs `/opt/atl/*` + `/lib/firmware/atl`.
   - Package names: `elmnt-*` vs `atl-*` (`atl-sw`).
   - Definitions path: `atl/` vs `iridium/sw-cd.yaml` vs `iridium.yaml`.
   - Check `module.devops.configuration/packages-definitions/` for the truth.
7. **Example values.** Example runner labels (`mxi.48xlarge` vs `cxi.8xlarge.on-demand`) and crons (`*/3`, `*/6`, `*/8` hours) vary. They are examples, not policy.
8. **`testing-cd-external-locally.md` repos.** Step 1 cites a workflow-entry job URL, but Step 3 calls the logs API on `ip.infra.sw.manifests`. The intro mentions `octl ws init --github-job-url`, which the steps never use.
9. **External upstream.** The user guide says it is "`integration-ci.xml` directly". The release-branch model and the integration check (`--candidate-manifest-file`) imply an internal upstream such as `atlantic-a1-sw-cd.internal.xml` is possible.
10. **Release-branch naming and flow (Confluence vs docs).**
    - Confluence 692781319: branch and tag `<project>_release_<vX.Y.Z>`, mirrored from `main`, with deb/rpm creation inside the release flow.
    - Docs: branch `rb-<project>-<version>` from `sw-cd.xml` through Mirror Manifests.
    - Prefer the docs. The Confluence version-bump policy (patch/minor/major) has no GitHub-doc counterpart, so treat it as unverified.
11. **FM/FB branch workflow for CD source.** No Platform doc defines an "FM/FB" CD-source workflow. The closest are `architecture/code_porting.md` (`porting/<rev>` PRs) and release-branch Phase 2. Treat any other FM/FB claim as unverified.

Source: files cited inline; Confluence 692781319

## 20. Owner / escalation

- **Owner:** Platform / DevOps, GitHub team `Elmnt-Internal/sw-platform-sg` (owner field on every doc above). Contact routes are in `how-to/contact-us.md`.
- **Forced CD re-runs:** `--force` / `force=true` dispatches, manual delivery syncs to customer orgs and `force_stability_check` are DevOps-gated. Ask Platform.
- **Approvers:** production signing = KMS Jira project approvers via the Portal; `aqua-external` access = DRR ticket + @ogabbay; `tahoe-external` access = Platform team.

- **Failing runs:** CD external and delivery-sync failures auto-create Jira (`create_jira`, `stability_jira_assignee`). Start from that ticket and the run's `result.json`.
- **Content owners:** package-definition and `entrypoint.sh` changes go through `module.devops.configuration` PRs. `cd-config.yaml` and delivery-manifest changes go through `ip.infra.sw.manifests` PRs.

Source: how-to/contact-us.md, how-to/cd-external/kms-production-signing.md, agents/aqua-env.md, agents/tahoe-env.md, workflows/faq.md
