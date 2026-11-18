# octl — the Element Platform CLI (formerly `elmcli`)

`octl` is the single CLI the SW group and CI use to lay out multi-repo workspaces from
manifests, build/test them inside project containers (driven by per-repo **starfiles**),
fetch/publish S3 artifacts, drive CI on demand, and allocate personal compute (EC2 VMs,
Ocean k8s workloads). Verified against live `octl` **v0.213.7** (build 2026-11-16) help
output; docs under `docs/` are Platform-maintained and partly auto-generated from that help.

**Contents**
1. Install, auth, config, global flags & env vars
2. Command map (all top-level commands)
3. Workspace lifecycle (`octl ws ...`)
4. Build under the hood: starfiles, containers, `build_artifacts`, `--args`
5. Testing, unit-test, lint, exec/debug
6. Remote / PLDM builds, CI data, workflows, cron
7. Manifests, artifacts, depends-on, star, package
8. Copilot plugins (`octl plugins`)
9. VMs (`octl vm`) and workloads (`octl wl`)
10. VM operational policy (lease, budget, cleanup, disk, storage, EFA, flex, spot)
11. Security scans (Coverity, BlackDuck), devcontainer
12. Design notes ("octl second iteration")
13. Doc-vs-CLI drift (verified)
14. Troubleshooting / error strings
15. Pitfalls
16. Owner / escalation

---

## 1. Install, auth, config, global flags & env vars

**Prereqs:** AWS CLI v2 (`2.17.54` pinned in docs), AWS SSO profile `devops-cli`
(`aws sso login --profile devops-cli`; pick role **ElmntSoftwareDeveloper** or **ElmntVlsiUsers**
at `https://elmnt.awsapps.com/start`; session max 12h), `gh` CLI logged in
(`gh auth login --skip-ssh-key -p ssh -w`). VLSI hosts: `module load gnu/awscli/2.17.54`,
`module load gnu/gh/2.72.0`.

**Install:** download the latest release tarball from `Elmnt-Internal/ip.infra.devops.apps`:
```bash
TAG=$(gh release view --repo Elmnt-Internal/ip.infra.devops.apps --json tagName --jq .tagName)
gh release download $TAG --repo Elmnt-Internal/ip.infra.devops.apps --clobber -D <dir>
tar -xvf <dir>/octl_linux_amd64.tar.gz -C ~/.local/bin/   # ensure ~/.local/bin on PATH
```
**Auto-update:** every invocation checks GitHub for a new release if ≥30 min since the last
check (not configurable) and self-updates immediately. Manual: `octl update`. Disabled by
`OCTL_OFFLINE_MODE=1`. `octl version` prints version/revision/build date (there is no
`--version` flag).

**AWS auth model:** octl uses `aws sts assume-role` into a **per-project role** for
project-scoped resources (ECR images, S3 artifacts) and general roles for EC2. Temporary
credentials are cached in SQLite at `~/octl/octl.db`. `octl login` "Starts the AWS Login
process" (SSO). `--profile` selects the AWS profile (default from config, i.e. `devops-cli`;
ignored in CI unless set).

**Config precedence:** `flag → env → config file`.
- Env: any flag as `OCTL_<FLAG_NAME>` with `-`→`_` (e.g. `--show-summary` → `OCTL_SHOW_SUMMARY=true`).
- File: `$HOME/.octl.yaml` (override with `--config`). Any flag can be a root key by exact
  flag name (`show-summary: true`). `octl config` prints the additional (non-flag) sections:

```yaml
tests:                 # per-stage output of test tasks
  show_pre_stage: ""   # e.g. on_failure
  show_main_stage: ""
  show_post_stage: ""
shared_cache:          # sccache
  local_mode: false    # true = local dir instead of S3
  dir: ""              # default $XDG_CACHE_HOME/octl/build
  max_size: ""         # default 10G
plugins: {}            # plugins.enable: false = octl never touches Copilot plugins on its own
github_auth: {}
```
- Artifact download cache: `$XDG_CACHE_HOME/octl/artifacts` (each version downloaded and
  verified once; workspaces extract from the cache; `--force-download` to refetch).
- Log: `$HOME/.octl.log` (`--logfile`); attach it to support requests.

| Global flag | Meaning |
|---|---|
| `--config` | config file (default `$HOME/.octl.yaml`) |
| `--profile` | AWS profile (default from config; ignored in CI unless set) |
| `--verbose` / `--silent` / `--plain` | verbosity / silence / no colors (`OCTL_VERBOSE`, `OCTL_SILENT`, `OCTL_PLAIN`) |
| `--log-format text\|json`, `--logfile` | log format / path |

| Env var | Effect |
|---|---|
| `OCTL_OFFLINE_MODE=1` | No external services when possible: skips auto-update, AWS role assumption, ECR auth; commands needing services fail fast |
| `OCTL_NON_INTERACTIVE=1` | Disable all prompts (CI, scripts, agents) — prompts are skipped, not blocked |
| `OCTL_SKIP_IMAGE_CHECK=1` | Skip custom-image validation on build |
| `OCTL_FEATURE=ocean-vm` | Expose `a1.*` / `dgx.*` Ocean flavors in `octl vm` |
| `EL_*` | Propagated into build/test containers |
| `EL_KMSS_*` | Auto-detected and mounted as secrets (`--secrets`) |
| `CI=true` | All Copilot-plugin paths become no-ops |

Docs: `octl help ws init` == `octl ws init -h`; `sudo octl man` generates man pages;
`octl completion` generates shell completion.

Source: how-to/setup-octl-dev-workspace.md, how-to/config-octl.md, octl/octl_aws_auth.md,
octl/workspace-faq.md, how-to/ci/starfile.md, `octl --help`, `octl config` (v0.213.7);
Confluence 1720909943.

---

## 2. Command map

