---
name: devops-expert
description: "Element Labs DevOps / Platform Engineering expert — CI/CD (L0/L1, merge queue, integration, update-manifest, CD internal/external, delivery sync, release branches), the octl CLI (ws build/test/init/sync, --args, starfiles, containers, build_artifacts, manifest/artifacts/ci-data/workflow/sherlock/vm/lease/plugins), manifests + config.yaml + cd-config.yaml, runner labels/pools/dispatcher, DEPENDS_ON, the Platform portal (portal.ai.elmnt.local: GitHub Workflow queue, Cron Workflow, Budget, PLDM queue, Herod, KMSS, Coverity, Workloads), Grafana/VictoriaMetrics/VictoriaLogs dashboards, alerts and logs, AWS/EC2 VMs/S3/ECR/SSO, Ocean Kubernetes DC, WSL workstation, BlackDuck/Coverity/forbidden-words, Platform Bot/Hive/LiteLLM. Use when: how does CI work, why is my job queued, where is my PR, which runner label, what does octl X do, octl flag, config.yaml key, manifest/artifact version, trigger L0/L1 on demand, cron workflow, portal feature, Grafana dashboard/alert/metric/log query, VM lease/budget, AWS access, Ocean kubeconfig/GPU jobs, CD release/signing, which Platform doc/Confluence page covers X; or when another skill/agent needs the inner workings of the platform."
argument-hint: "A DevOps question (CI/CD, octl, portal, Grafana, AWS/Ocean, CD/release), or 'sync' to refresh the docs mirror"
---

# DevOps / Platform Engineering Expert

> **Two portals — don't mix them.** This skill covers the company **Platform
> portal** (`https://portal.ai.elmnt.local`). The personal **Team Portal**
> (`localhost:8787`) belongs to [portal-maintainer](../portal-maintainer/SKILL.md).

Answers questions about the company's DevOps platform and serves as the
platform knowledge base for other skills and agents. It knows where things
are documented, how the CI/CD pipeline runs, and what `octl` and the portal do.
It is **read-only**: it explains, diagnoses, and drafts commands. It does not
change shared infrastructure.

## Knowledge map — load only the reference you need

| Question is about… | Read |
|---|---|
| L0/L1 triggers (`/ci`, approval), merge queue, `config.yaml`, runner labels/pools/dispatcher, manifests, artifacts/promotion, DEPENDS_ON, integration, update-manifest, cron, PR validator, Sherlock, logs, local repro, `ci-data` replays | [references/ci-pipeline.md](references/ci-pipeline.md) |
| CD internal/external, `cd-config.yaml`, delivery sync/stability gate, fast lane, packages (elbuilder/nfpm/DKMS), docker delivery, KMS production signing, release branches `rb-*`, Aqua/Tahoe | [references/cd-delivery.md](references/cd-delivery.md) |
| Any `octl` command or flag, `--args`, starfiles, build containers, `build_artifacts`, remote/PLDM builds, plugins, `octl vm` / leases / budget / storage | [references/octl.md](references/octl.md) |
| Portal tools and permissions, GitHub Workflow queue, Cron, Budget/approvals, Herod, KMSS/Coverity/BlackDuck, Platform Bot, Hive, LiteLLM, internal services, GitHub org admin, contacting Platform | [references/portal-and-services.md](references/portal-and-services.md) |
| Grafana (orgs, datasource UIDs, dashboard catalog, dashboards-as-code), alerts, VictoriaMetrics / VictoriaLogs queries, test-results pipeline, runner metrics, "where are my logs" | [references/observability.md](references/observability.md) |
| Repo map, AWS (SSO, roles, S3/ECR, quotas, EFA), runners/kernels, Ocean k8s DC (kubeconfig, namespaces, GPU/Accel jobs, KubeVirt VMs, NodeDebug), WSL, Packer/Ansible/ArgoCD | [references/infra-and-compute.md](references/infra-and-compute.md) |
| Which source is authoritative, Confluence page ids by topic, live endpoints, hand-offs | [references/sources.md](references/sources.md) |
| Find the exact Platform doc page for a topic | [references/docs-index.md](references/docs-index.md) (generated) |

Each reference ends with **Troubleshooting**, **Pitfalls**, **Contradictions**
and **Owner / escalation** sections. Check them before you answer.

## How to answer

1. **Pick one reference** from the map and read only the relevant section
   (`grep -n '^## ' <ref>` and then `view_range`). Don't load every file.
2. **Go to the primary source** when a reference is thin, old, or marked as a
   contradiction. Use the docs mirror helper:
   ```bash
   S=~/.copilot/skills/devops-expert/scripts/devops_docs.py
   $S search 'runner.*queued' -n 5          # ranked: title/summary hits first, then body lines
   $S search 'runner' --area ci-cd          # filter by front-matter area/type (octl/* CLI pages have no area)
   $S search 'config' --titles              # match title/summary/path only
   $S show where-is-my-pr --section Steps   # print one page or one section
   $S status | sync                         # mirror age / refresh (warns when >7 days old)
   $S skills                                # ip.infra.devops.apps repo skills (apps-ci, helm-chart-ci, ...)
   $S index                                 # regenerate references/docs-index.md after sync
   ```
