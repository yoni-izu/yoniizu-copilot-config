# Observability — Grafana, VictoriaMetrics, VictoriaLogs, test results, CI metrics

Element Labs observability uses **Grafana 13** (`https://grafana.elmnt.local`, MS SSO), **VictoriaMetrics** for metrics
(infra `vmsingle.elmnt.local`, team `vmsingle-sw` / `vmagent-sw`) and **VictoriaLogs** for logs (`vmlogs.elmnt.local`, shipped by Vector).
CI test results are stored in **Postgres** (`analytics.*`, via `test-results-exporter`). Dashboards are **GitOps-managed in Terraform** (`ip.infra.devops.retool-terraform`),
and alerts are **Helm values in `ip.infra.devops.apps`**. The Platform team (`sw-platform-sg`) owns all of it. EKS-hosted app logs go to CloudWatch, not VictoriaLogs.

**TOC**
1. Stack map & endpoints · 2. Grafana: access, orgs, datasources · 3. Dashboards as code (GitOps) · 4. Dashboard catalog
5. Alerts (metric, SQL, log) & contact points · 6. VictoriaMetrics (infra + SW, push, VictoriaMetricsTracker)
7. CI / runner metrics (github_exporter) · 8. Test-results pipelines (legacy VM, QA Postgres) · 9. Logs: VictoriaLogs + Vector + CloudWatch
10. EC2 / GPU (DCGM) / GitHub monitoring, KPIs · 11. How to answer common questions · 12. Pitfalls · 13. Owner / escalation · 14. Deeper skills & sources

---

## 1. Stack map & endpoints
Source: docs `how-to/sw-monitoring-victoriametrics.md`, `find-and-read-logs.md`, `grafana-gitops.md`; live probes (2026); repo trees.

| Component | Endpoint / location | Access | Deployed from |
|---|---|---|---|
| Grafana (v13.2.5) | `https://grafana.elmnt.local` (`/api/health` is open) | MS SSO; the API needs auth (401) | `victoria-metrics-k8s-stack` Helm in ns `monitoring` (EKS `retool-eu-central-1`), RDS `rds_grafana`; content via retool-terraform `modules/infra/grafana` |
| VictoriaMetrics infra | `https://vmsingle.elmnt.local` (VMUI `/vmui/`, Prom API `/api/v1/*`) | anonymous read | `ip.infra.devops.apps` `zarf/k8s/base/elmnt-observability/victoria-metrics` (+ cluster overrides `zarf/k8s/clusters/<ocean…>/values-overwrite/victoria-metrics`) |
| VictoriaMetrics SW (team) | read `https://vmsingle-sw.elmnt.local`, write `https://vmagent-sw.elmnt.local/api/v1/import/prometheus` | read open; write Basic Auth | retool-terraform `modules/infra/team-monitoring` (reusable per team) |
| VictoriaLogs | `https://vmlogs.elmnt.local` (VMUI `/select/vmui/`, ingest `/insert/jsonline`) | anonymous read | retool-terraform; server pod `vls-victoria-logs-single-server` |
| Vector (log shipper) | K8s DaemonSet / EC2+PLDM host agent | — | K8s: `ip.infra.devops.apps` `zarf/k8s/base/elmnt-observability/vector`; hosts: Ansible role in `ip.infra.devops.provisioning` |
| Alert rules | `ip.infra.devops.apps` `zarf/k8s/eks/monitoring/devops-grafana-alerts/` | PR | ArgoCD → ConfigMaps → Grafana sidecar |
| Alert orchestrator (log-alert → Jira) | `apps/services/alert-orchestrator`, deploy `zarf/k8s/eks/monitoring/alert-orchestrator` | — | ArgoCD |
| github_exporter (CI metrics) | `apps/services/github_exporter`, deploy `zarf/k8s/eks/monitoring/github-exporter` | — | fed by `platform-webhooks` (`apps/services/platform-webhooks`) |
| github-pr-stats exporter | `apps/services/github_pr_stats`, deploy `zarf/k8s/eks/monitoring/github-pr-stats` | — | scraped job `github-pr-stats` |
| CloudWatch exporter | `zarf/k8s/eks/monitoring/cloudwatch-exporter` | — | job `cloudwatch-exporter` |
| Other monitoring deploys | `zarf/k8s/eks/monitoring/{cur-ingester,otel-collector,runner-dispatcher}` | — | cur-ingester → FinOps PG (`finops-pg`) |
| Test-results exporter (QA) | repo `Elmnt-Internal/ip.infra.val.test-results-exporter` | — | EKS ns `monitoring`; RDS Postgres |
| MCP tools | `zarf/k8s/eks/mcp-tools/{victorialogs-mcp,victoriametrics-mcp}` | — | lets agents query VL/VM |

## 2. Grafana: access, orgs, datasources
Source: `how-to/grafana-gitops.md`; Confluence 169443335 "Grafana"; platform-ops `grafana-alerts` skill; dashboard JSON in retool-terraform.

- **Login**: Microsoft SSO. Everyone is a **Viewer** in the **Main** (orgId 1) and **SW** (org 6, per the `i-want-a-grafana-dashboard` skill) orgs.
  The **IT** org is granted via Entra groups `sw-platform-sg, it-SG, it-sysdev-SG, validation-system-SG, vlsi-SG, sw-SG`. No one gets Admin through SSO.
  **After a role/group change, log out and back in.**
- Every org has a **`[WIP]` folder** that anyone can edit. Put your drafts there (`[WIP]/<your-name>`). All other folders are Terraform-managed and view-only.