| Command (alias) | Purpose |
|---|---|
| `workspace` (`ws`) | init/sync/build/test/... a manifest-defined multi-repo workspace |
| `artifacts` | `download` artifacts into a workspace; `versions` lists published versions |
| `manifest` (`m`) | `changes`, `download`, `export`, `format`, `tree`, `validate` (`v`) |
| `depends-on` (`do`) | Init + build all manifests involved in a cross-repo DEPENDS_ON set, topologically |
| `ci-data` | Build/transform `ci_data.json` for on-demand CI (`download/init/select/set-*/validate`) |
| `workflow` (`wf`) | `run on-demand`, `runner-logs`, `sherlock` |
| `cron` | `list` GateStage cron workflows, `download-ci-data` |
| `star` | `format` / `parse` starfiles |
| `package` | `create` deb/rpm via nfpm, `generate-config` (`generate`) |
| `plugins` (`plugin`) | `manage` (`install`) / `update` GitHub Copilot CLI plugins |
| `vm` | Personal EC2 VMs: `create/list/start/stop/delete/lease renew` |
| `workloads` (`wl`) | Unified EC2 + Ocean k8s VMs/pods: `create (c)/list (ls)/delete (d)` (preview API) |
| `config`, `login`, `update`, `version`, `man`, `completion` | Utility |

Source: `octl --help` and subcommand `--help` (v0.213.7); octl/octl.md.

---

## 3. Workspace lifecycle (`octl ws ...`)

A **workspace** = directory of git repos laid out from a **manifest** (repo-tool style XML in
`Elmnt-Internal/ip.infra.sw.manifests`, `projects/<project>/<revision>/<manifest>.xml`) plus
pre-built **artifacts** from S3. `.repo/` holds manifest + metadata. Default path:
`$HOME/projects/<project>/ws-<manifest>` (override `--override-path`, absolute only).

| Command | Purpose | Key flags | Notes |
|---|---|---|---|
| `ws init` | Clone repos per manifest, then `sync`, then Copilot plugin step | `-p/--project`, `-m/--manifest-file`, `-r/--revision` (default `main`), `-b/--branch` (manifests repo branch), `--version`, `--dir`, `--github-job-url`, `-I/--interactive`, `--artifacts-only`, `--depth`, `--override-path`, `--clean`, `--with-latest-versions`, `--submodule-args`, plugin flags (`--plugins`, `--plugin-preset`, `--keep-plugins`, `--flatten-plugins`, `--no-plugins`) | Exactly one of `-p` / `-I` / `--github-job-url`; `-p` and `-m` together; `--dir`, `--version`, `--github-job-url` mutually exclusive. `--s3-only` deprecated → `--artifacts-only`. |
| `ws sync` | First run clones; later runs `pull --rebase` per repo (commit revs → detached checkout); also refreshes/moves Copilot plugins | `--ws` (default `.`), `-d/--depth`, `-F/--force-checkout`, `--nmu` (skip manifests repo), plugin flags | There is **no** `ws update`. Errors in one repo don't stop others. Prompts to delete repos dropped from manifest (skipped non-interactive). |
| `ws status` | git status of each repo | global `--verbose` adds last commit | |
| `ws forall` | Shell command in every repo | `-c/--command`, `-e/--abort-on-error`, optional `[PROJECT]` | Single-quote the command |
| `ws build` | Download artifacts, then build repos in containers via starfile `build()` | see §4 | Build whole ws once before single-repo builds |
| `ws install-artifacts` (`install`, `ia`) | Copy each repo's declared `artifacts()` into `build_artifacts` without building | `-I/--ignore-not-found` (default true) + build flag set | |
| `ws test <DIR/IP>` | Run starfile `tests()` suites | see §5 | |
| `ws unit-test` | Run starfile `unit_test()` (help still says "Taskfile") | build flag set + `--tag` | |
| `ws lint <ip>` | Run starfile `lint()` (code + commit-msg lint) | `--user-env` | |
| `ws validate` | Lint + format-check all starfiles in ws | — | |
| `ws exec '<task>'` | Run a **starfile task (function)** in a ws container — not an arbitrary shell cmd | `-l/--list` (+`--verbose`), `-d/--detached`, `--stop`, `--dry-run*` | Looks in project starfile + every `*.star` under `build_artifacts` |
| `ws debug` | Shell in a debug container with artifacts | `--remove`, `--mount src:dst`, `--privileged`, `-d/--detached`, `--device` | Mounts persist for container life; use `--remove` when changing mounts or privileged↔user |
| `ws clean [repos]` | Remove `build_artifacts` + run each repo's `clean` task (parallel) | `--skip-artifacts`, `--skip-clean-task`, `--links`, `--serial`, `--test-output` (default true), `--workspace`, `--arch` | Restores ws after override builds |
| `ws package` | Archive artifacts per starfiles (no upload) | `-n/--name` (default `archive.tar.zstd`), `--type`, `--all` | |
| `ws upload-patch <FILE>...` | Upload archive to collab S3 bucket (hotfix sharing with VLSI) | — | Prints manifest version + bucket |
| `ws remote-build` | Build ws manifest on CI (on-demand wf) and download the artifacts-only manifest | `--from-source`, `--from-build`, `--from-package` | Without `--from-*`, ws must be clean and pushed |
| `ws generate-ci-data` | Produce `ci_data.json` from the ws CI config stage | `--stage` (default `default`), `-o` (default `./ci_data.json`, `-` stdout) | Ws must be clean, all pushed |
| `ws generate-dev-container` | Write VS Code `devcontainer.json` + Dockerfile matching `ws debug` container | `--capabilities`, `--env`, `--mount`, `--privileged`, `--run-arg` | |
| `ws scan` | BlackDuck scan | `--token`, `--project`, `--project-name`, `--version` | §11 |
| `ws security coverity` (`sec cov`) | Coverity static analysis | `--mode`, `--fail-on`, `--submit`, ... | §11 |

Init examples:
```bash
octl ws init -p iridium -m drv-ci                                   # latest from manifests repo
octl ws init -p iridium -m drv-ci --version 1.695.0-15032026-181400 # pinned (see artifacts versions)
octl ws init -p iridium -m debug-manifest.xml --dir $(pwd)/manifests/
octl ws init -p iridium -m fb-iridium-sw-ci --revision fb-iridium-2_9_1_1p0_983
octl ws init --github-job-url <failed-test-job-URL>                 # reproduce CI test job
octl ws init -p iridium -m drv-ci --artifacts-only                  # no git clone
```

Source: octl/workspace-faq.md, octl/octl_workspace_*.md, how-to/setup-octl-dev-workspace.md,
`octl ws <sub> --help` (v0.213.7).

---

