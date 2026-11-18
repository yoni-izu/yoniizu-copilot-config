# Platform Portal, Services, Bots & Developer Tooling

**Summary.** `https://portal.ai.elmnt.local` is the Platform team's (owner `Elmnt-Internal/sw-platform-sg`) internal developer portal: SSO, a React SPA (`apps/web/platform`) behind **platform-bff**, with **gatestage** (Go + Postgres) as the API/queue backend for workflows, cron, budgets, approvals, PLDM, Coverity, BlackDuck and KMS signing.
Around it sit the read-only **Platform Bot** (portal / VS Code MCP / Teams), **Hive** (personal agentic workspace), a catalog of Copilot agents, internal web services (Perfetto, bazel-cache, Headlamp, LiteLLM, ...), security tooling (BlackDuck, Coverity, KMSS, forbidden-words scanner), GitHub org IaC and the Copilot plugin manager (`octl plugins`).
Public docs: https://elmnt-internal.github.io/ip.infra.devops.apps/ (source `ip.infra.devops.apps/docs`). Confluence space `platform` is mostly **deprecated → moved to GitHub docs**; prefer GitHub docs.
Local skills to reuse: **`pldm-job-queue`** (submit/monitor PLDM jobs via `pjq.py`) and **`vm-delegate`** (`mctl` remote builds on VMs).

## TOC
1. Portal access & sessions
2. Portal navigation — every tool
3. GitHub Workflow page (Portal queue)
4. Cron Workflow
5. Budget module & generic approvals
6. PLDM Job Queue (portal side)
7. Herod (CSR browser)
8. Security tooling (BlackDuck, Coverity, KMSS, forbidden words)
9. Backend stack & repository map
10. Platform Bot & platform-bot-mcp
11. Hive & Agentic Tasks
12. Platform Copilot agents catalog
13. Internal services catalog
14. GitHub org administration
15. Giving AI agents read-only access
16. Copilot plugins management
17. Contact the Platform team & Platform news
18. Pitfalls
19. Troubleshooting (symptom → fix)
20. Owner / escalation

---

## 1. Portal access & sessions
Source: how-to/use-platform-portal.md; architecture/portal-sessions.md, portal-sessions-reference.md, gatestage-auth-design.md; live probe (SPA HTML served).

| Item | Value |
|---|---|
| URL | https://portal.ai.elmnt.local (VPN / office network; Element Local CA trusted) |
| Login | "Sign in with SSO" → platform-bff OIDC → **Dex** (`dex.elmnt.local`) → corporate identity (Azure AD/Entra; Dex also has a GitHub connector for groups) |
| Session store | ElastiCache Valkey `platform-session-cache` → survives BFF deploys/restarts |
| Session lifetime | **Fixed 8 h**, no sliding refresh, no refresh token (Dex ID token TTL 24 h) |
| Group changes | Picked up only at **next login** (removed membership can linger ≤ 8 h) |
| Logout | Sets `platform_signed_out` cookie (12 h): auto-login suppressed, only the Sign-in button logs back in |
| Cookies | `platform_session` (HttpOnly, SameSite=Strict), `platform_oauth_<state>` (10 min), `platform_signed_out` (12 h) |
| Expired session | Read 401 → auto re-login (≤ 1 per 2 min per tab); write 401 → banner "Your session expired", page stays (no data loss) |
| Login errors | `/login?error=expired \| exchange_failed \| not_authorized \| session_error` |
| Session store down | BFF returns **503** (not 401) → don't re-login, wait/report |

Visibility is group-driven: each nav module is gated by `requireModule` against the OPA policy `core/policy/access.rego`. **A missing tool = missing group**, not a bug → ask team lead / Platform (§20).

## 2. Portal navigation — every tool
Source: how-to/use-platform-portal.md, github-codeowners.md, cd-external/kms-production-signing.md, scan-vulnerabilities-blackduck.md, coverity-static-analysis-octl.md; Confluence 535691268 (KMSS).

**Catalog** (top nav) = card grid of every tool you can access. Inbox (bell) → **Approvals** tab (polls every 30 s).