| Datasource (name/type) | UID | Use |
|---|---|---|
| VictoriaMetrics (Prometheus type, **default**, Main) | `P4169E866C3094E38` | PromQL, all infra/CI metrics |
| VictoriaMetrics native (`victoriametrics-metrics-datasource`) | `PA144FC3F5C193807` | MetricsQL extras |
| VictoriaLogs (`victoriametrics-logs-datasource`) | `victorialogs` | LogsQL; log alerts; Runner Details `ds_logs` |
| CloudWatch eu-central-1 | `deetazrrcw000c` (Main), `cloudwatch-sw` (SW) | EC2/RDS/EKS AWS metrics, Auto-Stop |
| Alertmanager | `P7647F508D5F54FCB` | alert state |
| Postgres "workflow DB" (CI/GitHub data) | `cf0eo1v68qry8d` | PLDM queue, Cron Workflows, Runner Utilization, `raw_sql` alerts |
| Test-results Postgres | `test-results-pg-sw` (SW org), `test-results-pg` (Main) | QA test results (`analytics.*`) |
| Runner-dispatcher Postgres | `efs6vpvnypds0b` | Dispatcher Job Lookup / DC Fleet |
| FinOps Postgres | `finops-pg` | EC2 personal cost, Cron Workflows cost |
| Update-manifest Postgres | `dfj613zi8ssn4b` | Update manifest dashboard |
| VM SW (Prometheus type, SW org) | `dfn7a2z29eha8f` | `vmsingle-sw` series (as seen on the exporter-health dashboard) |

The provisioned VM datasources come from Helm and are read-only. Datasources created in the UI plus SSO org mapping are Terraform-managed (`modules/infra/grafana`).

## 3. Dashboards as code (GitOps)
Source: `how-to/grafana-gitops.md`; `i-want-a-grafana-dashboard` skill; retool-terraform tree.

- **Repo/path**: `Elmnt-Internal/ip.infra.devops.retool-terraform` → `modules/infra/grafana/dashboards/<main|sw|it>/<Folder>/<Sub>/<Title>.json`.
  The directory tree is the folder tree. A `/` in a folder title becomes `-` on disk (`devops/aws` → `devops-aws`); see `local.folder_title_overrides` in `locals.tf`.
- **Identity = top-level `uid`**. Changing the uid makes `plan` show "1 to destroy, 1 to add" and **breaks every link/bookmark**.
- **CI**: on a PR, `terraform plan` is posted as a PR comment. On merge to `main`, apply runs. UI edits to managed dashboards are **reverted on the next apply**.
- **Edit flow**: open the dashboard → *Save as* into `[WIP]` → edit → Share → Export (turn **off** "Export for sharing externally"),
  or `GET /api/dashboards/uid/<uid>` and keep `.dashboard` → **restore the original uid** → replace the JSON file → PR.
- **New dashboard**: build it in `[WIP]`, export, pick a stable readable uid (e.g. `qa-test-results-postgres`), add the file under the right folder dir, open a PR.
- **Delete**: delete the file. Removing the last file in a dir deletes the folder.
- `dashboards/sw/` has its own **CODEOWNERS** (SW reviewers); the rest is reviewed by Platform.
- **Not** handled by this flow: alert rules (§5), Helm-provisioned VM datasources, users.
- Legacy: `ip.infra.devops.apps/zarf/grafana-dashboards` and `apps/services/github_pr_stats/grafana-provisioning/` still exist. Treat retool-terraform as the source of truth.

## 4. Dashboard catalog
Source: portal `apps/web/platform/src/lib/grafana.ts`; retool-terraform `dashboards/` (uids read from JSON); docs; Confluence 169443335.
All URLs are relative to `https://grafana.elmnt.local`. "File" is relative to `modules/infra/grafana/dashboards/`.

