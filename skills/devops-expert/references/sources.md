# DevOps knowledge sources — where truth lives

Precedence when sources disagree: **live system** (`octl --help`, workflow YAML,
manifest repo, portal) ▸ **GitHub docs** (`ip.infra.devops.apps/docs`) ▸
**Confluence `platform` space** (many pages archived) ▸ meeting notes.
Say which source you used and flag conflicts instead of silently picking one.

## 1. Maintained docs (primary)

| What | Where |
|---|---|
| Docs source (Markdown, front matter `area/type/owner/summary/last_reviewed`) | `Elmnt-Internal/ip.infra.devops.apps` → `docs/` |
| Published site (GitHub-SSO gated) | https://platform-engineering-docs.elmnt.com/ (old: `elmnt-internal.github.io/ip.infra.devops.apps` → redirects) |
| Local read-only mirror | `~/.cache/devops-expert/apps/docs/` — managed by `scripts/devops_docs.py sync` |
| Generated page index | [docs-index.md](docs-index.md) |
| Docs writing standard (for contributors) | `docs/_standard.yml`; repo skill `write-docs-and-skills` |
| Repo-local agent skills (apps-ci, helm-chart-ci, platform-map, node-debug-controller, deploy-service, …) | `ip.infra.devops.apps/.github/skills/*` — list with `devops_docs.py skills` |

Doc `area` values: `ci-cd`, `delivery`, `octl`, `portal`, `platform-services`,
`observability`, `aws-infra`, `hardware`, `agentic`, `customer-tools`, `github-iac`.
`docs/octl/*` is auto-generated from the CLI (`make -C octl docs`) — the
installed `octl <cmd> --help` is at least as authoritative.

## 2. Platform repositories (code is the final authority)

| Repo | Owns |
|---|---|
| `ip.infra.devops.apps` | `octl`, `github-runner`, portal (web + platform-bff, gatestage, herod), services, k8s/ArgoCD manifests (`zarf/k8s/...`), Grafana alert values, docs |
| `ip.infra.devops.workflow` | Reusable CI/CD workflows (`ci.yaml`, CD, integration, update-manifest) |
| `ip.infra.devops.workflow-entry` | Entry workflows repos call; dispatch target for on-demand runs |
| `ip.infra.sw.manifests` | Manifest XMLs + per-project `config.yaml` (`projects/<project>/<revision>/…`) |
| `ip.infra.devops.docker-images` | CI/dev container images |
| `ip.infra.devops.provisioning` | Packer AMIs, Ansible |
| `module.devops.configuration` | Per-repo CI config |

See `docs/repositories.md` (and [infra-and-compute.md](infra-and-compute.md)) for the full map.
Read single files with `gh api repos/Elmnt-Internal/<repo>/contents/<path> --jq .content | base64 -d`.

## 3. Confluence space `platform` ("R&D Platform Engineer")

Root: https://elementlabs.atlassian.net/wiki/spaces/platform/overview (page 18744889).
~500 pages; the how-tos under page 1332085282 were **migrated to GitHub docs and
are no longer maintained**. Still useful for designs, decisions and history.
Read with the Atlassian MCP `getConfluenceContent` (cloudId
`elementlabs.atlassian.net`, `content_format: markdown`, `detail: full`) or
search with CQL `space = "platform" AND title ~ "<term>"`.