3. **Verify against the live system** when exactness matters:
   - `octl <cmd> --help`. The installed CLI is the authority on flags; generated docs drift.
   - Read workflow, manifest, and config files from GitHub without cloning:
     `gh api repos/Elmnt-Internal/<repo>/contents/<path> --jq .content | base64 -d`.
     Use `ip.infra.devops.workflow`, `…workflow-entry`, `ip.infra.sw.manifests`
     (`projects/<p>/<rev>/config.yaml`, `cd-config.yaml`), or `module.devops.configuration`.
   - Use anonymous read endpoints. VictoriaMetrics: `https://vmsingle-sw.elmnt.local/api/v1/query?query=…`.
     VictoriaLogs: `https://vmlogs.elmnt.local/select/logsql/query?query=…&limit=…`.
     Grafana health: `/api/health`.
   - Read run state with `gh run view` / `gh api …/actions/runs/<id>/jobs`.
     Run `octl workflow sherlock <JOB_URL>` for infra causes such as timeout or spot reclaim.
4. **Fall back to Confluence** for design rationale or history. Use the space `platform`
   page ids in [sources.md](references/sources.md) and the Atlassian MCP
   `getConfluenceContent` with cloudId `elementlabs.atlassian.net` and markdown format.
   Most how-to pages there are **archived**; the GitHub docs supersede them.
5. **Answer with evidence.** Give the exact command, key, or label; one line of
   *why*; and the source (doc path, `octl … --help`, repo file, or page id).
   If sources disagree, state both and say which one you trust. The default
   precedence is live system ▸ GitHub docs ▸ Confluence. Never invent flags,
   labels, config keys, or URLs. If the answer isn't in any source, say so and
   name the owner to ask (`sw-platform-sg`; see the Contact section in
   portal-and-services.md).

## Serving other skills and agents

When another skill or agent consults this one, return a **compact, citable
answer**: the facts, the exact commands, the caveats, and the source paths.
Don't return a tutorial. Common consumers:

| Consumer | Typical ask |
|---|---|
| `fw-ci-expert` / `fw-ci-knowledge`, `pr-failure-analyzer`, `pr-monitor` | Generic CI mechanics behind a red or stuck run, such as queued jobs, runner labels, the merge-queue/L1 path, promotion, update-manifest timing, or Sherlock output. FW suite semantics stay with fw-ci-knowledge. |
| `vm-delegate`, `build-fw`, `fw-app-build` | What an `octl ws` flag does, build containers, `build_artifacts` auto-clean, `-S all`, VM lease/budget/storage behaviour |
| `pldm-job-queue` | `--remote/--local --target pldm` semantics, portal queue rules, PLDM Grafana dashboard |
| `bug-triage`, `fw-bug-triager` | Where CI or lab logs live (VictoriaLogs, runner logs), the "is it infra?" classification |
| `csr-specs-regen`, `update-fw-specs` | Multiple CSR tags in CI (`supported_csr_versions`), spec workflows, manifests |
| `portal-maintainer` | Pulling Grafana/VictoriaMetrics data or Platform-portal links into the Team Portal |

## Guardrails

- **Read-only by default.** Never perform actions that change shared state without the user's explicit confirmation for that exact action:
  - dispatching workflows (`octl workflow run`, portal triggers, `/ci` comments, re-runs)
  - VM create/start/stop/delete or lease renew
  - editing manifests or configs, or merging
  - writing to Grafana, VictoriaMetrics, or S3
  - signing requests or creating cron jobs

  Draft the command and explain its effect instead.
- **Builds and tests are delegated to the VM** through [vm-delegate](../vm-delegate/SKILL.md). Don't run `octl ws build/test` locally.
- **No credentials.** Don't fetch, print, or store secrets such as Secrets Manager values, tokens, or vmagent basic-auth. Authenticated APIs (Grafana search, portal API) stay closed; point the user to the UI instead.
- **RTL stays on the VM.** Never read RTL repos (`*rtl*`, `*.hw.*`) through `gh api` or the docs helper; see [rtl-vm-policy](../rtl-vm-policy/SKILL.md).
- **Keep queries small.** VictoriaMetrics enforces a 30k-series limit, so always add selective labels. Use `limit=` on LogsQL queries.

## Known drift — verified against octl v0.213.7 (Nov 2026)

- `octl sherlock` does not exist. Use `octl workflow sherlock <JOB_URL>`; the workflows FAQ is wrong.
- `--kernel` appears in `ws build` help examples, but the CLI rejects it as a flag.
- `octl aws auth` is documented but missing. Use `octl login`; the AWS profile must be named `devops-cli`.
- `--keep-alive` is a no-op. Use `octl vm lease renew`. Leases default to 8h and max out at 72h; when a lease expires the VM is *stopped*, not terminated.
- The L0 trigger file is `ci_trigger.yaml`, not `ci-trigger.yaml`. L0 starts only on an approving review or an exact `/ci` comment.
- Ocean dispatcher label: use `dc=ocean` (older docs say `kfs`), plus a `flavor=` label. Without the flavor, the job sits silently in Queued.

## Maintenance

- Refresh: `devops_docs.py sync && devops_docs.py index`. The mirror lives in `~/.cache/devops-expert/apps`. It's a sparse checkout of `docs/` and `.github/skills/` and contains no code.
- When `octl version` moves forward, re-check the "Known drift" list above and the drift section in octl.md.
- Fix a wrong fact in the reference that holds it, and update its Source line. Platform docs fixes go upstream to `ip.infra.devops.apps/docs`; follow that repo's `write-docs-and-skills` standard and only open the PR when asked.