| Dashboard | URL | File | Data / key vars |
|---|---|---|---|
| **Runner Details** | `/d/ce4fbxj65q6f4b/runner-details` | `main/Github/Runner Details.json` | `job_info` (VM) + VictoriaLogs; vars `project, manifest, repo, workflow_link, runner, job`. `workflow_link` = `<run URL>/attempts/<N>` |
| **Cron Workflows** | `/d/cron-workflows-sw/cron-workflows` | `main/Github/Cron Workflows.json` | PG workflow DB + finops + test-results; `var-team`, `var-workflow_name` = **numeric workflow id** |
| **EC2 Personal VM — Usage & Cost** | `/d/ec2-personal-usage-cost/ec2-personal-vm-usage-cost` | `main/AWS/AWS-Cost/…` | `finops-pg` + VM; `var-owner` = lowercased STS session user (EC2 tag `owner_session`); use `from=now/M&to=now` |
| **NVIDIA DCGM Exporter** | `/d/nvidia_dcgm_exporter/nvidia-dcgm-exporter` | `main/K8S/NVIDIA DCGM Exporter.json` | `var-datasource=P4169E866C3094E38&var-node=<node>&var-gpu=$__all&var-pod=<pod>` |
| **PLDM Job Queue** | `/d/pldm-job-queue-v1/pldm-job-queue` | `main/Github/PLDM Job Queue.json` | PG workflow DB (`cf0eo1v68qry8d`) |
| **Alerts Overview** | `/d/alerts-overview/alerts-overview?orgId=1&var-datasource=P4169E866C3094E38` | `main/Alert Manager.json` | firing/resolved by team, severity, cluster, state (incl. `log: info` alerts) |
| **Tests results** (legacy, VM) | `/d/begsnb827dse8d/tests-results` | `sw/Tests results.json` | `test_results` metric; vars `project, manifest, workflow_link, job_link, kernel, phase, group, name` |
| **QA Test Results** (Postgres) | `/d/dev3619-test-results` (SW org) | `sw/[QA] Tests Results/QA-Test-Results.json` | `test-results-pg-sw`; drilldowns, health, flaky (`dev3782-quality-integration-flaky`), perf trends in sibling folders |
| Test Results (Postgres, Main) | `/d/qa-test-results-postgres` | `main/Github/Test Results (Postgres).json` | `test-results-pg` |
| QA exporter health | `/d/test-results-exporter-health` | `sw/[QA] Tests Results/Exporter/…Health.json` | exporter metrics + CloudWatch + PG |
| **Runner Dispatcher — Job Lookup** | `/d/runner-dispatcher-lookup/runner-dispatcher-e28094-job-lookup?orgId=1&var-dc_job=dc` | `main/K8S/Runner Dispatcher - Job Lookup.json` | dispatcher RDS (`efs6vpvnypds0b`); paste a job URL into `job_link` |
| Runner Dispatcher — DC Fleet | `/d/runner-dispatcher-fleet` | `main/K8S/…DC Fleet.json` | DC runner fleet state |
| Runner Utilization | `/d/ad9s8h8` | `main/Runner Utilization.json` | PG + VM |
| GitHub Runner CPU & Memory | `/d/runner-cpu-memory-utilization` | `main/AWS/GitHub Runner — CPU & Memory Utilization.json` | CloudWatch; right-sizing siblings in `main/AWS/` |
| Workflow general view | `/d/df0453g684wzkf/workflow-general-view` | (Confluence link) | CI overview |
| Update manifest | `/d/ce4fbxj65q6f4s2/update-manifest` | `main/Github/Update manifest.json` | PG `dfj613zi8ssn4b` + VM |
| Depends on (L1) | `/d/ce4fbxj65q6f4s1/depends-on-l1` | `main/Github/Depends on (L1).json` | VM |
| Integration Manifest | `/d/devkmoy145edcc/integration-manifest` | `main/Github/Integration Manifest.json` | PG + VM |
| CI — Build Overview (L0/L1) | `/d/ci-build-overview` | `main/Github/CI — Build Overview (L0-L1).json` | `job_info` |
| CI — Failure Reasons | `/d/ci-failure-reasons` | `main/Github/CI — Failure Reasons.json` | |
| Test Job Results | `/d/test-job-results` | `main/Github/Test Job Results.json` | |
| Merge Queue Health | `/d/merge-queue-health` | `main/GitHub PR Stats/Merge Queue Health.json` | `github_merge_queue_*` |
| EC2 Auto-Stop Monitor | `/d/ec2-auto-stop-monitor` | `main/AWS/EC2/EC2 Auto-Stop Monitor.json` | CloudWatch `deetazrrcw000c` |
| Cluster Logs / Hosts | `/d/cluster-logs`, `/d/host-logs` | `main/Logs/*.json` | VictoriaLogs |
| CLI Logs (octl telemetry) | `/d/bdpzp3w3jkt8ga` | `main/Platform/CLI Logs.json` | VictoriaLogs |
| Hive — My Work | uid `hive-my-work-overview` | `main/Platform/Hive — My Work.json` | Hive metrics |
| Platform Bot — Overview/Traces | | `main/Platform/Platform Bot — *.json` | `bot_turn`/`bot_feedback` events in VL |

Other folders: `main/Kubernetes - *`, `Node Exporter Full`, `VictoriaMetrics - *`, `main/Platform/KubeVirt/*`, `main/VLSI-Grafana/*` (DV/DFT jobs, WF health),
`main/AWS/Spot/*`, `main/Coverity/*`, and `sw/` (vLLM accuracy/perf, kernels perf, Unit Tests overview, Workloads Nightly; `sw/Deprecated Dashboards/` is retired).
Dashboards cross-link through data links: workflow → job → Runner Details → logs.

## 5. Alerts (metric, SQL, log) & contact points
Source: `how-to/create-grafana-alerts.md`, `create-grafana-log-alerts.md`, `agents/grafana-alerts.md`; platform-ops `grafana-alerts` skill; apps repo tree.

**Layout** (`ip.infra.devops.apps/zarf/k8s/eks/monitoring/devops-grafana-alerts/`):

| Path | Content |
|---|---|
| `values.yaml` | shared defaults |
| `teams/<team>/values.yaml` | team metric/SQL alerts + contact points (`platform`, `qa`, `simulator`, `host_drivers`) |
| `clusters/<cluster>/teams/<team>/values.yaml` | cluster-scoped log alerts (e.g. `clusters/pldm/teams/compute_driver`, `clusters/ec2/teams/simulator`, `clusters/ocean/teams/{fleet-mgmt,it-labs}`) |
| `templates/alertrules.yaml`, `templates/contactpoints.yaml` | **never edit** |

Flow: PR → CI runs `helm lint` + `helm template` → merge → ArgoCD renders ConfigMaps labelled `grafana_alert: "1"` / `grafana_alerting: "1"` → the Grafana sidecar loads them.
Alerts cannot be edited in the UI (they are provisioned). ⚠ The `grafana-alerts` skill still shows an older `zarf/grafana-alerts/k8s/base` + `argocd-application.yaml` layout. **The docs path above is current.**

**Metric / SQL alert fields** (one item per alert in a group):

| Field | Notes |
|---|---|
| `title`, `uid` | uid lowercase, ≤40 chars, convention `<team>-<what>` |
| `query` **or** `raw_sql` | PromQL; or SQL that returns a numeric column named `value` (datasource `cf0eo1v68qry8d`) |
| `datasource_uid` | default VM `P4169E866C3094E38` |
| `threshold_type`, `threshold` | `gt/lt/gte/lte/eq/ne` |
| `for` | default `1m` |
| `summary`, `description`, `labels`, `receiver` | receiver = contact-point name |
| optional | `no_data_state` (NoData), `exec_err_state` (Error), `is_paused`, `time_range_from` 600 / `time_range_to` 0, `repeat_interval`, `orgId` (1) |