| Nav group → Tool | What it does / how to use | Route / notes |
|---|---|---|
| General → **Home** | Landing page, shortcuts | `/` |
| General → **Platform AI** | Chat with read-only Platform Bot (§10) | ≤ ~7,800 bytes per question, 👍/👎 feedback |
| General → **System Status** | Platform services health (inferred; no user doc) | read-only |
| Modules → **Herod** | Browse CSR/register model per project+tag (§7) | `/herod` |
| Modules → **Budget** | Personal EC2 & cron-team budgets, request extensions, approver queue (§5) | `/runway`, `/budget/history` (approvers) |
| Workflows → **PLDM Job Queue** | View/cancel/delete Palladium jobs (§6) | `/catalog/pldm-job-queue` |
| Workflows → **Cron Workflow** | Create/edit scheduled CI builds (§4) | gatestage + pg_cron |
| Workflows → **GitHub Workflow** | Dispatch Depends-On, Update Manifest, Merge PR, CD, KMS signing... via Portal queue (§3) | `/catalog/github-workflow` |
| GitHub Admin → **GitHub Board** | Org GitHub overview board (inferred from name) | no dedicated user doc |
| GitHub Admin → **GitHub Settings** | Manage GitHub **teams** (Teams tab: Create Team / Edit Team) — used for CODEOWNERS teams | `/catalog/github-settings` |
| Security → **KMSS** | Key Management & Signing Server: per-project key lifecycle (generate/revoke/archive/destroy/sign by key index), approval queues, audit, dashboards; backed by AWS KMS, audit via Jira + CloudWatch | gatestage modules `kmss-signing-write`, `kms-sign` |
| Security → **Coverity Scans** | List of Coverity SAST snapshots per project/stream | §8 |
| Security → **Coverity Issues** | Coverity findings browse/triage | §8 |
| Security → BlackDuck Scans (card) | SCA scan results: GitHub (CI/CD) & Dev tabs, version compare, Refresh | `/catalog/blackduck/version?id=` |
| Cloud → **My Workloads** | Your own running resources (inferred; no user doc) | pair with `octl vm` / `vm-delegate` skill |
| Cloud → **Kubernetes** | Read view of Platform EKS workloads | deep view: Headlamp (§13) |
| Cloud → **AWS EC2** | EC2 instance view (personal/team VMs) | costs → Budget |
| Management → **Admin** | Portal admin (modules, users, agentic tasks) | admins (`sw-platform-sg`) only |

## 3. GitHub Workflow page (Portal queue)
Source: how-to/trigger-workflows-from-portal.md; workflows/portal-queue.md; Confluence 276825738 (older copy).