## 4. Build under the hood: starfiles, containers, `build_artifacts`, `--args`

**Pipeline of `octl ws build [repo|dir]`:**
1. Resolve artifacts of the manifest; if their checksums changed since the last extraction
   (after `sync`/manifest bump) → automatic `ws clean`, then download (from cache
   `$XDG_CACHE_HOME/octl/artifacts`, bucket default `s3-github-action-package`) and extract into
   `<ws>/build_artifacts`. Skipped once extracted unless `sync`, `--force-download`, or `clean`.
2. Load the **project starfile** (artifact from `Elmnt-Internal/module.devops.configuration`,
   manifest `devops-configuration-ci.xml`, `dep_type="build"`, always extracted to
   `build_artifacts/opt/elmnt/devops/project.star`) and each repo's `<github-repo-full-name>.star`
   at repo root (e.g. `ip.mng.sw.fw.star`).
3. Order repos by the dependency graph (from `.repo`), call each repo's `build()` → suites →
   stages → tasks; **each stage runs in its own container**, tasks of a stage share it.
4. Copy each repo's `artifacts()` into `build_artifacts` so dependents can consume them.

**Starfile entry points** (Starlark, the Taskfile successor): `build()`, `unit_test()`
(short; runs as part of build in CI), `tests()` (called by `ws test`; test container has
**only** `build_artifacts` content, not the ws/build system), `lint()`, `artifacts()`, `clean`.

**`build_artifacts` layout → deployed path in test containers:**

| Type | Build-stage path | Deployed path |
|---|---|---|
| `bin` | `build_artifacts/<ARCH>/<BUILD_TYPE>/bin` | `/opt/elmnt/bin` + `/usr/local/bin` |
| `lib` | `build_artifacts/<ARCH>/<BUILD_TYPE>/lib` | `/opt/elmnt/lib` + `/usr/lib` |
| `include` / `specs` | `build_artifacts/include[/specs]` | `/opt/elmnt/include[/specs]` |
| `configs` | `build_artifacts/opt/elmnt/<DEST>` | `/opt/elmnt/<DEST>` |
| `fw` | `build_artifacts/fw/` | `/lib/firmware/element/` |

Also: `build_artifacts/logs/<ip>.log` with `--log-to-files`; `test_output/` (repo root,
parsed for summaries); `test_state/` at ws root (shared pre/test/post, `context.test.state_dir`).

**Containers:** images come from project ECR, e.g.
`891377201015.dkr.ecr.eu-central-1.amazonaws.com/iridium/ubuntu:24.04-base_x86_64`
(`--distro u24` default). Override per run with `--image` (overrides everything except
Coverity/DKMS images) or per stage via `container_opts` (`image`, `privileged`, `mounts`,
`userId`, ports, `health_cmd`, `extra_docker_args`, `delay_kill`, background). Custom images
must `FROM` the project base image and must not set `ENTRYPOINT`. ECR auth uses the
per-project assumed role (skipped offline). Pass env with `-e VAR=VAL` or `EL_*`; files with
`--secrets NAME=PATH` (→ `/run/secrets/<NAME>`).

**`--args "<string>"`** — an *arbitrary opaque string* injected into the starfile context as
`context.build.args` (legacy alias `octl.build.args`). octl does **not** parse it; the repo's
starfile decides meaning. So `octl ws build ip/mng --args "platform=arm variant=asic"` works
only because `ip/mng`'s starfile parses `key=value` tokens (e.g. `platform`, `variant`,
`app_name`). The same `--args` reaches `ws test`, `unit-test`, `lint`, `exec`, `debug`,
`install-artifacts`, `artifacts download`, `depends-on`, `coverity`. In CI config the
equivalent is `octl.args`/`octl.extra_flags`; cron "Dynamic" mode passes `--args` both to
`--list-configuration` and to `ws build`, so starfiles must tolerate unknown tokens.

**Other starfile context fields:** `context.ws_root`, `context.metadata`, `context.build.repos`,
`.os`, `.arch`, `.build_type`, `.kernel`, `.system_context.github.{run_type,changed_files,pr_labels,pr_branch,integration,depends_on.*}`,
`context.test.{tests,phase,group,test_output,state_dir}`. `print(context)` dumps all.

**Build flags (shared "build flag set")**

| Flag | Meaning |
|---|---|
| `[repo\|path]` / `--with-deps` | build one repo / plus its build deps |
| `-O, --override-artifact NAME:VER` | replace artifact version; `NAME:file://<abs ws root>[:repo1,repo2]` builds it from a local ws (repeatable) |
| `--align-artifacts` | with source overrides, give every involved ws the highest version of each shared artifact (writes `<ws>/.temporary_manifests`) |
| `-S, --skip-artifacts NAME\|all` | don't rebuild/download/copy; `all` = no resolve/download and **no auto-clean** |
| `--build-type Release\|Debug\|Sanitizer` | passed to starfile (CMAKE_BUILD_TYPE-like); sticky; switching auto-cleans `build_artifacts` |
| `--arch`, `--os`, `--distro` | target arch / OS (OS also selects artifact flavor: host os-release name, then `linux`) |
| `--tags CSR_TAGS` | comma-separated CSR versions into starlark context |
| `-C, --additional-context JSON` | extra JSON in starlark context |
| `--artifacts-filter key=value` | e.g. `stage=build` |
| `--parallel` | build independent repo groups in parallel |
| `--fail-fast` (default true) | stop on first pre-task error |
| `--skip-dkms`, `--submodule-args`, `--bucket`, `--built-ws-file`, `--debug`, `--log-to-files`, `--main-artifact` (artifacts-only ws) | misc |
| `--remote`, `--local`, `--target pldm`, `--from-file`, `-I`, `--save-as`, `--extra-flags` | remote/PLDM (§6) |

Kernel builds: drivers use `make custom` against `CUSTOMKERNELDIR`; exporting
`CUSTOMKERNELDIR=/lib/modules/<ver>/build` makes octl mount host `/lib/modules` into the
build container. (`--kernel` flag: see drift §13.)

Source: octl/octl_workspace_build.md, octl/workspace-faq.md, how-to/ci/starfile.md,
how-to/create-cron-workflow.md, workflows/ci-config-reference.md, `octl ws build --help` (v0.213.7).