```yaml
# teams/platform/values.yaml (shape)
- title: "Runner queue wait > 15m"
  uid: platform-runner-wait-high
  query: 'max(max_over_time(runner_wait_seconds{workflow_name="ci.yaml"}[15m]))'
  threshold_type: gt
  threshold: 900
  for: 5m
  receiver: integration-failures-sg
```

**Contact points**:
- `type: email`: `addresses` separated by `;`, plus `singleEmail` and `disableResolveMessage`.
- `slack` and `webhook` types are also supported.
- **Teams**: `type: teams` plus `externalSecret.remoteRef.key: prod/grafana/teams-<team>-<topic>` (AWS Secrets Manager, property `webhook_url`; **open a Platform ticket to create the secret**) and a `message` template.
  In Go templates write `$` as **`$$`**.
- Existing receivers: `integration-failures-sg` (~35 people, resolve messages disabled), `sw-managers-sg`, `sw-releases-sg`, `log-alerting-webhook`.

**Log alerts** (LogsQL against VictoriaLogs):

```yaml
default_values:            # first entry: defaults for the file
  time_range_from: 600
  for: 5m
  threshold: 1
  no_data_state: OK
  exec_err_state: Error
  receiver: log-alerting-webhook
alerts:
  - title: "vLLM configure future not ready"
    log: warning            # info = Alerts Overview only; warning = also opens a Jira ticket
    pattern: 'app:vllm AND _msg:"configure future not yet ready"'
    for: 5m
    description: "..."
    labels: { jira_project: DEV, jira_component: "<component>" }
```

- `warning` opens Jira through **alert-orchestrator** (`apps/services/alert-orchestrator`, `jira.go`), using `jira_project`/`jira_component`.
- Email and webhook can share one contact point.
- Watch firing alerts on **Alerts Overview**:
  `/d/alerts-overview/alerts-overview?orgId=1&from=now-2d&to=now&var-datasource=P4169E866C3094E38&var-team=$__all&var-severity=$__all&var-cluster=$__all&var-state=$__all`.
- **Agent route**: the `@grafana-alerts` Copilot agent (docs `agents/grafana-alerts.md`) takes a plain-language request and opens the PR; you review it.
  Example: the Hive alerts are group `platform-hive-work` in `teams/platform`.

## 6. VictoriaMetrics (infra + SW team instance)
Source: `how-to/sw-monitoring-victoriametrics.md`; Confluence 1075380225 "Victoria Metrics"; live `/api/v1/label/*/values` probes; vllm-atl `vllm_atl/tests/victoria_metrics_tracker.py`.

| | Infra `vmsingle.elmnt.local` | SW `vmsingle-sw` / `vmagent-sw` |
|---|---|---|
| Content | ~4.5k metric names: k8s, nodes, exporters, CI | only team-pushed series (live: 5 `vllm_atl_*` metrics) |
| Write | scrape (VMServiceScrape/VMPodScrape), github_exporter push | `POST https://vmagent-sw.elmnt.local/api/v1/import/prometheus` (Prom text, Basic Auth → **204**) |
| Read | open, Grafana Main `P4169E866C3094E38` | open `https://vmsingle-sw.elmnt.local` (writes → **403**); Grafana **SW org** |
| Retention / labels | — | **4 weeks**; `team="sw"` auto-injected |
| Creds | n/a | AWS SM `prod/monitoring/sw-vmagent/credentials` (`username`/`password`/`htpasswd`) |

**Push from CI (octl)**:
- Env `EL_VMAGENT_URL`, `EL_VMAGENT_USERNAME`, `EL_VMAGENT_PASSWORD` are injected **only in the `production` environment**.
- Starfile key `octl.monitoring.vmagent_url`; override with `ELMCLI_MONITORING_VMAGENT_URL`.
- In-cluster pushes skip auth.
- Errors: 401 means wrong creds; 403 means you pushed to vmsingle instead of vmagent.

```bash
curl -s -u "$EL_VMAGENT_USERNAME:$EL_VMAGENT_PASSWORD" --data-binary \
  'my_bench_latency_ms{suite="l1",model="llama"} 12.3' \
  "$EL_VMAGENT_URL/api/v1/import/prometheus"     # expect 204
curl -sk 'https://vmsingle-sw.elmnt.local/api/v1/query?query=my_bench_latency_ms'
```

**`VictoriaMetricsTracker`** (vllm-atl, `vllm_atl/tests/victoria_metrics_tracker.py`; used by `vllm_atl/tests/perf/test_e2e_benchmark.py`):
- Usage: `VictoriaMetricsTracker(test_type="performance"|"accuracy", suite="l1", metric_name="vllm_atl_worker_perf").log_run(metrics={...}, params={...})`.
- The tracker **does not push**. It appends JSONL under `$TEST_OUTPUT_DIR/victoria_metrics/`, and the starfile suite post stage uploads records made with `--victoria-upload`
  (module `vllm_atl.tests.victoria_metrics_upload`). The target comes from `VM_IMPORT_URL` or `EL_VMAGENT_URL`; if neither is set, it only records locally.
- Data model: one metric name, with the original key in a `metric` label and params as labels. The first `log_run` also writes `vllm_atl_run_info`
  (value 1; labels `suite`, `test_type`, `vllm_atl_commit`, `vllm_atl_branch`, `vllm_atl_manifest_version`, `ci_run_id`, `ci_run_attempt`…). Join it on `suite`.
- New test metrics should go to **Postgres via JUnit** (§8). The dashboard skill calls VM "possible but not recommended".
  Migration mapping: suite → `test_name`, params → dimension columns, metrics → `test_metrics` rows.