Nav: **Workflows → GitHub Workflow** (`/catalog/github-workflow`). VPN required from home. Every request is queued in gatestage and dispatched to `Elmnt-Internal/ip.infra.devops.workflow-entry` (runs visible at https://github.com/Elmnt-Internal/ip.infra.devops.workflow-entry/actions). Email on start and on completion.

| Workflow | Who | Key inputs / notes |
|---|---|---|
| Depends On | everyone | **Structured** mode (pick repo/branch/artifact) or **Legacy** raw `repo:branch,artifact:version`; PR URLs accepted |
| Single Update Manifest | everyone | bump one artifact version in a manifest |
| Merge PR | everyone | merge PR on `ip.infra.sw.manifests` |
| Integration | `sw-maintainers-SG` | **never type versions** — latest resolved from S3 at run start; "Include entire groups" ON by default |
| Mirror manifests | `sw-maintainers-SG` | |
| Code Porting | `sw-maintainers-SG` | |
| CD External Validation | everyone | |
| CD Release | per portal permissions | production release dispatch |
| New KMS Production Key Signing | KMS-authorised | gatestage `POST /api/v1/kms/sign-requests` → Jira ticket → approval → `kms_production_sign.yaml`; inputs project, component (e.g. `psoc-fw`), revision, version; output `s3://<bucket>/<project>/<component>/<revision>/signed-key-<N>-<version>/<manifest>.tar.zstd` |

Queue semantics:
- FIFO with **priority** (user runs default 0, integration default 1) and **affected-files concurrency** (runs touching the same manifest files serialize).
- States: `internal_queued → requested → waiting → queued → pending → in_progress → completed | cancelled`; integration polled every 30 s.
- Spot-interruption failures are **auto-retried**.
- **Bypass the queue:** cron runs (§4) and PLDM job-queue jobs (§6).

## 4. Cron Workflow
Source: how-to/create-cron-workflow.md; architecture/budget-governance.md; Confluence 884375553, 1735557362.

Nav: **Workflows → Cron Workflow**. Implementation: gatestage + `pg_cron` (NOT a GitHub `schedule:`) → dispatches `cronwf.yaml` on `ip.infra.sw.manifests`. Grafana: https://grafana.elmnt.local/d/cron-workflows-sw/cron-workflows

| Action | Allowed |
|---|---|
| Create | member of `sw-platform-sg` **or** `project-iridium-sw-sg` **or** `project-iridium-sys-production-sg`, **and** of the owning team (admins/maintainers: any team) |
| Edit / Activate / Delete | `sw-platform-sg`, `sw-maintainers-SG`, or owning-team members |
| List / history | any authenticated user |
| System workflows (team "DevOps System") | activate: sw-platform-sg, sw-maintainers-SG, sys-production, `sw-releases-sg`; edit/delete: **sw-platform-sg only** |

5-step wizard:
1. **Basics** — name, description, team, project.
2. **Configuration** — revision, manifest name (**without `.xml`**), manifest version (`latest` or pinned); Fail Fast ON by default; Run Type fixed = `Schedule`; no Repo field.
3. **Build** — labels, Release/Debug/Sanitizer, timeout **≤ 720 min**, disable unit tests, continue-on-error, `extra_flags`.
4. **Tests** — "Defined here" or **Dynamic** (from starfile `test_config`; starfile sees `phase=L1`, `run_type=Schedule`).
5. **Dependencies** (optional) + **Advanced**: notify on conclusion (start + finish mail; creator always included), auto-retry 1–3, Jira-on-failure (best effort; opens a `wf_cron` issue and triggers the AI Investigator).

Schedule syntax: standard **5-field cron**, min interval **1 h**, day-of-week 0–6; no `@daily`, no `TZ=` prefix, no Quartz (`?`, `L`, `W`, 6/7 fields). States: `new`, `idle`, `processing`, `failed`. Import/Export JSON supported (agent skill `cron-workflow-import-export`).

**Cron approval groups & team budget (Confluence 1735557362, DEV-4517, apps PR #3316):** proposed flow = create cron → choose approval group → group emailed → member approves → cron may run; EC2 spend tracked per approval group (proposed default **$2,000/month**), warn at 80 %, block at 100 % until a budget increase is approved. **Current implemented state (budget-governance.md):** team budgets are allocation-only (consumption shown 0, **not enforced**); extension requests use kind `cron-team-budget-extend` (§5).

## 5. Budget module & generic approvals
Source: how-to/personal-vm-budget.md; architecture/budget-governance.md, generic-approvals-design.md.

Nav: **Modules → Budget** (`/runway`). Visible to `sw-platform-sg`, the approver teams and requester groups.

| Fact | Value |
|---|---|
| Personal EC2 default | **$50 / month**, resets on the 1st |
| Consumption source | finops DB fed by `cur-ingester` daily 06:00 UTC → **~1 day lag** |
| Usage emails | at 50 / 80 / 100 % (recipient currently overridden to tal.cohen during rollout) |
| Request | `/runway` → **Request extension**; one pending request at a time; new limit ≤ 2× (client-side check); approved limit persists until an admin resets |
| Expiry | pending request expires after **7 days** |
| Self-approval | never allowed |
| octl enforcement | budget check is **warn-only, fails open** (`budget_dry_run: true`) |
| Approval kinds | `ec2-budget-extend` (subject `user:<u>`), `cron-team-budget-extend` (`team:<slug>`) |

Approver routing (first match wins):
| Requester in group | Approver group |
|---|---|
| `project-iridium-validation-silicon-sg` or `project-iridium-validation-system-sg` | `portal-budget-approvals-validation-sg` |
| `project-iridium-ai-sg` | `portal-budget-approvals-ai-sg` |
| anyone else | `portal-budget-approvals-sw-sg` |
| (override) | `sw-platform-sg` sees and can decide everything |

Generic approvals: one gatestage engine (`kind`, `subject`, routed team, state pending/approved/rejected/expired), emails to routed team members (≤ 5 retries), decisions in Inbox → Approvals and `/budget/history` (approvers).

## 6. PLDM Job Queue (portal side)
Source: how-to/pldm-job-queue.md; Confluence 1120927801, 1206583297.

Nav: **Workflows → PLDM Job Queue** (`/catalog/pldm-job-queue`). Columns: Display Name, Image Type, Status (`queued/running/completed/failed/cancelled`), Assigned EDK, Created At.
- **Cancel** = running job; **Delete** = queued job. Allowed for job owner or `sw-maintainers-SG`.
- EDK went offline → auto-requeue up to **3×** (`emul_online_edk_unavailable` → finally `emul_online_edk_retry_limit_exceeded`), badge "Requeued ×N".
- Image-type availability comes from **emul-online** via gatestage. Terminal jobs purged after **30 days**. Bypasses the Portal queue.
- Submit with `octl ws build --remote|--local --target pldm` → use the local **`pldm-job-queue`** skill (`pjq.py` submit/scan/show/cancel).
- Remote build machines/VM mirrors → local **`vm-delegate`** skill.
- Open items (Confluence 1206583297, epic DEV-2685): EDK health-check script & cleanup policy (EML-59/60/61).

## 7. Herod (CSR browser)
Source: how-to/use-herod-quick-start.md; architecture/herod-agent.md.

Nav: **Modules → Herod** (`/herod`) → choose **Project** + **TAG** → **Open project**. Backend herod-v2 (herod-rds + S3 `s3-sw-herod-pt-data`).
- Non-fullchip tags (`<project>_psoc...`): **Advanced Options → entry point** e.g. `PSOC_TOP` (`TOP` invalid there).
- New tag appears ≈ 10 min after PT generates JSON (even if release-specs workflow failed).
- **Herod agent** (herod-bot + herod-mcp): Phases 0–1 built; read-only; scoped to projects the user can see.

## 8. Security tooling
Source: how-to/scan-vulnerabilities-blackduck.md, coverity-static-analysis-octl.md, forbidden-words-scanner.md, cd-external/kms-production-signing.md; architecture/blackduck/blackduck-architecture.md; Confluence 535691268, 528646367, 530284546, 999686218, 1367900205, 484474905.

### BlackDuck (SCA — open-source components, CVEs, licenses)
| Use | How |
|---|---|
| Local scan | `octl ws scan` (company network required) → uploads to BlackDuck as `<user>/<manifest>/<revision>` |
| CI | reusable GitHub Actions workflow (Synopsys Detect) → project `<project>/<manifest>/<revision>` |
| Retention | BlackDuck keeps 10 versions/project; gatestage syncs every scan every 5 min to S3 report (kept 1 year) |
| Portal | BlackDuck Scans page: GitHub (CI/CD) & Dev tabs, version compare, Refresh; `project-<name>-*` groups see own project, admins all |
| IDE | BlackDuck plugin: SCA of dependencies + Rapid Scan Static (Confluence 530284546) |

### Coverity (SAST)
| Use | How |
|---|---|
| Full / incremental scan | `octl ws sec cov` / `octl ws sec cov --mode incremental` |
| Gate | `--fail-on high` (gates on internal findings only, not 3rd-party) |
| Submit to Connect server | `--submit` — **CI only** (OIDC); local submissions refused |
| Results | `.coverity/results/coverity-results.json`, `coverity-status.json` |
| Portal | Security → Coverity Scans / Coverity Issues |
| AI | agents `@coverity-security`, `@coverity-reviewer`; prompts `coverity-scan`, `coverity-fix` |

### KMSS (signing)
Dev/CI stages sign with **non-production (dummy) keys**, no approval. Production: request (Portal "New KMS Production Key Signing") → mandatory approval (Jira) → re-sign with AWS KMS prod key → S3 `signed-key-<N>-...`. Keys managed per project/component/index in **Security → KMSS**; SSO + group-gated.

### Forbidden-words scanner (betterleaks)
- Runs on **every PR** in `pr-validator.yml` (also validates `[JIRA-123]` title, ≤ 80 chars, non-Epic issue, `.clang-format` in repo root).
- Scans changed source (full tree on config change) + PR title/description/comments; **case-insensitive whole-word**.
- Blocked: `lotus neon ocean argon sifive accton elmnt element elemnt elment elementlabs elmcli*` and `author:`.
- Excluded: `.git/ .github/ .elmnt/ .vscode/`, CODEOWNERS, license files, copyright/SPDX header lines.
- Org rules baked in image `general/apps:betterleaks-scanner`; per-repo override `.elmnt/betterleaks-local.toml` (add `[[rules]]`, or one CEL `filter`; **no `prefilter`**; anchor paths `(?:^|/)tests/`).
- `.elmnt/` is never synced to external orgs.
- Local repro: Docker + assume `role-project-<name>` (base SSO role can't pull ECR).

## 9. Backend stack & repository map
Source: docs/repositories.md; architecture/portal-sessions.md, gatestage-auth-design.md, generic-approvals-design.md.

| Component | Role |
|---|---|
| `apps/web/platform` | Portal SPA (React) |
| platform-bff | BFF: OIDC session, `requireModule` OPA gate (`core/policy/access.rego`, shared Go module with gatestage), proxies to gatestage / agent-gateway |
| gatestage (https://gatestage.elmnt.local) | Go + Postgres: Portal queue, cron (pg_cron), budgets, generic approvals, PLDM queue, Coverity/BlackDuck sync, KMS sign requests |
| herod-v2 | CSR browser backend |
| agent-gateway → kagent (v0.10.x) | Platform Bot / agentic runtime |
| agentic-dispatcher | runs Agentic Tasks via `agentic_runner.yaml` |

Gatestage caller auth: Portal → Dex ID token (aud `gatestage`); octl → signed `sts:GetCallerIdentity`; GitHub Actions → OIDC token (service `devops-workflow`). Enforcement is per module via `AUTH_ENFORCE_MODULES` (others log-only; `cron-workflow` enforced). Maintainer groups: `sw-maintainers-SG`, `sw-platform-sg`; legacy `retool-admin-SG` grants nothing.

**Repo map (`Elmnt-Internal/ip.infra.devops.*`)**
| Repo | Contents |
|---|---|
| `ip.infra.devops.apps` | octl, elbuilder, runner, portal (web/bff/gatestage/herod), docs site |
| `ip.infra.devops.regcli` | register CLI |
| `ip.infra.devops.workflow` | reusable GitHub Actions workflows (pr-validator, scans, builds) |
| `ip.infra.devops.workflow-entry` | dispatchable entry workflows (Portal queue targets, `kms_production_sign.yaml`, `agentic_runner.yaml`) |
| `ip.infra.devops.aws.terraform` | AWS account infra (Terraform) |
| `ip.infra.devops.aws.github-runners` | self-hosted runner infra |
| `ip.infra.devops.aws.terraform-aws-github-runner-fork` | fork of the runner module |
| `ip.infra.devops.docker-images` | CI/tool images (e.g. betterleaks scanner) |
| `ip.infra.devops.provisioning` | Packer, Ansible, Kubespray, WSL, Vector |
| `ip.infra.devops.retool-terraform` | EKS management plane, Victoria* metrics/logs, LiteLLM etc. |
| `ip.infra.devops.vigor` | Gatestage/Portal deploy configs, ArgoCD GitOps |
| `ip.infra.devops.iac` | GitHub org config (repos, teams, rulesets, autolinks) |
| `ip.infra.devops.misc` | misc scripts |
| `ip.infra.devops.manifest.test1/2` | manifest test fixtures |

## 10. Platform Bot & platform-bot-mcp
Source: how-to/ask-platform-bot.md; marketplace `platform-bot-mcp-auth/SKILL.md`; Confluence 885031397.

Read-only assistant over Platform docs/tools (policies, how-tos, octl, CI failures, links). Surfaces:

| Surface | How |
|---|---|
| Portal | **General → Platform AI** (≤ ~7,800 bytes, parallel questions, 👍/👎) |
| Teams | Apps → *Built for your org* → **PlatformDevBot** ("Platform Bot") → Add/Open (no channel = private chat) |
| VS Code / Copilot MCP | server `https://platform-bot-mcp.ai.elmnt.local/mcp`, `"oauth": {"clientId": "platform-bot-mcp"}` |

MCP auth:
- **B1 SSO (default):** Dex static PKCE client `platform-bot-mcp`; no dynamic client registration. Needs VPN + Element Local CA.
- **B2 bearer fallback:** shared token from Secrets Manager `prod/ai/platform-bot-mcp/auth` (key `token`) + header `X-Platform-User: <you>`; never hardcode/commit.

Tools: `ask_platform_bot`, `rate_platform_bot`. Path: platform-bff/MCP → agent-gateway → kagent agents → MCP tools.

## 11. Hive & Agentic Tasks
Source: architecture/hive-design.md, hive-phase1-plan.md, agentic-platform.md, agentic-tasks.md.

- **Hive** — https://hive.ai.elmnt.local, standalone app, same Dex login.
  - Each user gets a personal **"My Hive"**: their Jira tickets, PRs and CI, with agents that act *as the user*.
  - Agents may open PRs only after user approval; **only humans merge**. Uses a scoped LiteLLM key.
  - Status: Phase 1 — M1–M3 done; M4a/M5 paused (2026-11-09). Team Hive = Phase 2.
- **Agentic Tasks** (Portal, admins only): define tasks run by agentic-dispatcher via `agentic_runner.yaml`; results/PRs land in GitHub for human review.

## 12. Platform Copilot agents catalog
Source: docs/agents/README.md (+ agents/workflow-metrics.md).

| Agent | Use for |
|---|---|
| `@github-iac` | GitHub org changes via `ip.infra.devops.iac` (repos, teams, rulesets) |
| `@aqua-env` / `@tahoe-env` | environment-specific helpers for Aqua / Tahoe environments |
| `@ci-debugger` | diagnose failing GitHub Actions runs |
| `@octl-vm` | create/manage personal VMs with `octl vm` |
| `@octl-workspace` | `octl ws` init/sync/build workflows |
| `@octl-remote-build` / `@octl-remote-test` | remote builds / tests |
| `@coverity-security` / `@coverity-reviewer` | run Coverity & fix / review findings |
| `@grafana-alerts` | Grafana alert authoring/triage |
| `@kernel-upgrade` | kernel upgrade procedure |
| `@workflow-metrics` | PromQL over `github_exporter` CI metrics (not listed in README table) |

## 13. Internal services catalog
Source: how-to/platform-internal-web-services.md, use-bazel-remote-cache.md, use-code-artifact.md, access-precomputed-references-bucket.md, dc/ocean-reference.md; architecture/artifactory-prd; Confluence 617974410, 1507524736, 1558544486, 339640322, 1314160710.

| Service | URL / location | Use |
|---|---|---|
| Portal | https://portal.ai.elmnt.local | §1–8 |
| Gatestage | https://gatestage.elmnt.local | portal API (auth required) |
| Dex | https://dex.elmnt.local | OIDC bridge to Azure (SAML) for portal, EKS, MCP |
| Perfetto | https://perfetto.elmnt.local/ | internal fork of trace viewer |
| Bazel remote cache | https://bazel-cache.elmnt.local/ (S3 `s3-elmnt-build-cache`) | `--remote_cache=https://bazel-cache.elmnt.local --remote_upload_local_results=true --tls_certificate=/usr/local/share/ca-certificates/elmnt-local-ca.crt` |
| Headlamp | https://headlamp.elmnt.local | Kubernetes UI (pick cluster, e.g. `ocean`) |
| Grafana | https://grafana.elmnt.local | dashboards (cron, CI metrics) |
| emul-online | via gatestage `/api/status` | Palladium EDK/image catalog feeding PLDM queue |
| LiteLLM AI gateway | https://litellm.ai.elmnt.local (UI `/ui`) | company AI gateway; per-user **virtual key**, per-key budgets/quotas, Prometheus metrics → Grafana |
| MLflow | see docs-site "Setup MLflow" | ML experiment tracking (Confluence 692781446 moved) |
| BlackDuck | (CI/IDE/octl) | SCA, §8 |
| Coverity Connect | (CI/octl) | SAST, §8 |
| JFrog Artifactory | staging https://jfrog.frank-stg-eks01.elmnt.local | planned replacement for S3/ECR/CodeArtifact (two instances: KFS + AWS; PRD DEV-4589) |
| AWS CodeArtifact | Python index | via `uv` only; octl sets `UV_INDEX_URL` |
| Precomputed refs | S3 `s3-elmnt-precomputed-references` (`-staging`) | prefixes `vcd/` 365 d, `general/` 60 d, `fw-build-size-reports/` 60 d; assume `role-project-<project>` |
| Notifications app | Windows desktop app (Intune) | company announcements; one-time "Setup Now" O365 login; logs `%appdata%/NotificationClient` |

LiteLLM client config (Confluence 1558544486):
- **Copilot CLI:** `COPILOT_PROVIDER_BASE_URL=https://litellm.ai.elmnt.local`, `COPILOT_PROVIDER_API_KEY=<virtual key>`, `COPILOT_MODEL=<model>` (one model per session).
- **VS Code:** Chat: Manage Language Models → Add Models → **Custom Endpoint**, API type Chat Completions. If models are hidden on VS Code 1.132+, set `"chat.agentHost.byokModels.enabled": true`.
- **Claude Code:** `ANTHROPIC_BASE_URL=https://litellm.ai.elmnt.local/anthropic`, `ANTHROPIC_AUTH_TOKEN=<virtual key>`; do not also set `ANTHROPIC_API_KEY`.
- Inline completions and Cursor cannot use the gateway.

## 14. GitHub org administration
Source: how-to/join-elmnt-github-org.md, configure-github-ssh-key.md, manage-repos-with-copilot.md, manage-github-autolinks.md, github-codeowners.md, ci/rulesets-branch-protection.md, use-github-copilot.md; agents/github-iac.md.

| Task | How |
|---|---|
| Join `Elmnt-Internal` | accept invitation email; none → Jira **DRR** "New GitHub Invitation" (drr-watchdog automates) |
| Copilot license | DRR, Work Type "Add GitHub Co-Pilot license" (~1 business day) |
| SSH key | `ssh-keygen -t ed25519` → GitHub Settings → SSH and GPG keys → **Configure SSO → Authorize** for Elmnt-Internal |
| Create/update/delete/archive repo | `ip.infra.devops.iac` issue template "Repository: Create / Update / Delete/Archive" → Copilot opens Terraform PR (~3–5 min) → "Approve and run workflows" → review/merge → repo live ~10 min after merge. Check PR for hallucinated names. Skills: `create-github-repo`, `update-github-repo`, `delete-github-repo`, `manage-github-teams`; agent `@github-iac` |
| Teams / CODEOWNERS teams | Portal **GitHub Admin → GitHub Settings → Teams** (Create Team / Edit Team) |
| Rulesets / branch protection | managed as code in iac: required PR reviews, status checks, push restrictions, signed commits, no force-push, linear history |
| Autolinks | `JIRAKEY-123` → Jira link; works in **commit messages, not PR titles**; config `autolinks.jsonnet` in iac |

## 15. Giving AI agents read-only access
Source: how-to/give-ai-agents-read-only-access.md.

| System | Read-only mechanism |
|---|---|
| AWS | role `ElmntAIReadOnly` (prod `891377201015`, staging `825765405658`); profiles `ai-readonly` / `ai-readonly-staging` (`source_profile` = your admin/SSO profile, region `eu-central-1`, 1 h sessions) |
| EKS | kube context `ai-readonly-retool-eks` → cluster `retool-eu-central-1` |
| Atlassian MCP | client-side tool allowlist (read tools only) |
| GitHub MCP | read-only by default |
| Agent rules | put "use read-only profiles" in `~/.copilot/copilot-instructions.md` |

## 16. Copilot plugins management
Source: how-to/manage-copilot-plugins.md, use-github-copilot.md.

Plugins come from marketplace repo `Elmnt-Internal/module.infra.ai_group.toolkit`.
| Command | Effect |
|---|---|
| `octl plugins manage` | interactive picker; flags `--plugins`, `--preset`, `--keep-plugins`, `--flatten`, `-I`, `--dry-run`, `--yes`; `k` = keep permanently |
| `octl plugins update [--only <p>]` | update installed plugins; exit code **5** = a per-plugin failure |
| auto | `octl ws init` / `ws sync` apply workspace defaults (`.github/plugin/workspace-defaults.json`: swGroupDefaults, workspaceDefaults, teamDirectories) |

- Switching workspace removes the previous workspace's team/default plugins (not kept ones).
- State: `$COPILOT_HOME/el-plugins-presets.json`, ledger `el-plugins-state.json`; instruction files copied to `$COPILOT_HOME/instructions/`.
- Disable: `plugins.enable: false` in `~/.octl.yaml`; no-op when `CI=true`.
- Needs Copilot CLI (or `COPILOT_BIN`) + `gh auth login`.

## 17. Contact the Platform team & Platform news
Source: how-to/contact-us.md, platform-news-update.md; Confluence 881557509, 885031397.

Escalation ladder:
1. Ask **PlatformDevBot** (private chat or Platform AI).
2. Post in Teams **"Platform - hub"** channel.
3. Mention **@Platform-Team** if blocked.

Jira:
- **DRR** = service requests (access, invitations, licenses) — https://elementlabs.atlassian.net/jira/core/projects/DRR/board
- **DEV** = Platform features/bugs.

**Platform news:** monthly ~15-min **recorded** session (no live Q&A) posted to Teams "Platform - news"; drafted by platform-bot skill `platform-news-report`.

## 18. Pitfalls
Source: synthesis of sections above.
- A missing nav item means a missing group; group changes need re-login, and the 8 h session doesn't refresh.
- Logging out suppresses auto-login for 12 h; use the Sign-in button explicitly.
- Cron is pg_cron in gatestage, so editing `.github/workflows` schedules does nothing. Use the Cron Workflow page.
- The cron manifest name must be given without `.xml`. A schedule shorter than 1 h, `@daily`, a TZ prefix or Quartz syntax is rejected.
- Integration runs: never enter versions; they're resolved at run start, not at submit time.
- Budget: usage lags about 1 day. The 2× limit cap is only a UI check. octl budget enforcement is currently warn-only. Only one pending request is allowed, and it expires after 7 days.
- Team (cron) budgets show allocation only; consumption reads 0 and is not enforced yet.
- `coverity --submit` from a laptop is refused (CI OIDC only).
- Forbidden words: a broad `filter` on `rule_id` disables all codenames in the repo. `prefilter` in the local toml is rejected. `^tests/` never matches because paths are prefixed.
- Autolinks don't render in PR titles.
- Copilot-generated iac PRs can hallucinate repo or team names; review before merging.
- platform-bot-mcp: a stale dynamic auth provider for `dex.elmnt.local` breaks login. Never commit the bearer token.
- LiteLLM: use a personal virtual key, never the master key. With Claude Code, don't set `ANTHROPIC_API_KEY` alongside the auth token.
- The `retool-admin-SG` group grants nothing in gatestage anymore.

## 19. Troubleshooting (symptom → fix)
Source: how-to docs above; platform-bot-mcp-auth SKILL.md; portal-sessions-reference.md.

| Symptom | Fix |
|---|---|
| Portal won't load / DNS fail | connect VPN; trust Element Local CA |
| `/login?error=not_authorized` | not in any portal group → DRR / ask team lead |
| `/login?error=expired` or `exchange_failed` | click Sign in again (OAuth state cookie 10 min) |
| 503 on every call | session store/BFF issue → Platform hub, don't loop logins |
| "Your session expired" on save | re-login in a new tab, then retry save (page state kept) |
| Tool missing from nav/Catalog | lacks group → request membership, then **log out/in** |
| Portal-queue run stuck `waiting/queued` | another run holds same affected files / integration priority → wait; check workflow-entry Actions |
| Cron never fires | state not active / approval pending / invalid schedule → check Cron page state & Grafana dashboard |
| Cron create button disabled | not in sw-platform-sg / project-iridium-sw-sg / sys-production + owning team |
| Budget request button greyed | already one pending request, or the requested value is above 2× |
| PLDM job `emul_online_edk_retry_limit_exceeded` | EDK pool unhealthy → resubmit later; report in Platform hub |
| Herod tag missing | wait ~10 min after PT JSON; for psoc tag pick `PSOC_TOP` entry point |
| `octl ws scan` fails | must be on company network; check BlackDuck token/VPN |
| PR blocked by forbidden-words | rename word, or add narrow `filter` in `.elmnt/betterleaks-local.toml` (CODEOWNERS approval) |
| MCP login doesn't start / wrong client_id | remove dynamic auth provider `dex.elmnt.local` in VS Code, restart MCP server |
| MCP `ENOTFOUND` | VPN |
| MCP 401 | complete SSO login (or B2 bearer + `X-Platform-User`) |
| BYOK models absent in VS Code picker | `"chat.agentHost.byokModels.enabled": true` + Reload Window |
| `octl plugins update` exits 5 | one plugin failed → rerun with `--only <plugin>`; check `gh auth status` |
| GitHub `git push` 403 via SSH | key not SSO-authorized → Configure SSO → Authorize |

## 20. Owner / escalation
Source: how-to/contact-us.md; docs front matter (`owner: Elmnt-Internal/sw-platform-sg`).

| What | Owner / channel |
|---|---|
| Portal, gatestage, bots, docs | Platform team, GitHub team `Elmnt-Internal/sw-platform-sg` |
| Access / licenses / invitations | Jira **DRR** |
| Bugs / features | Jira **DEV** (e.g. budget DEV-4517, PLDM queue DEV-2685, Artifactory DEV-4589) |
| Live help | PlatformDevBot → Teams "Platform - hub" → @Platform-Team |
| Budget approvals | `portal-budget-approvals-{sw,ai,validation}-sg`, fallback `sw-platform-sg` |
| Release/integration workflows | `sw-maintainers-SG` |
| Docs | https://elmnt-internal.github.io/ip.infra.devops.apps/ (PRs to `ip.infra.devops.apps/docs`) |