---

## 5. Testing, unit-test, lint, exec/debug

| Command | Key flags | Notes |
|---|---|---|
| `octl ws test <DIR/IP>` | `-t/--tests a,b`, `-g/--group`, `-p/--phase l0\|l1`, `-L/--list-configuration`, `--output-config FILE`, `--dry-run[-format plain\|json]`, `--dbg`, `--show-summary`, `--hide-table`, `--failed-only`, `--show-success`, `--show-skipped`, `--show-failed`, `-I/--interactive-summary`, `--save-context`, `--save-task-results` (JUnit XML), `--tag` (single CSR version), `-e`, `--args` | `--dbg` drops into the test container with `/tmp/test_cmd.sh`. `--list-groups` is deprecated → `--list-configuration`. L1 includes L0. |
| `octl ws unit-test` | build flag set + `--tag` | Runs `unit_test()` from the starfile |
| `octl ws lint <ip>` | `--user-env` | |
| `octl ws exec '<task>'` | `-l`, `-d`, `--stop`, `--dry-run` | Shell completion lists task names |
| `octl ws debug` | `--remove`, `--mount`, `--privileged`, `-d`, `--device` | `-d` useful for background agents |

Suite model: `pre` / `main` / `post` stages, each its own container; suite stops if `pre`
fails; `post` always runs. Test summaries parsed from GTest/PyTest output in `test_output`.
Export tests of a phase for CI data: `octl ws test --list-configuration --phase l1 --output-config tests.json`.

Source: octl/octl_workspace_test.md, how-to/ci/starfile.md, how-to/build-and-test-remotely.md,
`octl ws test --help` (v0.213.7).

---

## 6. Remote / PLDM builds, CI data, workflows, cron

### `octl ws build --remote|--local --target pldm` (Palladium job queue)

| Mode | What happens | Preconditions |
|---|---|---|
| `--remote --target pldm` | Dispatches `remote_build.yaml` in `ip.infra.devops.workflow-entry` (CI build of your ws manifest); on success inserts PLDM jobs (one per image_type) into the PLDM Job Queue using the fresh artifacts | **Clean ws: all changes committed AND pushed**; authenticated `gh` |
| `--local --target pldm` | Assumes you already ran `octl ws build` locally; exports ws manifest with **local HEAD revisions** to S3 as `artifacts_only_manifest.xml` (per-build prefix), dispatches `insert_pldm_job.yaml`; **no** remote build | `--from-file` or `-I` required; ws need not be clean |
| `--remote` (no target) | Dispatch a CI build only (FAQ/help example) | clean, pushed |

Tests config: `--from-file <yaml>` or `-I/--interactive` (TUI; saved as
`remote-build-tests-config.yaml`, rename with `--save-as`). `--extra-flags` = extra octl flags
for the remote build step only (per-test flags go in the YAML, e.g. `args: "smoke"`).
`--remote`/`--local` mutually exclusive. `-O` overrides (version or `file://`) work the same.
Image availability shown as `(currently available for pldm job queue)` / `(currently unavailable)`
(job still queues). Output summary: S3 path of manifest, workflow ID, tests-config path, run URL.

### `octl ws remote-build`
Default: builds the ws manifest via the on-demand workflow, uploads to S3, waits, then
downloads the built **artifacts-only manifest** (e.g. `./kernels-ci.xml`). `--from-source`:
build + package `<manifest>.tar.zstd` + upload locally (uncommitted changes OK).
`--from-build`: package+upload existing local build. `--from-package`: upload existing package.
Manual equivalent: `ws build` → `ws package` → `ws upload-patch` →
`manifest download --artifacts-only ... --bucket s3-collab-artifacts`.
Requires exactly one job in the `default` stage.

### CI data pipeline (`ci-data` + `wf run on-demand`)
`ci_data.json` = list of entries (one per build job). All `ci-data` subcommands validate input
first. `-o -`/`--input -` (default `-`) allow piping; logs go to stderr.