**Useful metric families on the infra VM** (`curl -sk https://vmsingle.elmnt.local/api/v1/label/__name__/values`):

| Family | Examples / job |
|---|---|
| Nodes/hosts | `node_*` — jobs `node-exporter-platform-ec2` (EC2 runners/VMs), `-platform-dc`, `-proxmox-vms`, `-sysdev`; `windows_*` |
| K8s | `kube_*`, `kubelet_*`, `container_*`, `apiserver_*`, `cilium_*`, `coredns_*`, `kubevirt_*`, `argocd_*` |
| CI (github_exporter, pushed) | `workflow_info`, `job_info`, `step_info`, `runner_wait_seconds`, `test_results` |
| GitHub stats (`github-pr-stats`) | `github_pr_info`, `github_pr_ci_runs_total`, `github_pr_ci_duration_seconds_total`, `github_pr_ci_reruns_total`, `github_pr_review_wait_seconds`, `github_code_reviews_total`, `github_merge_queue_depth`, `github_merge_queue_entry_wait_seconds`, `github_repo_workflow_runs_by_conclusion_total`, `github_repo_workflow_duration_seconds_total`, `github_rate_limit_remaining` |
| GPU | `DCGM_FI_DEV_GPU_UTIL`, `_FB_USED/_FB_FREE`, `_GPU_TEMP`, `_POWER_USAGE`, `_XID_ERRORS`, `_NVLINK_BANDWIDTH_TOTAL`, `DCGM_FI_PROF_PIPE_TENSOR_ACTIVE` (job `nvidia-dcgm-exporter`) |
| HW / lab | `idrac_*`, `redfish_*`, `openbmc_*`, `hw_*`, `sat_*`, `cartridge_prbs_*`, `rmc_*`, `weka_*`, `pve_*` (proxmox) |
| Services | `litellm_*`, `vllm:*`, `hive_*`, `platform_webhooks_*`, `django_*`, `awx_*`, `probe_*` (blackbox, job `URL_Checks`) |
| Stack self | `vm_*`, `vmagent_*`, `vl_*` (VictoriaLogs), `vector_*`, `grafana_*`, `alertmanager_*` |

EC2 runner node label example:
`node_uname_info{job="node-exporter-platform-ec2",cluster="multi-runner",role="github-runner",instance_id="i-…",instance_type="c8i.8xlarge",site="eu-central-1",node="multi-runner-base-cxi-8xlarge-action-runner"}`.

## 7. CI / runner metrics (github_exporter)
Source: `agents/workflow-metrics.md`, `how-to/get-runner-logs-metrics.md`, `tests-results-dashboard.md`; portal `grafana.ts`.

On each GitHub `workflow_run` completed webhook, `platform-webhooks` calls **github_exporter**, which pushes series to the infra VM. Supported workflows:
`ci.yaml`, `sensitive_workflows.yaml`, `manifests_integration.yaml`, `cd_external.yaml`, `cd_internal.yaml`, `ci_manifest.yaml`.

| Metric | Key labels |
|---|---|
| `workflow_info` | `workflow_name, conclusion, project, repo, manifest_name, revision, pr_number, workflow_link, workflow_run_id, triggering_actor, inputs_group, action, duration, artifacts_info` |
| `job_info` | `job_name, full_job_name, job_link, job_id, runner_name, machine_name, instance_type, ami_id, distribution, architecture, conclusion, duration, repo, workflow_name, phase` |
| `runner_wait_seconds` | `wait_seconds, created, started, job_link` (queue time before a runner picked up the job) |
| `step_info` | per-step duration/conclusion |
| `test_results` | `project, manifest_name, job_link, kernel, phase, test_group, test_name, revision, run_type, result, class_name` |

```promql
# failed L1 builds of a repo (doc example)
count(job_info{workflow_name="ci.yaml", job_name=~"Build .*", phase="L1", repo="ip.infra.sw.firmware", conclusion="failure"})
# slow queue: jobs that waited > 10 min for a runner in the last day
count(max_over_time(runner_wait_seconds{workflow_name="ci.yaml"}[1d]) > 600)
# runner CPU during a job (use instance_id from job/runner name)
100 - avg by (instance_id)(rate(node_cpu_seconds_total{job="node-exporter-platform-ec2",mode="idle",instance_id="i-XXXX"}[1m]))*100
```

Notes:
- On `ci.yaml`, `repo` is the **developer's repo**, not the manifest repo.
- `inputs_group` is not a reliable filter.
- `artifacts_info` holds names only.
- Runner Details' `workflow_link` var requires the `/attempts/<N>` suffix.

**Runner logs and metrics for a job**:
- `octl workflow runner-logs [--log-type elmnt|syslog] <job-URL | instance-id>` prints a VMUI link.
- The job summary annotation links to Runner Details (CPU/mem + logs for that runner).
- For DC/on-prem jobs: **Runner Dispatcher — Job Lookup**. Paste the job URL into `job_link` to see which dispatcher/host ran it, queue and assignment times.

## 8. Test-results pipelines
Source: `how-to/qa-test-results.md`, `tests-results-dashboard.md`; i-want-a-grafana-dashboard `references/queries.md`.

| | Legacy (VM) | **QA / current (Postgres)** |
|---|---|---|
| Input | job artifact `…-<jobid>-<attempt>-tests_report` containing `report.md` | artifacts ending **`-test_output`** (pytest **JUnit XML**) + job-log groups `Test_Manifest_Name` / `Test_Manifest_Project` / `Test_Manifest` |
| Processor | github_exporter → `test_results` metric | `ip.infra.val.test-results-exporter`: HMAC webhook → `ingest.webhook_events` (**202**) → worker every 10 s → `analytics.*` in one transaction |
| Dashboard | Tests results `/d/begsnb827dse8d` (default 12h, project=iridium, manifest=drv-ci.xml) | QA Test Results (SW org `dev3619-test-results`) + `[QA] Tests Results/*` |
| Idempotency | webhook returns 200 when done, 503 while in progress, 500 when retryable; done marker in `processed_runs` | per-run transaction |