| Topic | Page ids (title) |
|---|---|
| Onboarding | 167247885 (Platform Team Getting Started), 63897704 (Zero to Hero – Dev Workspace/octl), 132124552 (Zero To Hero – WSL), 1720189102 (Zero To Ocean, parts 1–6: 1721434225, 1719632169, 1721303110, 1719927045, 1720713356, 1720909943) |
| CI concepts | 53149766 (L0 & L1 – Two-Step CI), 53248089 (Merge Queue), 50004162 (Depends-On), 253298696 (Why/When depends-on), 261751225 (Integration Workflow), 53939586 (Update Manifest), 264634369 (CI CONFIG – V2), 596837703 (CI Config & simplified starfile proposal), 1002831909 (L0/L1 on demand), 1385824336 (On-demand runs on custom manifest), 1597669386 (Unified remote build + test), 1599602715 (Reducing GH runner pools) |
| Manifests / artifacts | 74121892 (Manifest File), 194085176 (Multi Branch Manifest Creation), 612040839 (Mirror manifests), 1331561454 (Per-Manifest Config Sidecar), 99026051 (Artifacts for Manifest Building), 185073665 (Starfile) |
| CD / release | 1224933486 (CD at Element), 1162084492 (CD Internal guide), 1165328462 / 1168998471 (CD External flow / user guide), 1126269111 (cd-config.yaml), 692781319 (CD Release Process), 692748452 (CD Packages & Container Images), 526549027 (CI→Integration→CD + DKMS — authoritative update), 1138623072 (elbuilder), 942604342 / 856784900 (KMS production signing), 1422360923 (Release branch vs main) |
| Observability | 169443335 (Grafana), 794853403 (Victoria Logs), 1075380225 (Victoria Metrics), 792494146 (Vector), 1125843167 (Grafana alerts), 941883447 (EC2 Monitoring), 99025264 (GitHub Monitoring), 1611792573 (stability & monitoring action items) |
| Portal / services | 535756802 (DevOps Portal), 884375553 (Cron via portal), 1735557362 (Cron approval groups & budget), 1507524736 / 1558544486 (LiteLLM gateway / configure), 339640322 (Notifications), 885031397 (PlatformDevBot), 881557509 (Contact us) |
| PLDM / Palladium CI | 1120927801 (PLDM Job queue), 1206583297 (Job Queue open issues), 228491521 (PLDM regression), 115507285 (PLDM specs & CI) |
| Security tooling | 528646367 (Black-Duck CI), 1367900205 (Coverity in CI), 999686218 (Coverity octl+CI+AI design), 484474905 (PR Validator + forbidden words), 535691268 (KMSS overview) |
| AWS / DC | 1661501664 (AWS upgrades), 760152593 (Kubernetes DC), 1317273617 (DC current status), 1698168861 (EKS → Karpenter), 1261469707 (Naming convention), 501678139 (AWS tags) |
| Status / news | 1436188747, 1064304641, 1645674687, 1646788645 (Platform 1–4 weekly status), 242779200 (Platform weekly status), 1294041469 (Open PRs) |

## 4. Live systems (read-only probes)

| System | URL | Anonymous? |
|---|---|---|
| Portal | https://portal.ai.elmnt.local | SPA; API needs SSO |
| Grafana | https://grafana.elmnt.local | `/api/health` only; rest needs auth |
| VictoriaMetrics (SW) | https://vmsingle-sw.elmnt.local (`/api/v1/query`, `/api/v1/label/__name__/values`) | yes, read |
| VictoriaLogs | https://vmlogs.elmnt.local (`/select/logsql/query`) | yes, read |
| Hive / Platform AI | https://hive.ai.elmnt.local, https://platform-bot-mcp.ai.elmnt.local | SSO |
| Bazel cache | https://bazel-cache.elmnt.local | — |
| Perfetto | https://perfetto.elmnt.local | — |
| Emulation catalog | https://emul-online.elmnt.local (`/api/status`) | — |
| Ocean k8s API | https://api.ocean.elmnt.local:6443 (also `ocean-staging`, `ocean-dev`) | kubeconfig |

## 5. Related skills / plugins (hand-offs)

| Need | Go to |
|---|---|
| FW test suites, FW CI failure classes, DEPENDS_ON/manifest for **firmware** | fw-soc `fw-ci-knowledge` skill / `fw-ci-expert` agent |
| Analyze a specific red PR (portal-integrated, pitfalls log) | `pr-failure-analyzer` skill |
| Submit/monitor PLDM jobs | `pldm-job-queue` skill (`pjq.py`) |
| Build on a VM / mirror workspace | `vm-delegate` skill (`mctl`) |
| Grafana alert rule procedure | marketplace plugin `platform-ops` → `grafana-alerts` (install via `octl plugins manage`) |
| Test metric → Grafana panel | marketplace plugin `i-want-a-grafana-dashboard` |
| AWS SSO/ECR/S3 procedures, platform CI summary | marketplace `platform-ops` → `aws-access`, `platform-context` |
| Platform Bot MCP auth | marketplace `platform-bot-mcp-auth` |