| Command | Purpose / flags |
|---|---|
| `ci-data download` | `--github-run-url` / `--github-job-url` (job URL also replaces tests with that job's test), `--index`, `-o` (default `./ci_data.json`) |
| `ci-data init -p <project>` | Empty document |
| `ci-data select [index]` | Zero-based, matches cronwf matrix job / `ci-<run_id>-<index>` |
| `ci-data set-manifest --from-file X.xml [--skip-build]` | Without `--skip-build`, `skip_build` is **cleared** (CI rebuilds) |
| `ci-data set-tests --from-file tests.{yaml,json}` / `--skip` | Replace tests / skip unit+runner tests |
| `ci-data set-dependencies --from-file [NAME=]FILE` | Repeatable |
| `ci-data set-additional-context` | `--phase`, `--pr-labels`, `--pr-branch`, `--changed-files` |
| `ci-data validate` | Exit 0 silent; errors like `invalid ci_data: [0]: json: unknown field "skip_bulid"` |
| `wf run on-demand --ci-data <file\|->` | Dispatch on-demand workflow in manifests repo. `--project`/`--manifest-name` required unless entries carry `target_manifest.content`. `--wait` (+`--wait-timeout`, default 3h), `--pr <URL>` (comment), `--fail-fast`/`--test-fail-fast` (default true), `--auto-ticket-generation`, `--workflow-id`, `--workflow-owner-email`, `--version` (default `latest`), `--revision`, `--bucket` |

Recipes:
```bash
octl ci-data download --github-run-url "$RUN" -o - | octl wf run on-demand --ci-data -     # rerun exactly
octl ws generate-ci-data -o - | octl wf run on-demand --ci-data -                          # run my ws CI config
octl ws generate-ci-data -o - | octl ci-data set-tests --skip -o - | octl wf run on-demand --ci-data -  # build only, no wait
octl ci-data download --github-job-url "$JOB" -o - \
 | octl ci-data set-manifest --from-file <(octl manifest download --artifacts-only -p iridium --github-job-url "$JOB" -o -) --skip-build -o - \
 | octl wf run on-demand --ci-data -                                                        # rerun one test job, no rebuild
```
Narrow a pytest job: `jq '.[0].tests[0].octl.extra_flags += " -e PYTEST_ADDOPTS=\"-k <name>\""'`
(`extra_flags` is pasted unquoted into `octl ws test`; ignored if the test sets `cmd`).

### Workflow helpers and cron
| Command | Purpose |
|---|---|
| `octl wf runner-logs <JOB_URL> [--log-type syslog\|elmnt]` | EC2 runner logs from AWS |
| `octl wf sherlock <JOB_URL>` | Infra root-cause for failed/cancelled job: timeout-minutes exceeded (+ running step), EC2 **spot interruption**. Must be a `/job/<id>` URL |
| `octl cron list` | GateStage cron workflows; filters `--team`, `--workflow-name`, `--display-name`, `--created-by` (exact), `--limit` (50), `--offset`, `--full-names`, `-o` |
| `octl cron download-ci-data <CRON_ID> [--pretty] [-o]` | Saved CI data of a cron (compact JSON to stdout by default) |

Source: how-to/pldm-job-queue.md, how-to/build-and-test-remotely.md, how-to/use-octl-ci-data.md,
octl/octl_ci-data*.md, octl/octl_workflow*.md, octl/octl_cron*.md, `--help` of each (v0.213.7).

---

## 7. Manifests, artifacts, depends-on, star, package

| Command | Purpose | Key flags / notes |
|---|---|---|
| `artifacts versions -p P -m M` | List versions (newest first) with integration status **Pending/Testing/Passed/Failed** and CD status `N/M` | `-r` (main), `--top` (10), `--reverse`, `--summary` (commit titles, action IDs), `--pr <URL>` (versions containing a merged PR; `--reverse --top 1` = first version with it), `-o json\|text\|table`, `-b`, `-I` |
| `artifacts download [name[:ver]]` | Download/extract manifest artifacts into ws | `--starfiles-only`, `--force-download`, build flag set |
| `manifest changes -p -m --from --to` | Commits per repo, repos/artifacts added/removed, version changes | `--pr-only`, `--skip-pr-details`, `-r`, `--bucket` |
| `manifest download` | Manifest of a version from S3 or a GH run/job | `-p`, `-m`, `-r`, `--version` (`latest`), `--artifacts-only`, `--github-run-url`/`--github-job-url`, `--index`, `-o`, `--bucket` |
| `manifest export` | Manifest from current local HEAD SHAs (stdout) | `-o`, `--upload` (collab S3), `-O NAME:VER` (no source overrides); `--from-release -m --repo-name --sha` works without a ws |
| `manifest format -f FILE` | Prettify in place | |
| `manifest validate FILE` (`v`) | Validity check | |
| `manifest tree` | Dependency graph | `-d`, `--flat`, `--repos-graph`, `--out` (tree.json), `--bom -p -m -f` (+`--ignore-conflicts build,runtime,external,infra\|all`) |
| `depends-on` (`do`) | For `--deps repo:branch,PR-URL,artifact:version`: find involved manifests, init ws with branches/versions, build topologically | `-p`, `--init` / `--build` (split), `--override-path` (default root `$HOME/projects/<project>`), `--clean`, `--manifests-repo-path`, `-b`, `-r`, `--depth`, build flag set |
| `star format FILE... [--validate]` | Format starfiles | |
| `star parse [FILE...]` | Syntax check (CI-only); expects ws layout with devops artifact | `--project`, `--repos-mapping` |
| `package generate-config` (`generate`) | nfpm configs from package definitions | `-P`, `--packages-definitions`, `--nfpm-template`, `--pkg-version` (ws manifest version or 0.0.1), `--key-file` (PGP sign), `--output-dir`, `--out` |
| `package create` | deb/rpm via nfpm | `-p deb\|rpm`, `-f` (nfpm.yaml), `--from-file`, `-t` (generated/), `--archive[-name]`, `--hashes-file` |

Buckets: `s3-github-action-package` (CI artifacts/manifests, default), `s3-collab-artifacts`
(collab: `upload-patch`, `manifest export --upload`, `remote-build --from-*`).

Reproduce DEPENDS_ON locally:
```bash
octl do -p iridium --deps ip.infra.sw.drv.pci:feature/dma,ip.infra.sw.sim:feature/dma --override-path <abs-dir> --init
octl do -p iridium --deps ... --override-path <abs-dir> --build
```

Source: octl/octl_artifacts*.md, octl/octl_manifest*.md, octl/octl_depends-on.md,
octl/octl_star*.md, octl/octl_package*.md, how-to/reproduce-ci-failures-locally.md, `--help` (v0.213.7).

---

## 8. Copilot plugins (`octl plugins`)

Manages GitHub Copilot **CLI** plugins from the **ai-toolkit** marketplace
(`module.infra.ai_group.toolkit`). Defaults per workspace come from
`.github/plugin/workspace-defaults.json` in the toolkit (`swGroupDefaults` +
`workspaceDefaults` keyed `<project>/<manifest>` → `<manifest>` → `*`, `teamDirectories`).
No list is compiled into octl; add your ws via PR to that file.

| Command | Purpose | Flags |
|---|---|---|
| `plugins manage` (`install`) | Install the ws's plugins; switching ws removes the previous ws's team/default plugins | `--plugins a,b`, `--preset`, `--save-preset`, `--list-presets`, `--delete-preset`, `--keep-plugins`, `--flatten` (write into ws `.github` instead), `--dry-run`, `--yes`, `-I` |
| `plugins update` | Update what the marketplace lists today; reconcile shipped instruction files (hand-edited left alone) | `--only a,b`, `--dry-run` |

Hooks: `ws init` runs update then install (non-interactive unless `-I`; flag is `--plugin-preset`,
not `--preset`); `ws sync` refreshes every time and moves plugins if ws changed. Both only
**warn** on failure; no-ops on `CI=true`. Opt out: `plugins: {enable: false}` in `~/.octl.yaml`
or `--no-plugins`. Self-installed / other-marketplace / `--plugin-dir` plugins are never touched.
Env: `COPILOT_BIN`, `COPILOT_HOME` (`~/.copilot`), `COPILOT_INSTRUCTIONS_DIR`, `GITHUB_TOKEN`/`GH_TOKEN`.
Exit codes: 0 ok, 1 error, 2 usage, 3 Copilot CLI not found, 4 aborted, 5 some plugins failed,
6 Copilot home not writable. Restart Copilot CLI to pick up changes.

Source: how-to/manage-copilot-plugins.md, `octl plugins manage|update --help`, `octl ws init|sync --help` (v0.213.7).

---

## 9. VMs (`octl vm`) and workloads (`octl wl`)

Prereqs: office network/VPN; key loaded in ssh-agent (`ssh-add ~/.ssh/id_ed25519`); connect
with `ssh -A <PrivateIP>`. Agents: set `OCTL_NON_INTERACTIVE=1`, use `--wait`, parse JSON.

| Command | Purpose | Key flags |
|---|---|---|
| `vm create` | Personal EC2 from base AMI (octl + creds tooling preinstalled) | `--flavor`, `--list-flavors`, `--lease` (default 8h, max 72h), `--disk` GB, `--features kvm`, `--ami`, `-d`, `-t k=v` (reserved: owner, Name, monitor, lease_expires_at, ...), `-i/--identity` (`~/.ssh/id_ed25519`), `--replicas`, `--placement-group`, `--scale-up`/`--scale-out`, `--node-selector`, `-w/--wait` (default true) |
| `vm list` | Your instances (LEASE ENDS column, absolute times) | `--all`, `--owner`, `-i ids`, `--min-cores`/`--max-cores`, `-m/--max-results` (10), `--output table\|json`, `-t/--terminated` |
| `vm start ID...` | Start + fresh lease | `--lease`, `-w` |
| `vm stop ID...` | Stop | `-w` |
| `vm delete ID...` | Terminate | `-A/--all`, `-w` |
| `vm lease renew ID...` | Deadline = **now + `--lease`** (default 8h, max 72h) | repeatable; list every replica |
| `wl create` (`c`) | EC2 / kubevirt VM / pod from a flavor; kind defaults by flavor location | `--flavor`, `--kind vm\|pod\|ec2`, `--replicas`, `--placement packed\|spread` (required for >1 k8s replica), `--image`/`--command` (pod), `-n`, `--location`, `--lease`/`--ami`/`--features` (EC2), `--user-data-secret`, `--maintenance`, `-l`, `-o`, `-w` |
| `wl list` (`ls`) | Your workloads | `--kind`, `--ids`, `--owner`, `-A`, `--min/--max-cores`, `--terminated`, `-o` |
| `wl delete` (`d`) | Delete yours only | `--ids`, `--kind`, `-A`, `-w` |

- `--features kvm`: nested virtualization; user in `kvm` group (QEMU 5–20× faster than TCG).
  Requesting an unsupported feature errors and names flavors that offer it.
- Ocean (`OCTL_FEATURE=ocean-vm`): flavors `a1.small`…`a1.4xlarge` (A1 chips/sleds),
  `dgx.small`…`dgx.large` (B200). On Ocean `--lease`, `--disk` (fixed 100Gi), `--ami`,
  `--features` are rejected/ignored; `vm create --replicas` creates 1 → use `octl wl`.
  Kube config `~/.kube/ocean.yaml`, context `ocean`. Ctrl+C during `--wait` deletes the VM.
- Typical flavors: `c7i.16xlarge` (build), `m7i.48xlarge` (test). Launch-template list
  (`general-debug-flow`): t2.medium/2xlarge, c6i.8xlarge, c7i.4/8/12/16xlarge, c8i.8/12/16xlarge,
  inf2.xlarge, m7i.16/48xlarge, m8id.48xlarge, m8g.48xlarge (arm64), c7i.metal-24xl.

Source: octl/octl_vm*.md, octl/octl_workloads*.md, how-to/create-ec2-vm-instance.md,
agents/octl-vm.md, `octl vm|wl <sub> --help` (v0.213.7); Confluence 1720909943.

---

## 10. VM operational policy

| Topic | Rule |
|---|---|
| **Lease** | Every octl-created personal VM has an absolute deadline; at expiry the VM is **stopped (not terminated)**. Default 8h, max 72h (longer = rejected, not truncated). Email 1h before expiry with renew command. `vm start` grants a fresh lease. VMs created outside octl / pre-lease VMs have no deadline and are never auto-stopped. Replaced the old low-CPU-for-1h monitor. |
| **Keep-alive** | `--keep-alive` is deprecated: accepted, does nothing, prints warning. Flavor restriction and one-per-user limit are gone. Use `octl vm lease renew <ID> --lease 72h`. |
| **Personal budget** | Default **$50/month** of running time, resets on the 1st; **enforced** — over limit, octl won't create VMs. Check/request at Platform portal `https://portal.ai.elmnt.local/runway` (Budget). One pending request; max 2× current; approvers: AI `portal-budget-approvals-ai-sg`, Validation/System `portal-budget-approvals-validation-sg`, rest of SW `portal-budget-approvals-sw-sg`. |
| **Stopped cleanup** | Stopped continuously **60 days → terminated** (instance + root volume, unrecoverable). No exemption tag. Any start resets the clock. Check stop time: `aws ec2 describe-instances --instance-ids <ID> --query 'Reservations[].Instances[].StateTransitionReason'`. Delete unused VMs yourself. |
| **Grow disk** | `aws ec2 modify-volume --volume-id <root vol> --size <N>` with `role-ec2-basic` creds; **size only, max 500 GB**, grow-only, ≤4 mods/24h; wait for `optimizing`, then `sudo reboot` (cloud-init grows FS) or `growpart` + `resize2fs`/`xfs_growfs`. Find root vol via `RootDeviceName` (not always `/dev/sda1`). |
| **NVMe instance store** | `m8id.*` (8x/48x/96xlarge): local NVMe auto-assembled (RAID0 `/dev/md0` if several) as XFS at `/mnt/nvme-instance-store` (cache dir `.../cache`, Docker data-root `.../docker`). Survives reboot; **wiped on stop/start/terminate**. Bind-mount into containers; guard with `[ -d /mnt/nvme-instance-store/cache ]`. |
| **User persistent storage** | EFS access point created on first `vm create`; mounted at `/mnt/<USERNAME>` on new VMs only (recreate older VMs). Survives VM deletion; personal but **not private**; **no secrets**; no backup; not for build cache (NFS, billed per GB stored+transferred); not mountable in WSL. Cross-user sharing: `/mnt/shared_cache`. |
| **EFA VMs** | `octl vm create --flavor c5n.9xlarge --replicas 2 [--placement-group]`; EFA flavors: c5n.9xlarge, c6i.32xlarge, m7i.48xlarge, m8id.96xlarge. Verify `fi_info -p efa`, `fi_pingpong -p efa`. Placement groups may slow provisioning. |
| **Launch template (console)** | Role `role-group-sw`; template `general-debug-flow`; subnet `sw-private-<az>` only, **no public IP** (else `UnauthorizedOperation`); use existing SG; set unique `Name` tag. |
| **Flex vs non-flex (CI runners)** | Flex = one runner label backed by several instance types (better availability/scale-out; e.g. `cxi.8xlarge` label next to `c7i.8xlarge`). Non-flex = label → one fixed type (use when you need consistent perf / specific CPU / family features, e.g. `inf2`). Chosen by label config, not workflow. |
| **Spot reclaim** | CI runners are EC2 Spot; interruption shows as a job dying mid-step. Confirm with `octl wf sherlock <job URL>`; just re-run (portal queue auto-retries spot failures with +1 priority). Avoid with `.on-demand` label suffix in `ci-config.yaml`; `no-fail-fast` PR label lets other jobs finish so only failed jobs need rerun. |

Source: how-to/vm-lease.md, how-to/keep-vm-alive.md, how-to/personal-vm-budget.md,
how-to/stopped-vm-cleanup-policy.md, how-to/grow-vm-disk.md, how-to/use-nvme-instance-store.md,
how-to/user-persistent-storage.md, how-to/request-efa-supported-vms.md,
how-to/create-ec2-from-launch-template.md, workflows/faq.md, workflows/portal-queue.md,
workflows/ci-workflow-overview.md; Confluence 771162115, 1169293413; `octl vm create --keep-alive --help` (v0.213.7).

---

## 11. Security scans, devcontainer, VS Code

**Coverity** — `octl ws security coverity` (`ws sec cov`) `[repos...]`: reuses the exact
`ws build` infra (starfiles, artifacts, images, mounts).
Modes `--mode build` (default; rebuild wrapped in `cov-build` + `cov-analyze`),
`incremental` (`cov-run-desktop`, source-only, fast), `results-only` (query Coverity Connect;
needs token). Flags: `--check-set owasp-web-top-10-2025|cwe-top-25-2024`, `--fail-on low|medium|high`
(gates **internal** findings only), `--severity`, `--show all|internal|external`,
`--format table|json`, `--format-file`, `--submit` (to gatestage; `COVERITY_GATESTAGE_URL`
overrides; identity = git `user.email`).

**BlackDuck** — `octl ws scan` (inside a ws, on company network). Token priority: `--token` →
`BLACKDUCK_TOKEN` → AWS Secrets Manager. Project naming: CI `<project>/<manifest>/<revision>`,
local `<username>/<project>/<manifest>/<revision>`, version = manifest version. Tunables via
env (`SEARCH_DEPTH`, `DETECT_TOOLS`, `DETECT_EXCLUDED_DETECTOR_TYPES`, `BINARY_SCAN_PATTERNS`, ...).
`--debug` prints the docker command. Results appear in Platform portal (BlackDuck Scans), kept 1 year.

**Devcontainer** — `octl ws generate-dev-container` → Dockerfile + devcontainer.json matching
the `ws debug` container; then VS Code "Dev Containers: Rebuild without cache and Reopen in Container".
VS Code → AWS VM: copy WSL SSH keys to `C:\Users\<user>\.ssh`, add host in Windows SSH config,
use Remote-SSH.

Source: how-to/coverity-static-analysis-octl.md, how-to/scan-vulnerabilities-blackduck.md,
how-to/generate-devcontainer-config.md, how-to/connect-vscode-wsl-aws-vm.md,
`octl ws sec cov --help`, `octl ws scan --help` (v0.213.7).

---

## 12. Design notes — "octl second iteration"

Problems identified: (1) runtime-dependency build order (overrides must build in DAG order,
each ws built once — the "main ws" was rebuilt at the end; dependents must receive runtime
artifacts of built ws); (2) **Taskfile** too limited (conditionals; no control over container
options; no commands outside the container); (3) static GitHub creds; (4) adding a
project/kernel/OS option needs slow manual artifact builds; (5) no user custom images.
Decisions: build order from the manifests' `.repo` dir (not a build_tree file); DAG build, copy
each built ws's artifacts into dependent overrides; a **build-only mode** using only build-type
artifacts, then test from a modified manifest; **Starlark testing framework** (imports via
`load`, `defer` cleanup, per-task container options, in/out-of-container commands, "closest
image option wins", test suites with pre/post); use the `gh` CLI token for GitHub API. This is
why current docs say "starfile" while some help text still says "Taskfile".

Source: octl/octl_second_iteration.md.

---

## 13. Doc-vs-CLI drift (verified against v0.213.7)

| Item | Docs say | Live CLI |
|---|---|---|
| `octl ws build --kernel <K>` | FAQ, build help *examples*, pldm-job-queue.md flag table, starfile.md | `ERROR: unknown flag: --kernel` — flag no longer exists (example text is stale) |
| `octl aws auth` | octl/octl_aws_auth.md; build-and-test-remotely.md prereq "`octl aws auth`" | `unknown command "aws"`; use `aws sso login --profile devops-cli` / `octl login` |
| `octl sherlock <URL>` | workflows/faq.md | `unknown command "sherlock"`; correct: `octl wf sherlock <job URL>` (job URL, not run URL) |
| `ws test --list-groups` | `ws test --help` example | deprecated → `-L/--list-configuration` |
| `ws init --s3-only` | old docs | deprecated → `--artifacts-only` |
| `vm create --keep-alive` | Confluence 1169293413 (old) | deprecated no-op; use `--lease` |
| `vm create` long help: "You can have only a single EC2 instance" | — | contradicts lease/budget docs (one-per-user limit removed; budget enforced instead); treat help sentence as stale |
| `ws build --target` | help: `"pldm" (required with --remote or --local)` | FAQ/help example still show bare `octl ws build --remote` |
| `--save-as` | pldm doc: only valid with `--remote` | help: tied to `--interactive` answers |
| `ws unit-test` / `ws exec` | help says "Taskfile"/"task file" | actually starfile `unit_test()` / starfile functions |
| `star parse` path | help: `build_artifacts/opts/atl/devops/` | starfile.md: `build_artifacts/opt/elmnt/devops/project.star` |
| `ws unit-test <repo>` | team usage `octl ws unit-test ip/mng` | usage line shows no positional arg (scoping by repo not documented) |
| Confluence 102994235 | `octl workspace init -u <url> -m neon/default.xml`, `elmcli/docs` links | `-u` doesn't exist; use `-p/-m`; docs in `ip.infra.devops.apps/docs/octl` |

Source: `octl <cmd> --help` probes (v0.213.7) vs cited docs.

---

## 14. Troubleshooting / error strings

| Symptom / string | Cause → fix |
|---|---|
| `--override-path must be an absolute path` | Use `$(pwd)/...` or `/home/...` |
| init flag-combination error | One mode only: `-p`+`-m` / `-I` / `--github-job-url`; `--dir`, `--version`, `--github-job-url` exclusive |
| `workspace requires all changes to be committed and pushed.` / `...all commits to be pushed.` | `generate-ci-data`, `remote-build`, `--remote --target pldm` read from GitHub → push, or use `--from-source` / `--local` |
| `invalid ci_data: [i]: json: unknown field ...` | Fix the field; `octl ci-data validate` |
| `CI data has <N> entries; pick one with octl ci-data select <index>` | Insert `ci-data select <i>` |
| `default stage has <N> jobs; octl ws remote-build requires exactly one` | Run the printed pipe with the job index |
| Test-only run rebuilds | Add `--skip-build` to `set-manifest` |
| Job stays *Queued* | No runner pool matches labels |
| Build unexpectedly cleaned `build_artifacts` | Manifest artifacts changed, or `--build-type` switched; use `-S all` to avoid |
| Prompts hang in scripts/agents | `OCTL_NON_INTERACTIVE=1` |
| No network / hangs on AWS | `OCTL_OFFLINE_MODE=1` (fails fast instead) |
| `push`/AWS calls fail after ~12h | SSO session expired → `aws sso login --profile devops-cli` |
| `path escapes from parent` in a starfile `load` | `load` path resolves outside ws root |
| PR label with `'` breaks CI before starfile runs | Avoid quotes/metachars in labels read via `pr_labels` |
| Job died mid-step, no error | Spot interruption → `octl wf sherlock <job URL>`; re-run |
| `vm create` refused | Budget exceeded (portal Runway) / not on VPN / ssh key not in agent |
| `UnauthorizedOperation` (EC2) | Disk modify with non-size params or wrong role; console launch in public subnet/public IP |
| Plugins: exit 3 "not installed" / "only VS Code's launcher" | Install Copilot CLI (`npm i -g @github/copilot`) or set `COPILOT_BIN` |
| Plugins: exit 6 "cannot write to ..." | `~/.copilot` owned by root after `sudo copilot` → fix ownership |
| Plugins: "no GitHub credentials found" | `gh auth login` or `GITHUB_TOKEN` |
| More detail needed | `--verbose`; read `~/.octl.log` |

Source: octl/workspace-faq.md, how-to/build-and-test-remotely.md, how-to/use-octl-ci-data.md,
how-to/manage-copilot-plugins.md, how-to/ci/starfile.md, how-to/grow-vm-disk.md, workflows/faq.md.

---

## 15. Pitfalls

- `--args` is opaque: octl never validates it; a typo (`varient=asic`) silently does whatever
  the starfile does with unknown keys. Check the repo's `.star` for accepted keys.
- Build the **whole ws once** before `octl ws build <repo>`; single-repo builds rely on
  `build_artifacts` from dependents.
- `--build-type` is sticky; switching wipes `build_artifacts`. `ws sync` + build may auto-clean.
- `-S all` skips the auto-clean but keeps the stored checksum — the next normal build cleans.
- `-O NAME:file://PATH` needs the **workspace root** (absolute), not a repo dir; after
  override builds, `octl ws clean` to restore. Shared artifact skew across ws → `--align-artifacts`.
- `ws exec` runs starfile **tasks**, not shell commands; use `ws debug` for a shell.
- Changing `ws debug` mounts or privileged mode requires `--remove`.
- `--remote` and `ws generate-ci-data` build what is on **GitHub**, not your local edits;
  `--local --target pldm` and `remote-build --from-source` use local state.
- `--local --target pldm` requires a prior successful local `octl ws build`.
- `vm lease renew` sets now+duration (can *shorten* a lease); pass every replica.
- Instance store is wiped on stop/start (incl. lease expiry → stop!). Persistent storage is EFS, slow, not private.
- Stopped ≠ safe: 60-day termination. Budget counts running time only, but storage is billed while stopped.
- `ws init`/`ws sync` may uninstall Copilot plugins when switching workspaces → `--keep-plugins` or `--no-plugins`.
- Many PR-level / CI-only context fields (`pr_labels`, `pr_branch`, `changed_files`) are empty
  locally and in on-demand runs unless set via `ci-data set-additional-context`.
- Don't trust example text in `--help` blindly (`--kernel`); probe flags.

Source: synthesis of sections above.

---

## 16. Owner / escalation

- **Owner:** Platform team (`Elmnt-Internal/sw-platform-sg`); code in
  `Elmnt-Internal/ip.infra.devops.apps` (`octl/cmd/*`, config `octl/cmd/config/config.{yaml,jsonnet}`),
  generated docs `docs/octl/`. Project starfile: `Elmnt-Internal/module.devops.configuration`.
  Manifests: `Elmnt-Internal/ip.infra.sw.manifests`. Workflows: `ip.infra.devops.workflow-entry`.
- **Support:** ask **PlatformDevBot** (private chat or *Platform - hub* Teams channel) first;
  then mention `@Platform-Team` in *Platform - hub*. News: *Platform - news*.
- **Requests:** service requests → Jira **DRR**; bugs/features → Jira **DEV**. Custom image
  additions → Jira to DevOps. Budget increases → portal Runway (approver groups in §10).
- Attach `~/.octl.log`, the exact command, `octl version`, and the GH job URL.

Source: how-to/contact-us.md, how-to/ci/starfile.md, front-matter `owner:` of how-to docs; Confluence 102994235.