**Postgres schema `analytics`**:

| Table | Columns |
|---|---|
| `workflow_info` | `workflow_run_id, repo, branch, event, pr, conclusion, workflow_link, completed_at` |
| `job_info` | `job_id, workflow_run_id, job_name, project, manifest_name, manifest_version, revision, phase, completed_at` |
| `step_info` | per step |
| `test_results` | `test_result_id, job_id, workflow_run_id, test_group, test_name, result` (passed/failed/error/skipped), `time`, promoted dims `chip_ver, plat, model_name, tp, pp, benchmark, platform…` |
| `test_metrics` | `test_result_id, job_id, workflow_run_id, metric_name, metric_value NUMERIC, source_scale, source_unit, completed_at` |
| `dashboard_dim_group`, `dashboard_dim_test` | variable-picker dimensions (written only when the manifest log groups exist) |

- **Producers**: in pytest, `record_property("latency_ms", 12.3)` (vllm-atl: `junit_metrics.stash_config_labels`) → JUnit `<property>`.
- **Field registry** `app/fields.py`:
  - `_metric(...)` needs no migration.
  - `_column(...)` needs `python -m app.schema emit <col> --title add_<col> --write`. Gate with `make check`.
  - An undeclared numeric property becomes a metric with unknown scale/unit. A non-numeric one is dropped.
  - Out-of-range values become NULL (`property_out_of_range_total`). Missing context is counted in `tests_without_context_total`.
- **psql (RO)**: secret `test-results/rds-credentials-ro` in ns `monitoring`, cluster `retool-eu-central-1`; `kubectl run psql --rm -it --image=postgres:16 …`.

```sql
-- pass rate per test group, last 7 days (always bound by completed_at / job_id)
SELECT t.test_group,
       count(*) FILTER (WHERE t.result='passed')::float8 / count(*) AS pass_rate
FROM analytics.test_results t JOIN analytics.job_info j USING (job_id)
WHERE j.completed_at > now() - interval '7 days' AND j.project = 'iridium'
GROUP BY 1 ORDER BY 2;
-- a perf metric trend (Grafana time series)
SELECT m.completed_at AS time, m.metric_value::float8 AS value
FROM analytics.test_metrics m JOIN analytics.test_results t USING (test_result_id)
WHERE m.metric_name = 'latency_ms' AND t.test_name = ANY(ARRAY[${test:sqlstring}]::text[])
  AND $__timeFilter(m.completed_at) ORDER BY 1;
```

## 9. Logs: VictoriaLogs + Vector (+ CloudWatch for EKS)
Source: `how-to/find-and-read-logs.md`, `get-runner-logs-metrics.md`; Confluence 794853403 "Victoria Logs", 792494146 "Vector Logging Overview"; live `/select/logsql/*` probes.

- **Shipping**: Vector → `https://vmlogs.elmnt.local/insert/jsonline`. Vector sources:
  - `kubernetes_logs`
  - `journald_system`
  - `journald_kernel`
  - `elmnt_logs` = `/var/log/elmnt/**/*.{txt,log,json}` (live data also shows `/var/log/atl/*.json`)
- **To get your app's logs collected**: write to `/var/log/elmnt/<app>/…` on hosts or to stdout in the on-prem K8s clusters.
- **Read**: VMUI `https://vmlogs.elmnt.local/select/vmui/`, Grafana Explore with datasource `victorialogs`, the `Logs/Cluster Logs` and `Logs/Hosts` dashboards, or the API.
- **Stream fields** (live): `env, cluster, container, namespace, node, rack, site, app` (plus `event, module, agent`).
  Other fields: `instance_id, host, file, filename, role, source_type, severity, level, msg, _msg`, and journald `_SYSTEMD_UNIT, UNIT, SYSLOG_IDENTIFIER, PRIORITY, _PID`.
- **Live `cluster` values**: `ec2`, `pldm`, `ocean`, `ocean-staging`, `ocean-dev`, `sival`, `VLSI`, `Production`, `Validation`, `n/a`.
  ⚠ The docs say `ec2-instances`; **live data uses `ec2`**, so check with `field_values`.
- **Namespaces** seen: `elmnt` (dominant), `elmnt-system`, `elmnt-core`, `elmnt-observability`, `host-system`, `kube-system`, `kubevirt`, `bmc`, `runner-dispatcher`, `sw-platform-group`, `ai-group`.
- `severity` is mostly empty. Filter on text instead of relying on it.

| Need | LogsQL |
|---|---|
| A pod | `_time:1h pod:"my-pod-abc"` |
| A PLDM EDK host | `_time:6h cluster:"pldm" node:"pldm-edk0-z2a"` |
| Kernel log of a node | `_time:1h app:"kernel" node:"<node>"` |
| An EC2 runner / VM | `_time:1d instance_id:"i-0123456789abcdef0"` |
| Runtime lib logs | `_time:1h namespace:"elmnt" app:"runtime_lib_log"` |
| Errors of an app | `_time:30m app:"my-app" "connection refused"` · `_time:1h app:"my-app" (error OR fatal) \| stats count() by (node)` |
| systemd unit | `_time:1h _SYSTEMD_UNIT:"docker.service" node:"<node>"` |

```bash
curl -sk https://vmlogs.elmnt.local/select/logsql/query --data-urlencode 'query=_time:15m cluster:"pldm" error' -d limit=20
curl -sk https://vmlogs.elmnt.local/select/logsql/field_values --data-urlencode 'query=_time:1h' -d field=cluster
curl -sk https://vmlogs.elmnt.local/select/logsql/stream_field_names --data-urlencode 'query=_time:1h'
```

- **EKS (`retool-eu-central-1`) workloads (Hive, platform-webhooks, exporters, Grafana…) are NOT in VictoriaLogs.**
  They go through fluent-bit (ns `amazon-cloudwatch`) to CloudWatch Logs group `/aws/containerinsights/retool-eu-central-1/application`, or use `kubectl logs`.
- The agent gateway / Platform Bot emits `bot_turn` / `bot_feedback` events to VL (Platform Bot dashboards).
- There is no OTLP tracing backend in production yet (an `otel-collector` deploy dir exists).
- ⚠ **Loki** (Confluence 47841289) is **superseded** by VictoriaLogs. Ignore the Loki instructions.

## 10. EC2 / GPU / GitHub monitoring, KPIs, CloudWatch
Source: Confluence 941883447 "EC2 Monitoring", 99025264 "GitHub Monitoring", 43483756 "KPIs", 43418217 "CloudWatch & Grafana", 1611792573 "Action Items…"; portal `grafana.ts`.

- **EC2**:
  - User-owned EC2 instances that sit **idle** (low CPU, 1 h rolling average) are **gracefully stopped**. EBS persists. See **EC2 Auto-Stop Monitor** (CloudWatch).
  - Personal cost: **EC2 Personal VM — Usage & Cost** (`var-owner=<lowercased user>`, `from=now/M`). Data comes from FinOps PG (`cur-ingester` CUR data) + VM.
  - Runner right-sizing / spot: the `main/AWS/*` dashboards.
- **GPU**: NVIDIA DCGM exporter is deployed by the GPU operator (`ip.infra.devops.apps/zarf/k8s/base/elmnt-system/nvidia-gpu-operator/templates/vmservicescrape-dcgm-exporter.yaml`).
  - Labels: `gpu, UUID, modelName, Hostname, pci_bus_id, node, cluster, exported_pod, exported_namespace, DCGM_FI_DRIVER_VERSION`.
  - Example query: `avg by (node,gpu)(DCGM_FI_DEV_GPU_UTIL{node="<node>"})`. The portal deep-links using `exported_pod`.
- **GitHub**:
  - `github-pr-stats` metrics feed the `main/GitHub PR Stats/*` dashboards (merge queue health, review wait, CI per PR/repo, CI init step).
  - Watch `github_rate_limit_remaining`.
  - The 2024 Confluence page is an aspirational list. The implemented reality is the metrics in §6.
- **KPIs** (Confluence 43483756): build success rate, build time, deployment frequency, MTTR, change failure rate, test coverage, utilization, throughput, lead time, security compliance.
  **No implemented uptime/MTTF/MTTR dashboard was found.** Treat them as conceptual; compute ad hoc from `workflow_info`/`job_info` and the `probe_success` (URL_Checks) series.
- **CloudWatch**: datasource `deetazrrcw000c` (Main) / `cloudwatch-sw` (SW) plus `cloudwatch-exporter` (job) for AWS service metrics (RDS, EC2, EKS).
  Confluence 43418217 is a generic 2024 how-to.
- **Stability action items** (Confluence 1611792573): VM metrics + alerts done (DEV-3896); chip + WEKA alerts next; DGX alerts open.

## 11. How to answer common questions
Source: synthesis of §§1–10.

| Question | Answer path |
|---|---|
| "Why is my runner/job slow?" | 1) Queue time: `runner_wait_seconds{job_link="<job URL>"}` (or Runner Dispatcher Job Lookup for DC). 2) Machine: `job_info{job_link=…}` → `instance_type`, `runner_name`; open Runner Details with `var-workflow_link=<run>/attempts/<N>` and the job's time range for CPU/mem saturation. 3) Steps: `step_info` / CI init-step dashboard. 4) Compare `duration` with peers via `job_info{job_name=…}`. |
| "Where are my job logs?" | GitHub Actions log first. Runner host logs: `octl workflow runner-logs <job URL>` → VMUI link (`--log-type syslog` for journald), or LogsQL `instance_id:"i-…"`. PLDM: `cluster:"pldm" node:"<edk>"`. EKS service: CloudWatch `/aws/containerinsights/retool-eu-central-1/application`. |
| "Where are my app's logs on a host/cluster?" | Write to `/var/log/elmnt/…` or stdout; query `app:"<app>"` (+ `cluster`, `node`). Find names with `field_values`. |
| "Add an alert" | PR to `devops-grafana-alerts/teams/<team>/values.yaml` (metric/SQL) or `clusters/<c>/teams/<team>/values.yaml` (log). Reuse or define a contact point; Teams needs a Platform ticket for the SM secret. Or ask `@grafana-alerts`. Verify on Alerts Overview. |
| "Add a dashboard / panel" | Save-as into `[WIP]` → edit → export → keep the uid → PR to retool-terraform `modules/infra/grafana/dashboards/<org>/<Folder>/`. Use the `i-want-a-grafana-dashboard` skill for queries and a starter JSON. |
| "Publish my test metrics" | Preferred: pytest `record_property` → JUnit artifact `*-test_output` + `Test_Manifest*` log groups → Postgres `analytics.test_metrics` → QA dashboards. Alternative: push Prom text to `vmagent-sw` (4-week retention). |
| "Did test X pass / is it flaky?" | `analytics.test_results` (SQL §8) or `[QA] Tests Results/Health/quality-integration-flaky`. Legacy: Tests results dashboard. |
| "Who is using GPU on node N?" | DCGM dashboard with `var-node=N`; `DCGM_FI_DEV_GPU_UTIL` by `exported_pod`. |
| "How much does my VM cost?" | EC2 Personal VM — Usage & Cost, `var-owner=<user>`, `from=now/M`. |
| "Is the PLDM queue stuck?" | PLDM Job Queue dashboard (`/d/pldm-job-queue-v1`); logs `cluster:"pldm"`. |
| "Cron workflow history for my team" | Cron Workflows dashboard `var-team=<team>` (`var-workflow_name` is a numeric id). |
| "Which metrics exist for X?" | `curl -sk 'https://vmsingle.elmnt.local/api/v1/label/__name__/values' \| tr , '\n' \| grep -i X`; jobs via `/api/v1/label/job/values`. |

## 12. Pitfalls
Source: docs + live probes + skills.

1. **Changing a dashboard uid** destroys and recreates it, which breaks every link. **UI edits to managed dashboards are silently reverted** on the next apply.
2. Grafana roles come from SSO groups. Log out and back in after a group change. Nobody gets Admin; use `[WIP]` for drafts.
3. **Series-limit errors**: `/api/v1/series` or broad queries on `job_info`, `step_info`, `runner_wait_seconds` exceed **30 000 series**; `test_results` exceeds 1.5 M even over 1 h.
   Always add selective labels (`workflow_name`, `repo`, `job_link`, `project`).
4. github_exporter labels such as `duration`/`wait_seconds` are **label strings**, not values. `repo` on `ci.yaml` is the dev repo. `inputs_group` is unreliable.
5. Push to **vmagent-sw**, not vmsingle-sw (403). 401 means bad creds. Creds exist only in the `production` CI environment. SW data expires after **4 weeks**.
6. `VictoriaMetricsTracker` without `--victoria-upload` / `EL_VMAGENT_URL` only writes local JSONL, so nothing reaches Grafana.
7. QA exporter: without the `Test_Manifest*` log groups, tests land with no dashboard dims (`tests_without_context_total`). Undeclared non-numeric properties are dropped.
8. Grafana SQL:
   - A custom `allValue` bypasses `:sqlstring`. Use `= ANY(ARRAY[${var:sqlstring}]::text[])`, not regex.
   - Cast `::float8`.
   - `from`/`to` macros may need `YYYYMMDD`.
   - Always bound `completed_at`/`job_id` (tens of millions of rows).
9. LogsQL: always add a `_time:` filter. `cluster` is `ec2`, not `ec2-instances`. `severity` is usually empty.
10. EKS app logs are in **CloudWatch**, not VictoriaLogs. Loki docs are stale.
11. Alert YAML: Go-template `$` must be `$$`. Never edit `templates/*`. uid must be ≤40 chars and lowercase. `raw_sql` must return a `value` column.
12. Runner Details' `workflow_link` must end with `/attempts/<N>`. Cron Workflows' `workflow_name` var takes a numeric id.
13. The `grafana-alerts` skill's repo layout (`zarf/grafana-alerts/k8s/base`) is outdated. Use `zarf/k8s/eks/monitoring/devops-grafana-alerts/`.

## 13. Owner / escalation
Source: `how-to/contact-us.md`; docs front-matter; Confluence authorship.

- **Owner**: Platform team, GitHub `Elmnt-Internal/sw-platform-sg`. Docs: https://elmnt-internal.github.io/ip.infra.devops.apps/ (source `ip.infra.devops.apps/docs/`).
- **Support order**: 1) PlatformDevBot (private chat or channel). 2) Teams **Platform - hub**. 3) Mention `@Platform-Team`. News: Teams **Platform - news**.
- **Tickets**: service requests (e.g. a Teams webhook secret, Grafana org access, new team-monitoring instance) → Jira **DRR**. Bugs/features → Jira **DEV**.
- **Code owners**: `dashboards/sw/` → SW CODEOWNERS; the rest of retool-terraform and devops-grafana-alerts → Platform.
  The QA exporter belongs to the validation/QA team (`ip.infra.val.*`).
- **Confluence authors** (context, possibly stale): Doron Sofer (VictoriaLogs/VictoriaMetrics/Vector), Ezra Tameno (Grafana), Tal Cohen.

## 14. Deeper skills & sources
Source: ai_group toolkit marketplace (`Elmnt-Internal/module.infra.ai_group.toolkit`), install with `octl plugins manage`.

| Skill | Plugin | Use for |
|---|---|---|
| `grafana-alerts` | `platform-ops` (`plugins/teams/platform/platform-ops`) | full alert/contact-point schema, Teams templates, review checklist (⚠ old path layout) |
| `i-want-a-grafana-dashboard` | `i-want-a-grafana-dashboard` (`plugins/general/…`) | dashboard design → `references/queries.md` (Postgres `analytics` + PromQL patterns), `dashboard-starter.json`, SW org 6, VM→PG migration |
| `@grafana-alerts`, workflow-metrics agents | docs `agents/grafana-alerts.md`, `agents/workflow-metrics.md` | natural-language alert PRs, CI metric questions |

Key docs (`ip.infra.devops.apps/docs/how-to/`): `create-grafana-alerts.md`, `create-grafana-log-alerts.md`, `grafana-gitops.md`, `sw-monitoring-victoriametrics.md`,
`tests-results-dashboard.md`, `qa-test-results.md`, `find-and-read-logs.md`, `get-runner-logs-metrics.md`.
Confluence: 169443335 Grafana · 794853403 Victoria Logs · 1075380225 Victoria Metrics · 792494146 Vector · 941883447 EC2 Monitoring · 99025264 GitHub Monitoring · 43483756 KPIs ·
43418217 CloudWatch & Grafana · 1611792573 Stability action items · 47841289 Loki (stale).
