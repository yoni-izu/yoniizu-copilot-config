# Infrastructure & Compute — AWS, Runners, Ocean DC, Workstation, Provisioning

The Platform/DevOps team (`Elmnt-Internal/sw-platform-sg`) runs two compute planes:
- **AWS**, in account `891377201015` / region `eu-central-1`. It hosts EC2 dev VMs, the self-hosted GitHub runners, the EKS management cluster `retool-eu-central-1`, S3, ECR and CodeArtifact.
- **Ocean**, an on-prem Kubernetes DC at KFS. It has DGX B200, A1 accelerator and GP CPU nodes, KubeVirt VMs and Kueue.

Everything is code: Packer, Ansible and Kubespray (`provisioning`), Terraform (`aws.terraform`, `retool-terraform`, `iac`) and ArgoCD GitOps (`apps/zarf/k8s`). The canonical docs are https://elmnt-internal.github.io/ip.infra.devops.apps/ (source: `ip.infra.devops.apps/docs`). This file condenses them. When the docs and Confluence disagree, the GitHub docs win.

## TOC
1. [Platform repository map](#1-platform-repository-map)
2. [AWS account, SSO, console, roles](#2-aws-account-sso-console-roles)
3. [EC2 VMs, key pairs, EFA, quotas, disks](#3-ec2-vms-key-pairs-efa-quotas-disks)
4. [S3, ECR, CodeArtifact, Secrets Manager, naming and tags](#4-s3-ecr-codeartifact-secrets-manager-naming-and-tags)
5. [AWS platform upgrades (EKS, AMIs, Cilium, Karpenter, kagent)](#5-aws-platform-upgrades)
6. [GitHub Actions runners (AWS pools, labels, dispatcher)](#6-github-actions-runners)
7. [Ocean Kubernetes DC](#7-ocean-kubernetes-dc)
8. [Ocean architecture, node provisioning, NodeDebug, GitOps](#8-ocean-architecture-node-provisioning-nodedebug-gitops)
9. [Dev workstation (WSL)](#9-dev-workstation-wsl)
10. [Provisioning and IaC (Packer, Ansible/AWX, Terraform, ArgoCD, Helm CI)](#10-provisioning-and-iac)
11. [Kernel upgrade flow and enabling a new OS/kernel/arch](#11-kernel-upgrade-flow-and-new-oskernelarch)
12. [Storage](#12-storage)
13. [Lab hardware hosts (PLDM/FPGA/Lauterbach)](#13-lab-hardware-hosts)
14. [Troubleshooting (symptom/error → fix)](#14-troubleshooting-symptomerror--fix)
15. [Pitfalls](#15-pitfalls)
16. [Owner / escalation](#16-owner--escalation)
17. [Source contradictions](#17-source-contradictions)

---

## 1. Platform repository map
Source: `docs/repositories.md`; repo skills in `ip.infra.devops.apps/.github/skills/`; `docs/agents/github-iac.md`. Every repo below is owned by `Elmnt-Internal/sw-platform-sg`.

| Repo (`Elmnt-Internal/…`) | Owns |
|---|---|
| `ip.infra.devops.apps` | `octl`, `elbuilder`, the custom `github-runner`, the Portal backend, the docs site, and `zarf/k8s` (ArgoCD/Helm manifests: `eks/`, `base/`, `clusters/{ocean,ocean-staging,ocean-dev,harbor,eks}`). Services include `kubevirt-cdi`, `accel-admission-controller`, `drr-watchdog`, `packer-cleaner`, `node-debug-controller` and runner-dispatcher. |
| `ip.infra.devops.workflow` | Reusable GitHub Actions workflows (`ci.yaml`, `runner_tests.yaml`, …) |
| `ip.infra.devops.workflow-entry` | Entry and dispatchable workflows that product repos call |
| `ip.infra.devops.aws.terraform` | AWS account Terraform: IAM, `modules/iam/projects-roles/main.tf`, `project-<group>-role` modules |
| `ip.infra.devops.aws.github-runners` | Runner pool configs `base/templates/runner-configs/<pool>.yaml`, the `bump-ami.yml` workflow |
| `ip.infra.devops.aws.terraform-aws-github-runner-fork` | Internal fork of the upstream `terraform-aws-github-runner` module |
| `ip.infra.devops.docker-images` | CI and dev images: `docker-images/software/docker-bake.hcl`, `shared/kernels/ubuntu-{VER}.dockerfile` |
| `ip.infra.devops.provisioning` | Packer AMIs and golden images, Ansible roles (kernel, Vector, …), Kubespray and Cilium, WSL images `WSL/wsl-u24-*`, inventories `ansible/inventory/{pldm,fpga}`, the `packer.yml` workflow |
| `ip.infra.devops.retool-terraform` | The EKS management plane `retool-eu-central-1`: ArgoCD, VictoriaLogs/Metrics, kagent. Plan runs on the PR, apply runs on merge (`ci-merge.yml`), and a nightly `terraform-sync` reverts drift. |
| `ip.infra.devops.vigor` | Gatestage Portal, ArgoCD pipelines, image management |
| `ip.infra.devops.iac` | GitHub org config as code, 100% Terraform: `terraform/organizations/<org>/<prefix>/<repo_type>/repo_<name>.tf`. Its agent and skills (`create/update/delete-github-repo`, `manage-github-teams`, `drr-naming-convention`) only open PRs. |
| `ip.infra.devops.regcli` | Register and memory access CLI |
| `ip.infra.devops.misc` | Unsupported side tools |
| `manifest.test1`, `manifest.test2` | Test repos for the manifest and CI flow |
| `ip.infra.devops.artifactory.terraform` | Artifactory PoC IaC (see the architecture/artifactory-prd docs) |
| `module.devops.configuration` / `ip.infra.sw.manifests` | Per-project `config.yaml` and manifest XML. These are CI inputs, not infra. |

Repo skills in `apps/.github/skills/` that matter here: `helm-chart-ci` (source of truth: "Helm Chart CI/CD Flow" in `zarf/k8s/README.md`), `node-debug-controller` (NodeDebug CRs), `deploy-service` (ArgoCD onboarding), `kernel-upgrade`, `github-iac`.

---

## 2. AWS account, SSO, console, roles
Source: `docs/how-to/configure-aws-sso.md`, `login-to-aws-console.md`, `assign-aws-permission-set-to-group.md`; plugin skill `platform-ops/aws-access`; Confluence 1311080571.

| Item | Value |
|---|---|
| Account / region | `891377201015` / `eu-central-1` |
| CLI SSO start URL | `https://elmnt.awsapps.com/start/`. The `sso-session` is named `profile-elmnt`. |
| CLI profile name | **`devops-cli`**. octl hard-depends on this name. |
| Console portal | `https://d-93674c7e01.awsapps.com/start` (Azure/Entra SAML) |
| Session length | 12 h. After that you get `ExpiredToken`; re-run the login. |
| Org | GitHub `Elmnt-Internal`; Portal at `retool.elmnt.local` |

```ini
# ~/.aws/config (docs version)
[sso-session profile-elmnt]
sso_start_url = https://elmnt.awsapps.com/start/
sso_region = eu-west-1          # aws-access skill says eu-central-1, see §17
sso_registration_scopes = sso:account:access
[profile devops-cli]
sso_session = profile-elmnt
sso_account_id = 891377201015
sso_role_name = ElmntSoftwareDeveloper   # pick your group's role
region = eu-central-1
```
```bash
aws sso login --profile devops-cli && aws sts get-caller-identity --profile devops-cli
```

- **SSO role names per group:** `ElmntSoftwareDeveloper`, `ElmntVlsiUsers`, `ElmntAIDevelopers`, `ElmntValidationUsers`, `ElmntQualUsers`, `ElmntArchDevelopers` (the console doc says `ElmntArchUsers`), `ElmntITUsers`.
- **Console switch-role targets:** `role-group-<grp>` (`role-group-sw|vlsi|ai`); `role-project-<project>` (project S3, ECR push); `role-ec2-basic` (volume resize); `role-admin` / `AWSAdministratorAccess` (Platform only).
- **Adding a permission set for a new group** (IT + Platform):
  (1) IT creates SG `project-<project>-<group>-SG` and assigns it to the Azure "AWS" app;
  (2) add the SG to the project claim's Scoped Groups;
  (3) add claim `AccessControl:Group<group>` (namespace `https://aws.amazon.com/SAML/Attributes`, value `"true"`);
  (4) create a permission set in IAM Identity Center in the `awsreseller11` account and assign it to the SW account;
  (5) create `role-project-<project>` in the SW account;
  (6) add Terraform module `project-<group>-role` to `Projects-roles` in `aws.terraform`.
- **Overnight/agentic sessions** (Confluence 1311080571): set `ELMCLI_OFFLINE_MODE=1` and `ELMCLI_NON_INTERACTIVE=1`, prefer `ws build --remote`. DEV-3479 added an SSO-expiry command; DEV-3310 added refresh-token reuse. `elmcli` is the octl lineage name.
- **To use `gh` on EC2 or PLDM:** run `gh auth login`, or export `GITHUB_TOKEN`, then `octl update` (`integrate-github-personal-aws-or-pldm.md`).

---

## 3. EC2 VMs, key pairs, EFA, quotas, disks
Source: `add-key-pair-aws-ec2.md`, `request-efa-supported-vms.md`, `octl vm create --help` (probed), the aws-access skill.

- **Key pair (one time):** assume `role-group-sw` → EC2 → Key pairs → Actions → Import; name it with your full name (`firstname-lastname`) and paste `~/.ssh/id_ed25519.pub`. octl selects the key by name.
- **`octl vm create`:**
  - Flags: `--flavor`, `--list-flavors`, `--ami`, `--disk`, `--features kvm`, `--replicas`, `--placement-group`, `--scale-up|--scale-out`, `-t k=v`, `-d`.
  - `--lease` default **8h**, max **72h**; renew with `octl vm lease renew`. Other subcommands: `list|start|stop|delete|lease`.
  - Only **one** personal EC2 instance at a time: delete it and wait for `terminated`. Subnet: `sw-private-<az>`.
- **EFA (multi-host RDMA):** `octl vm create --flavor c5n.9xlarge --replicas 2 [--placement-group]`.
  - EFA flavors: `c5n.9xlarge`, `c6i.32xlarge`, `m7i.48xlarge`, `m8id.96xlarge`.
  - Verify with `/opt/amazon/efa/bin/fi_info -p efa`, then `fi_pingpong -p efa` (server, then client `<server-ip>`).
- **Quotas, spot, on-demand:** runner pools are **spot by default**, with `.on-demand` variants (§6). Large and EFA flavors are capped by per-family EC2 vCPU quotas that Platform raises via AWS Support; ask in DevOps-Support rather than retrying.
- **IAM limits:** roles and permission sets are Terraform-managed (never hand-create IAM); request access via **DRR**. `role-devops-maintenance` (packer-cleaner) uses a **15-minute** session.
- **Growing a VM disk:** with `role-ec2-basic`, run `aws ec2 modify-volume --volume-id <id> --size <GB>`, then reboot. Max **500 GB**, **4 modifications per 24h**, never shrinks.
- **Persistent storage:** EFS `/mnt/<USERNAME>`, created on the first `octl vm create` (older VMs don't get it); `/mnt/shared_cache` for cross-user sharing. See §12.

---

## 4. S3, ECR, CodeArtifact, Secrets Manager, naming and tags
Source: the platform-context and aws-access skills, `access-precomputed-references-bucket.md`, `how-to/dc/mirror-image-to-ecr.md`, `packer-cleaner.md`, `architecture/artifactory-prd/storage-comparison.md`, `resource-naming-convention.md`, Confluence 501678139.

**S3 buckets** (bucket names in the GitHub docs take precedence):

| Bucket | Use |
|---|---|
| `s3-github-action-package-staging` | CI artifact **staging**. Builds upload here. |
| `s3-github-action-package` | CI artifact **production**. L1 pass promotes here; `octl ws init/build` downloads from here. |
| `s3-elmnt-github-actions-cache` | GitHub Actions cache |
| `s3-elmnt-build-cache` | Bazel remote cache backend (`bazel-cache.elmnt.local`) |
| `s3-sw-external-artifacts` | External deps referenced by manifests (`dep_type="external"`) |
| `s3-sw-general` | Shared test data (for example `tests-data/…`) |
| `s3-collab-artifacts` | Exported local manifests (`--upload`) |
| `s3-devops-general` | Platform: `linux-kernel/{aws,generic,mainline,kernel.org,source}/v…/{amd64,arm64}/`, `kubevirt-images/`, `wsl-images/u24-6.18/wsl-u24-6.18-package.7z` |
| `s3-elmnt-precomputed-references` (`-staging`) | Per-project `<project>/vcd/` (365-day expiry), `<project>/general/` (60d), `iridium/fw-build-size-reports/` (60d). Access needs `role-project-<project>`. CI reaches it via the `github-actions` IAM user trust. |

**ECR:**
- Registry `891377201015.dkr.ecr.eu-central-1.amazonaws.com`; path `<project>/<image>:<tag>`; base images `<project>/ubuntu:24.04-{base-aws,test,coverity}_{x86_64,aarch64}`.
- Push: assume `role-project-<project>`, then
  `aws ecr get-login-password --region eu-central-1 | docker login --username AWS --password-stdin 891377201015.dkr.ecr.eu-central-1.amazonaws.com`.
- **Mirroring for Ocean.** DC namespaces admit **only ECR images**. To use a public image:
  ```bash
  crane copy --platform linux/amd64 docker.io/library/<img>:<tag> \
    891377201015.dkr.ecr.eu-central-1.amazonaws.com/devops/third-parties/<img>:<tag>
  ```
  - Tags are IMMUTABLE. Mirroring needs an ECR-write profile such as `AWSAdministratorAccess` (`devops-cli` can't write), so ask Platform.
  - Pods pull with `imagePullSecrets: [{name: platform-registry}]`, synced by External Secrets.
- **ArgoCD Image Updater** polls ECR every 3 min (IRSA) and commits semver bumps to Git. It ignores `latest`, `dev` and `test`.

**CodeArtifact (Python):**
- Domain `elmnt` (`codeartifact-elmnt-domain` in the audit); `internal-packages` with upstream `external-packages` (proxies PyPI). We publish about 8 wheels (`torch`, `torchvision`, `torchao` `+cpu`). No retention policy. Get a token:
  ```bash
  export CODEARTIFACT_AUTH_TOKEN=$(aws codeartifact get-authorization-token --domain elmnt \
    --domain-owner 891377201015 --query authorizationToken --output text --profile devops-cli)
  export UV_INDEX_URL="https://aws:${CODEARTIFACT_AUTH_TOKEN}@elmnt-891377201015.d.codeartifact.eu-central-1.amazonaws.com/pypi/python-packages/simple/"
  ```
- In CI, octl sets `UV_INDEX_URL` automatically.

**Secrets Manager:** service credentials, for example `prod/github/app/devops-maintenance` (packer-cleaner's GitHub App). External Secrets syncs into K8s (`platform-registry`). Never put secrets in EFS, S3 or Git.

**Naming:** `resource-naming-convention.md` is the **GitHub repo** naming convention (`drr-naming-convention` skill) and supersedes Confluence 1261469707. AWS names follow existing patterns: `role-group-*`, `role-project-*`, `s3-<scope>-<purpose>`, `project-<project>-<group>-SG`.

**Cost tags** (Confluence 501678139). Apply to all resources:

| Tag | Example values |
|---|---|
| `ElmntDepartment` | `SW`, `AI`, `Validation` |
| `Component` | `Retool`, `EKS`, `SW` |
| `Service` | user or service name |

In Terraform: `locals { base_tags = {...} }` + `tags = merge(local.base_tags, {Name = "..."})`. `octl vm create -t` reserves `owner`, `owner_session`, `owner_email`, `Name`.

---

## 5. AWS platform upgrades
Source: `check-aws-platform-upgrades.md`, `check-a-kagent-upgrade.md`, `workflows/packer-cleaner.md`, Confluence 1661501664 and 1698168861.

- **EKS cluster** `retool-eu-central-1` (`ip.infra.devops.retool-terraform`): control-plane version is `k8s_cluster_version` in `prod.tfvars`. Upgrade **one minor at a time**. **Extended support costs about 6×**, so stay in standard. Kubelet may lag up to 3 minors; keep it ≤2.
- **Node groups:** pin `ami_id` in every `extra_node_groups` entry; roll nodes by adding a new group and draining the old. aws-auth ConfigMap is deprecated; use **access entries**.
- **Terraform:** retool-terraform has **no lock file**; AWS provider v6 must land **together** with EKS module v21. Plan on PR, apply on merge; manual console changes are reverted nightly by `terraform-sync`.
- **Cilium 1.20:** EKS uses **ENI mode** (pod subnets `100.65.0.0/18`, `100.65.64.0/18`), a `block-imds` policy and kube-proxy replacement. Ocean uses a VXLAN/tunnel overlay via Kubespray, with L2 announcements and kube-vip.
- **Addon dependency rules:** cert-manager ≥1.18 needs ingress-nginx ≥1.12.6; EKS 1.35 needs coredns ≥1.12.1. Check the full table in the doc before bumping.
- **Runner AMIs:** resolved from SSM `/github-action-runners/<pool>/runners/config/ami_id`. Rebuild by running provisioning `packer.yml` with `release=true`, which triggers `bump-ami` in github-runners. Prefixed kernel variants need `build_base=true`.
- **Packer cleaner** (daily 02:00 UTC):
  - Keeps the newest **10 AMIs per pattern**, plus any AMI that a launch template's `$Default` version references. AMIs used only by running instances are **not** protected.
  - Deletes `packer_*` key pairs older than 10 days.
  - Terminates `multi-runner*` instances older than 30 min that are unregistered, offline or idle. **Busy runners are never touched.**
- **kagent:** `kagent_version` in `retool-terraform/modules/elmnt/kagent`. Verify with `kubectl -n kagent get pods,agents`; roll back with `helm rollback kagent` + `helm rollback kagent-crds`.
- **Karpenter** (Confluence 1698168861, DEV-4976) is a **proposal, not decided**, to replace Cluster Autoscaler on EKS.
  - Why: it picks the cheapest fitting type across families and Spot, and drift replacement follows the NodePool disruption budget (for example 10%). Saving is about −$400/month before Spot.
  - Plan: pilot on 2× `t3a.medium` → move the 8xlarge, then xlarge groups → remove CA.
  - Right-size first: pods peak at about 8.7 GiB/CPU, while m6a nodes have 4 GiB/CPU.
  - Check `retool-terraform` for the current state before asserting it.

---

## 6. GitHub Actions runners
Source: `workflows/ci-config-reference.md` (runner pools, DC labels), `runner-dispatch-labels.md`, `dynamic-aws-runner-labels.md`, `architecture/runner-dispatcher*.md`, `enable-new-os-kernel-arch.md`.

**AWS pools** (`ip.infra.devops.aws.github-runners`, built on the `terraform-aws-github-runner` fork): all Ubuntu 24.04, all **spot** unless the label ends in `.on-demand`. Warm pools: `4xlarge-od`, `8xlarge-od`.

| Pool label | Notes |
|---|---|
| `cxi.4xlarge` | kernel 6.17; WSL-kernel variants `ubuntu-24-wsl-6-6` / `ubuntu-24-wsl-6-18` |
| `cxi.8xlarge` | 6.17, or 6.12 via `ubuntu-24-6-12` |
| `cxi.8xlarge-base` | stock 6.8 |
| `cxi.12xlarge`, `cxi.16xlarge`, `mxi.16xlarge`, `mxi.96xlarge` | x86_64 compute |
| `mxi.48xlarge` | EFA |
| `mxg.48xlarge` | **the only arm64 pool**: m8g Graviton4, EFA, 6.17 |
| `m8i.48xlarge` | full RMA on `efa-direct` |
| `m8id.8xlarge` / `m8id.48xlarge` / `m8id.96xlarge` | local NVMe at `/mnt/nvme-instance-store/cache` |
| `kvm`, `efa` (+ `cluster-placement-group`), `inf2`, `regular` | special-purpose |

- **Retired 2026-08 (DEV-2760):** the `c6g`, `c7g` and `t4g` Graviton pools. Jobs that request them **queue forever with no error**.
- **Kernel labels:** `ubuntu-24-6-12`, `ubuntu-24-6-17`, `ubuntu-24-wsl-6-6`, `ubuntu-24-wsl-6-18`. cxi/mxi pools do **not** register a bare `ubuntu-24`. Each pool's kernel label comes from `labelMatchers` in `runner-configs/<pool>.yaml` (for example `linux-cxi-8xlarge-k{VER}.yaml`).
- **Dynamic labels** `ghr-ec2-instance-type:<type>`, `ghr-ec2-ebs-volume-size:<GB>`, `ghr-ec2-image-id:<ami>` and custom `ghr-team:<x>` all **need Platform approval**; agents must not recommend them by default. They bypass warm pools (slower start).
- Set labels in the project `config.yaml` (manifests repo), not in workflow YAML.

**Runner dispatcher (Ocean / DC runners):** one image in two `--mode`s: a **router** in the EKS management cluster, plus one **dispatcher per DC**.
- Flow: platform-webhooks (`workflow_job`) → router → DB → dispatcher polls every 10 s and creates a KubeVirt VM or pod → Kueue gang-schedules. Reap runs every 30 s; sync every 1 min.
- **Routing labels:**

  | Label | Values / meaning |
  |---|---|
  | `dc=ocean` | Target DC |
  | `resource=vm\|pod` | Workload kind |
  | `namespace=<team>-group` | Team namespace |
  | `flavor=<a1.*\|dgx.*\|…>` | Required for `dc=ocean` |
  | `group_uuid`, `group_size`, `workflow_id`, `workflow_attempt` | Injected automatically |

- **Flavor is mandatory:** the default `c7i.8xlarge` is AWS, so a `dc=ocean` job without a flavor is rejected; a wrong flavor queues silently. Debug with Grafana "Runner Dispatcher — Job Lookup" (`https://grafana.elmnt.local/d/runner-dispatcher-lookup/...`).
- **Re-runs:** never freeze run identity in upstream job outputs; a re-run reuses a stale `workflow_attempt` and orphans the gang.
- Runner tests: `ip.infra.devops.workflow/.github/workflows/runner_tests.yaml`.

---

## 7. Ocean Kubernetes DC
Source: `docs/how-to/dc/{zero-to-ocean,kubeconfig,ocean-reference,ocean-gpu-jobs,ocean-accel-jobs,ocean-vms,ocean-octl-setup,mirror-image-to-ecr,ocean-troubleshooting}.md` (the Zero-To-Ocean Confluence series 1720189102 etc. mirrors these).

**Clusters:**

| Cluster | Role | API |
|---|---|---|
| `ocean` | **production** (KFS DC) | `https://api.ocean.elmnt.local:6443` |
| `ocean-staging` | staging, Platform-only | `api.ocean-staging…` |
| `ocean-dev` | dev, Platform-only | `api.ocean-dev…` |
| `harbor` | planned (CSR site) | n/a |

**Access (kubectl):**
1. Office network or VPN, with corporate DNS.
2. `kubectl krew install oidc-login`, with `${KREW_ROOT:-$HOME/.krew}/bin` on `PATH`.
3. Kubeconfig (issuer Dex `https://dex.elmnt.local`, client `kubernetes`, device-code flow) **must** be `~/.kube/ocean.yaml` with context `ocean`, because octl looks for exactly that. Token cache: `~/.kube/cache/oidc-login`.
4. `export KUBECONFIG=~/.kube/config:~/.kube/ocean.yaml; kubectl --context ocean get ns`

**Tenancy:** namespaces `<team>-group`, editable by members of `Elmnt-Internal:<team>-sg` (GitHub/Entra SG; request via **DRR**).

| Team ns | GPU share | A1 dies |
|---|---|---|
| `sw-platform-group` | 17% | 16 |
| `ai-group` | 49% | 16 |
| `sw-inference-workloads-group` | 34% | 16 |
| `sw-inference-stack-group` | — | 16 |

- Every container needs CPU and memory requests **and** limits because of the ResourceQuota (`kubectl get resourcequota -n <team>-group`).

**Node classes:**

| Class | Resource | Taint | Notes |
|---|---|---|---|
| **DGX** | `nvidia.com/gpu` (8× B200 per node); VMs use passthrough `nvidia.com/GB100_B200` | `dedicated=dgx` | `runtimeClassName: nvidia` |
| **A1** | `elmnt.com/accel` (dies: 4 per chip, 32 per sled) | `dedicated=a1` | a webhook rounds the request up to a multiple of 4 |
| **GP** | CPU | — | general purpose |

**Kueue** (quota + gang + TAS):
- LocalQueue `default` per team namespace, ClusterQueue `<team>-cq`, cohort `accel-inference`, ResourceFlavors `dgx-gpu` and `accel`. WorkloadPriorityClass `high-priority` = 1000.
- Label Jobs `kueue.x-k8s.io/queue-name: default` unless the namespace has `kueue.x-k8s.io/default-queue: default`. The old `accel` queue does **not** exist, so jobs aimed at it are never admitted.
- Kueue adds dgx/a1 taint tolerations only to workloads it manages; a bare pod outside a queue stays `Pending` on `untolerated taint`.
- A1 admission check `chip-topology-check`: `Ready`; `Retry` (not enough whole chips free yet, requeues); `Rejected` (can never fit).
- Topology: die → chip → CT → host → rack → leaf switch. Multi-rack scale-out must respect rail topology (same sled index → same leaf, DEV-3195). Borrowed A1 capacity can be **preempted** when the owner reclaims it.

**GPU Job template** (DGX):
```yaml
apiVersion: batch/v1
kind: Job
metadata: {name: gpu-hello, namespace: <team>-group, labels: {kueue.x-k8s.io/queue-name: default}}
spec:
  suspend: true          # Kueue unsuspends on admission
  backoffLimit: 0
  ttlSecondsAfterFinished: 3600
  template:
    spec:
      restartPolicy: Never
      runtimeClassName: nvidia
      containers:
      - name: cuda
        image: <ECR mirror of nvcr.io/nvidia/cuda:12.4.1-base-ubuntu22.04>
        command: ["bash","-c","nvidia-smi && sleep 30"]
        resources:
          requests: {cpu: "4", memory: 16Gi, nvidia.com/gpu: "1"}
          limits:   {cpu: "4", memory: 16Gi, nvidia.com/gpu: "1"}
```
- **A1 jobs:** same shape, with `elmnt.com/accel: "<dies>"` in requests and limits. For multi-node work use Kubeflow Trainer v2 / PyTorch `torchrun` (`ocean-accel-jobs.md`).

**Ocean VMs (KubeVirt + CDI):** prerequisites `octl login` (uses `devops-cli`) and `export OCTL_FEATURE=ocean-vm`.
- `octl vm create --flavor a1.small --node-selector any [--scale-up|--scale-out]`
  - `--node-selector` is required but **not applied**; `--lease` is **rejected** (no lease, delete the VM yourself); `--disk` is ignored (root disk 100Gi); `--replicas` creates 1.
  - `dgx.*` flavors always get **one** B200 passthrough GPU. VMs are admitted only in namespaces labelled `default-queue`.
- Multi-VM gangs: `octl workloads create --flavor <f> --kind vm|pod|ec2 -n <team>-group --replicas N --placement packed|spread`. `octl workloads` (alias `wl`) is hidden in preview and needs no `OCTL_FEATURE`.
- VM ops: `kubectl virt console|stop|start <vm> -n <team>-group`.
- **Golden images:** `kubevirt-cdi` `goldenImage.images` (from `s3-devops-general/kubevirt-images/`); opt in to a kernel variant with DataVolume annotation `golden-image.kubevirt.io/variant: k{VER}`.

**Flavors** (`octl vm create --list-flavors` prints the live list):

| Flavor | vCPU / Mem | Device |
|---|---|---|
| `a1.small` | 12 / 66Gi | 4 dies (1 chip) |
| `a1.2xsmall` … `a1.7xsmall` | 22–72 / 132–462Gi | 8–28 dies |
| `a1.large` | 88 / 541Gi | 32 dies (1 sled) |
| `a1.2xlarge` / `a1.3xlarge` / `a1.4xlarge` | 174 / 260 / 344 vCPU | 64 / 96 / 128 dies |
| `dgx.small` / `dgx.2xsmall` / `dgx.medium` / `dgx.large` | 26 / 54 / 108 / 216 vCPU | 1 / 2 / 4 / 8 GPUs (pods) |

**UIs:** Headlamp `https://headlamp.elmnt.local` (OIDC) · Grafana `https://grafana.elmnt.local` · VictoriaLogs `https://vmlogs.elmnt.local/select/vmui/` · AWX `https://awx.elmnt.local`.

---

## 8. Ocean architecture, node provisioning, NodeDebug, GitOps
Source:
- Confluence 1317273617 "Data Center — Current Status" (2026-07-01, DevOps_v19; versions may be stale) and 760152593 "Kubernetes Data Center".
- Repo skills `node-debug-controller` and `deploy-service`.

**Stack layers:**

| Layer | Contents |
|---|---|
| **L0** | Racks and sleds, A1, DGX |
| **L1** | Golden Packer image (Ubuntu 24.04 + agents + drivers), with per-class tuning in Ansible: kubelet reserved, CPU/Mem Manager, CPU isolation, IRQ affinity, hugepages, `tuned-adm` performance, swap off, apparmor off. The same base disk is reused for KubeVirt raw images. |
| **L2** | Kubespray (v2.26.0, K8s 1.33.5 at time of writing), Cilium (tunnel + L2 announcements), **Multus** (secondary networks, SR-IOV coexistence), kube-vip LB, strict-ARP |
| **L3** | ArgoCD |
| **L4** | Platform services, in namespace `elmnt-system`: KubeVirt, KubeVirt-CDI, Kueue, scheduler-plugins, OpenEBS-ZFS, device-plugin, accel-mutating-webhook, accel-admission-controller, accel-kueue, kubeflow-trainer. Identity: Dex/OIDC. Secrets: External Secrets. Pull registry: ECR. |

- **SR-IOV:** A1 nodes have an `eth0` VF pool, `mlx_pods` vs `mlx_vms`, selected by the label `elmnt.com/accel.workload.config`.
- **Device plugin:** discovers `/dev/accel/accelN`, advertises `elmnt.com/accel`, publishes the `AccelTopology` CRD, and does chip-aware `GetPreferredAllocation` (epic DEV-2320).

**Bootstrap order:**
1. Image the node with the golden image.
2. Run Kubespray `cluster.yml`. Its own ArgoCD addon is **disabled**.
3. The post-install playbook registers the cluster in ArgoCD and wires OIDC.
4. ArgoCD deploys everything else.

Provisioning owns the raw cluster; GitOps owns everything above it.

**Add a node** (IT ↔ DevOps; AWX + NetBox as source of truth):
- AWX workflow template 154, "Platform K8S Node Provision Workflow (Ocean — A1 Node)":
  1. Reimage OS
  2. Provision Node
  3. Preflight Check
  4. Update K8s Inventory (auto-PR + merge)
  5. Kubespray `scale.yml --limit=<node>,kube_control_plane[0]`
- Prerequisites: the node is in NetBox (cluster, IP, role) and reachable, and AWX has the `elmnt` SSH, GitHub App and AWS credentials.
- After the join, GitOps applies the `topology.kubernetes.io/{rack,zone}` labels from NetBox, and OpenEBS-ZFS initializes local storage.

**NodeDebug** (`nodedebug.elmnt.com/v1alpha1`, short name `nd`, cluster-scoped):
```yaml
apiVersion: nodedebug.elmnt.com/v1alpha1
kind: NodeDebug
metadata: {name: <node>}
spec: {nodeName: <node>, reason: "DEV-1234 ECC errors in dmesg", drainMode: Wait}  # Wait|Evict|None
```
- Workflow: `kubectl apply -f nd.yaml` → `kubectl get nd -o wide` → `kubectl delete nd <node>` to return the node.
- **drainMode:** `Wait` evicts service pods and waits for Jobs, VMs, runners and bare pods; `Evict` evicts everything; `None` only cordons, labels and taints.
- **Debug pods** need nodeSelector `node-debug/enabled: "true"` + `kubernetes.io/hostname`, and tolerations for `node-debug/enabled=true:NoSchedule` and `node.kubernetes.io/unschedulable`. A wildcard toleration does not count.
- **Rules:**
  - Never cordon or taint a debug node by hand. One NodeDebug per node. Control-plane nodes are rejected with `Forbidden`.
  - The node is claimed via the `rmc.elmnt.com/drain-owner` annotation; if another holder has it, the CR stays `Pending`. `LimitReached` means `--max-debug-nodes` was hit.
  - With `--vm-passthrough-on-debug`, A1 nodes flip to `vm-passthrough` and the VFs move `mlx_pods` → `mlx_vms`.

**ArgoCD GitOps:**
- ArgoCD runs **centrally on the EKS management cluster**.
- DC clusters are registered as cluster secrets labelled `env` and `cluster-name`, with branch-per-env (`main` / `staging` / `development`).
- SAML/Entra RBAC; the Platform team has write access.
- **Two planes:**
  - EKS: `zarf/k8s/eks/<group>/<app>/` (Kustomize or Helm, `eks-applicationset`, namespace = `<group>`).
  - DC: `zarf/k8s/base/<group>/<chart>/` (Helm umbrella), deployed to `ocean`, `ocean-dev`, `ocean-staging` and `harbor`.
  - In both, directories must be **exactly two levels deep**.
- Values merge order: chart `values.yaml` → `clusters/<cluster>/config.yaml` → `clusters/<cluster>/values-overwrite/<chart>/values.yaml`.

---

## 9. Dev workstation (WSL)
Source: `setup-wsl-dev-workstation.md`, `migrate-wsl-6.6-to-6.18.md`, `install-cato-certificate-wsl.md`, `fix-kind-wsl2-dataplane.md`.

- **Image:** Ubuntu 24.04 with custom WSL kernel 6.18 (older images use 6.6). Package `s3://s3-devops-general/wsl-images/u24-6.18/wsl-u24-6.18-package.7z` (download with `role-group-sw`), built from provisioning `WSL/wsl-u24-*` with `./build.sh`.
- **Install:** install WSL from the **Intune Company Portal** → reboot → unpack and run `installWSL2.ps1`, which writes `.wslconfig` with `networkingMode=mirrored` and `dnsTunneling=true`.
- **Migrating 6.6 → 6.18:**
  1. In the old distro, save the package list with `apt-mark showmanual`.
  2. Install the new distro.
  3. `wsl --mount --vhd <old ext4.vhdx>` and rsync `/home/<user>` across.
  4. Reinstall the packages.
  5. Restore VS Code/Copilot chats with `restore_vscode_chat_sessions.py --apply`.
- **Cato TLS interception** (symptom: `verify error … code 19` or self-signed chain):
  ```bash
  sudo mkdir -p /usr/local/share/ca-certificates/Cato
  sudo cp cato.crt /usr/local/share/ca-certificates/Cato/   # must be .crt (PEM)
  sudo update-ca-certificates
  ```
  - Tools with their own CA store (Python certifi, Node) may also need `REQUESTS_CA_BUNDLE`, `SSL_CERT_FILE` or `NODE_EXTRA_CA_CERTS`.
- **kind on WSL2: pods can't reach Services**
  1. Set `kubeProxyMode: iptables` in the kind config.
  2. `sudo modprobe` the `xt_*` / `nf_*` netfilter modules.
  3. Delete the kindnet and kube-proxy pods so they restart.
- WSL **cannot** mount EFS (`/mnt/<user>`, `/mnt/shared_cache`) because it has no VPC route.
- Clock skew (`InvalidIdentityToken`): `sudo hwclock --hctosys`.
- **Salt:** Platform docs do not cover Salt for workstations. Treat Salt-managed config as IT-owned and ask IT.

---

## 10. Provisioning and IaC
Source: `repositories.md`, `check-aws-platform-upgrades.md`, `packer-cleaner.md`, `docs/agents/github-iac.md`, the `helm-chart-ci` and `deploy-service` skills, Confluence 1317273617.

| Tool | Where | What |
|---|---|---|
| **Packer** | `provisioning` `packer.yml` | Runner, dev-VM and DC golden AMIs, plus KubeVirt images (x86_64 only). Inputs: `release`, `build_base`, `kernel_config`, `image_prefix`. |
| **Ansible** | `provisioning/ansible` | Roles (kernel, Vector log shipping, node tuning); inventories `pldm`, `fpga` and the Kubespray inventory |
| **AWX** | `https://awx.elmnt.local` | Runs the Ansible workflows (node provisioning WFT 154) |
| **NetBox** | — | Source of truth for DC inventory and topology labels |
| **Kubespray** | `provisioning` | DC cluster build and scale; installs Cilium |
| **Terraform** | `aws.terraform` (IAM, account), `aws.github-runners` + fork (runners), `retool-terraform` (EKS, ArgoCD, kagent, observability), `iac` (GitHub org) | Plan on PR, apply on merge |
| **ArgoCD** | `apps/zarf/k8s` | GitOps for EKS and DC |
| **Helm chart CI** | `apps/.github/workflows/helm-charts.yml` | Lint, publish charts to ECR channels, desired-state diff on the PR |
| **Docker images** | `docker-images` bake | CI and dev base images pushed to ECR |

**Helm chart CI rules** (from the `helm-chart-ci` skill):
- Both the chart path **and** its service code must match `on.pull_request.paths`.
- Values-only changes don't publish charts.
- Never commit prerelease versions to `Chart.yaml`.
- Diffs are desired-state vs desired-state, not against the live cluster.
- Use `${{ secrets.GITHUB_TOKEN }}`.

**GitHub IaC:** creating or changing repos and teams goes through `ip.infra.devops.iac` PRs, which the `github-iac` agent only opens. Never click-ops in the GitHub UI, because Terraform will revert it.

---

## 11. Kernel upgrade flow and new OS/kernel/arch
Source: `docs/workflows/kernel-upgrade.md`, `docs/agents/kernel-upgrade.md`, plugin skill `platform-ops/kernel-upgrade`, `enable-new-os-kernel-arch.md`.

**Model:** the new kernel is deployed **in parallel**, as prefixed AMIs plus new labels. The default is never replaced until promotion.

| Variable | Meaning |
|---|---|
| `VER` | `{MAJOR}{MINOR}`, for example `619` |
| `MAJOR_MINOR_PATCH` | for example `6.19.0` |
| `BUILD_STAMP` | mainline only |
| `PKGVER` | backport only, for example `6.17.0-38.38~24.04.1` |
| WSL tag | for example `linux-msft-wsl-6.18.33.1`. **WSL versions do not match mainline.** |

**Kernel sources:**
- AWS backport: `linux-aws-{VER}` + `linux-signed-aws-{VER}`.
- **HWE signed backport (recommended):** `linux-hwe-{VER}` + `linux-signed-hwe-{VER}`.
- Mainline PPA: unsigned.
- Pick the latest ABI that has a complete set: image + headers + modules + modules-extra.

**Steps:**
1. Upload the `.deb`s to `s3://s3-devops-general/linux-kernel/<aws|generic|mainline>/v{…}/{amd64,arm64}/`. Kernel.org tools go to `linux-kernel/kernel.org/v…`.
2. Add `ansible/roles/kernel/defaults/ubuntu-{VER}.yml` with `kernel_base_url`, `linux-modules-extra` and the matching gcc. **Never edit `ubuntu.yml`.**
3. docker-images: add `shared/kernels/ubuntu-{VER}.dockerfile` and a bake target/tag `{FULLVER}`. Install `libstdc++-N-dev` next to the pinned gcc.
4. Run Packer with `build_base=true kernel_config={VER} image_prefix=k{VER}-`. This also builds the KubeVirt images into `s3-devops-general/kubevirt-images/`.
5. Pin the variant in `kubevirt-cdi` `goldenImage.images`. VMs opt in with `golden-image.kubevirt.io/variant: k{VER}`. Images are registered by explicit URI, so the parallel image does not replace the default.
6. WSL: add `WSL/wsl-u24-<new>` and run `./build.sh`. The WSL-headers AMI prefix is `wsl{WVER}-` with `CUSTOMKERNELDIR`.
7. octl: update `KernelsToTags` in `octl/app/app.go` and `supportedKernels` in `core/models/ci.go`.
8. Runners: add `runner-configs/linux-cxi-8xlarge-k{VER}.yaml`, with `labelMatchers` including `ubuntu-24-{MAJOR}-{MINOR}` and an AMI filter on `k{VER}-`.
9. Validate: an opt-in workflow with `runs-on` the new label, plus an octl build.
10. **Promote:** replace the label in the default runner configs and remove the `k{VER}-` AMI filter.
    - Workflows that still use the old label keep working.
    - **Rollback** = revert the runner config and AMI pin.

**Enabling a new OS, kernel or arch for a project:**
- Add a build in the project `config.yaml` with `continue_on_error: true` and `octl.extra_flags: "--artifacts-filter stage=build"`, then fix the failures.
- Changes need DevOps CODEOWNER approval.
- arm64 runners exist only as `mxg.48xlarge`.

---

## 12. Storage
Source: `user-persistent-storage.md`, `config-octl.md`; Confluence 1052246099 (NFS), 760152593, 1317273617.

| Storage | Where | Notes |
|---|---|---|
| **User persistent storage** | EC2 VMs, `/mnt/<USERNAME>` | AWS **EFS** access point per user, created on the first `octl vm create`. Not private (other access points are mountable). **No backup, no secrets.** Too slow and costly for build cache (billed per GB stored *and* transferred). Not in WSL. Mount point is fixed; use symlinks. |
| **Shared cache** | `/mnt/shared_cache` on all VMs | EFS/NFS, org-wide, shared between users. Not in WSL. |
| **octl build cache** | `~/.octl.yaml` `shared_cache:` | `local_mode: true`, `dir:` (default `$XDG_CACHE_HOME/octl/build`), `max_size` (default 10G); otherwise S3-backed |
| **Instance NVMe** | `m8id.*` runners | `/mnt/nvme-instance-store/cache` (ephemeral) |
| **EBS root** | EC2 VMs | Resize via `modify-volume` (≤500 GB, 4 per 24h) |
| **Ocean local** | DC nodes | **OpenEBS ZFS** LocalPV, initialized per node by an init container. Root disks for KubeVirt VMs are fixed at 100Gi. |
| **WEKA** | Ocean DC (planned) | Confluence 760152593: IT's ETA for the KFS production cluster "should include WEKA". No current Platform doc. Treat it as not yet available and confirm with Platform. |
| **NetApp / FSx** | — | **Not documented** in the Platform docs or the pages read. Don't assume it exists. |

- **NFS discussion** (Confluence 1052246099, 2026-04-20). These are open action items, not decisions:
  - Options: per-user EFS `/home` mount, `/mnt/shared_cache` with structure and team quotas, an `elmcli volume` resource, and EBS snapshots to restore `/home`.
  - Use `octl workspace upload-patch` to share artifacts.

---

## 13. Lab hardware hosts
Source: `pldm-fpga-setup.md`, `use-lauterbach.md`, `integrate-github-personal-aws-or-pldm.md`.

- **PLDM and FPGA hosts:**
  - LDAP SSH login. Copy your `id_ed25519` there for GitHub access.
  - **Restricted sudo.**
  - Hosts are in provisioning `ansible/inventory/{pldm,fpga}`, so request changes there.
- **Lauterbach:** `ssh -X <edk-host>`, then `t32marm` (Arm) or `t32marc` (ARC). USB-attached debuggers only.
- **GitHub on PLDM or EC2:** run `gh auth login`, or set `GITHUB_TOKEN`, then `octl update`.

---

## 14. Troubleshooting (symptom/error → fix)
Source: `how-to/dc/ocean-troubleshooting.md`, the aws-access skill, `runner-dispatch-labels.md`, `ci-config-reference.md`.

| Symptom / error | Fix |
|---|---|
| `ExpiredToken(Exception)` / `Token has expired` | `aws sso login --profile devops-cli` (12h session) |
| octl can't find credentials / profile | The profile must be named `devops-cli` |
| `AccessDenied` on S3 precomputed-refs | Assume `role-project-<project>`; ask for project SG membership in DRR |
| `AccessDenied` on ECR push | Assume `role-project-<project>`. Third-party mirroring needs a Platform admin profile. |
| `InvalidIdentityToken` in WSL | Clock skew: `sudo hwclock --hctosys` |
| SSH to new EC2 fails | Key pair not imported, or not named with your full name |
| `modify-volume` error | Over 500 GB, or 4 modifications in 24h already used. Needs `role-ec2-basic`. |
| `fi_info -p efa` returns nothing | The flavor isn't EFA-capable, or `--placement-group` / `--replicas` is missing |
| TLS `verify error code 19` in WSL | Install the Cato certificate (§9) |
| kind on WSL2: Services unreachable | `kubeProxyMode: iptables` + modprobe the netfilter modules + restart kindnet and kube-proxy |
| `dial tcp: lookup api.ocean.elmnt.local: no such host` | Office network or VPN, and corporate DNS in WSL |
| `executable kubectl-oidc_login not found` | Add `$HOME/.krew/bin` to `PATH`, then `kubectl krew install oidc-login` |
| `Unauthorized` / `forbidden` after login | `rm -rf ~/.kube/cache/oidc-login` and log in again; if it persists, request `Elmnt-Internal:<team>-sg` in DRR |
| `unsupported flavor: a1.small` | `export OCTL_FEATURE=ocean-vm` |
| `--node-selector is required for kubernetes flavors` | Add `--node-selector any` |
| `--lease is not supported … leases apply to AWS VMs only` | Drop `--lease`, and delete the Ocean VM when done |
| `kubeconfig is not available` | Save it as `~/.kube/ocean.yaml` with context `ocean` |
| `could not find namespace with edit permissions` | Run `kubectl get ns` to refresh the token, then request team access in DRR |
| `More than a single namespace found, but we cannot prompt` | `octl workloads create -n <team>-group …` |
| `reading pubkey: …` | `ssh-keygen -t ed25519`, or `-i <key>` with a matching `.pub` |
| `creating vm in CI is not supported` | Use `dc=ocean` runner labels instead |
| Job stays suspended / not admitted | Queue name must be `default`, not `accel`. Check `kubectl describe workload -n <ns> <wl>` and `kubectl get clusterqueue <team>-cq`. |
| Pod `Pending`: `untolerated taint {dedicated: dgx\|a1}` | Run it as a Kueue-managed workload in `<team>-group`; Kueue adds the toleration |
| `exceeded quota: …` | Set requests **and** limits on every container, and delete finished pods |
| `chip-topology-check` = `Retry` / `Rejected` | Retry: wait for chips to free up. Rejected: lower per-pod `elmnt.com/accel` or split the work across workers. |
| Workload evicted or preempted | You were borrowing over quota and the owner reclaimed it; stay within nominal quota |
| Image pull denied on Ocean | Not an ECR image: mirror it to `devops/third-parties/` and add `imagePullSecrets: platform-registry` |
| CI job queued forever, no error | Retired label (`c6g`, `c7g`, `t4g`), bare `ubuntu-24` on cxi/mxi, or a missing or wrong `flavor=` on `dc=ocean`. Check the Grafana Job Lookup. |
| `NodeDebug` stuck `Pending` | Another owner holds `rmc.elmnt.com/drain-owner`, or it hit `LimitReached`; see `kubectl describe nd` |
| kagent broken after a bump | `helm rollback kagent` and `helm rollback kagent-crds`; pin `kagent_version` |
| Manual AWS or EKS change disappeared | Nightly `terraform-sync` reverted it; make the change in retool-terraform |

---

## 15. Pitfalls
Source: synthesized from §2–§13 sources.
- **Profile name:** anything other than `devops-cli` silently breaks octl.
- **Ocean VMs have no lease.** They hold quota until you delete them. AWS VMs stop at the lease (default 8h, max 72h).
- **Only one personal EC2 VM** per user. Delete and wait for `terminated` before creating another.
- **Retired Graviton pools hang jobs silently.** `mxg.48xlarge` is the only arm64 pool.
- **Don't propose `ghr-*` dynamic labels** without Platform approval. They also skip the warm pools.
- **The `dc=ocean` flavor is mandatory.** The default `c7i.8xlarge` is AWS-only, so jobs without it are rejected.
- **Re-run identity:** don't pass `workflow_attempt` or the run ID through upstream job outputs.
- **Ocean admits only ECR images.** Mirror tags are immutable, so pick the tag carefully.
- **Kueue queue name:** `accel` is gone; use `default`. Bare pods outside Kueue never get the dgx/a1 tolerations.
- **Never hand-cordon or taint a node** that has a NodeDebug; delete the CR instead.
- **Kernel upgrade:** never edit `ubuntu.yml`; never swap the default label before validation. WSL kernel numbers ≠ mainline.
- **The packer cleaner deletes AMIs** beyond the newest 10 unless a launch template `$Default` references them. A running instance does not protect its AMI.
- **EKS upgrades:** one minor at a time, and avoid extended support (about 6× cost). AWS provider 6 must ship with EKS module 21.
- **EFS is NFS:** slow and billed per GB transferred. Not a build cache, no backups, no secrets, not in WSL.
- **ArgoCD app dirs must be exactly two levels deep**, or the ApplicationSet ignores them.
- **Confluence pages are dated:** the K8s 1.33.5 and Kubespray 2.26.0 versions and the Karpenter status may have moved on. Verify in the repos.

---

## 16. Owner / escalation
Source: `repositories.md`, `how-to/dc/*` (support/DRR sections), Confluence 167247885 not read (docs sufficient).
- **Owner:** Platform/DevOps team, GitHub team `Elmnt-Internal/sw-platform-sg`, the CODEOWNER of all `ip.infra.devops.*` repos.
- **Support:** MS Teams "Platform - hub" → **DevOps-Support** channel. Include the error text, run URL, flavor/labels and namespace.
- **Access requests** (AWS roles, `<team>-sg`, S3 project roles, quota): Jira **DRR** project.
- **Platform work items:** Jira `DEV-*`, for example DEV-2760 (Graviton retirement), DEV-4976 (Karpenter), DEV-2320 (DC scheduling), DEV-3195 (rail topology).
- **IT-owned:** Entra SGs, the Azure AWS app claims, Intune (WSL install), Cato, Salt, NetBox and racking.
- **Network or cluster outage on Ocean:** Platform on-call via DevOps-Support. Check Grafana and Headlamp first.

---

## 17. Source contradictions
1. **CI artifact buckets:** the `aws-access` plugin skill names `s3-sw-ci-staging-artifacts` / `s3-sw-ci-production-artifacts`, but the `platform-context` skill and the GitHub docs use `s3-github-action-package-staging` / `s3-github-action-package` (cache `s3-elmnt-github-actions-cache`). **The GitHub docs win.**
2. **SSO config:** the docs use `sso_region = eu-west-1` with `Elmnt*` roles (for example `ElmntSoftwareDeveloper`); the aws-access skill uses `eu-central-1` with `sso_role_name = role-group-sw` and calls the start URL "Okta". The docs win; Entra/Azure SAML is the IdP.
3. **Arch role name:** `ElmntArchDevelopers` (SSO doc) vs `ElmntArchUsers` (console-login doc).
4. **arm64 runners:** `enable-new-os-kernel-arch.md` says all AWS pools are x86_64; `ci-config-reference.md` lists the arm64 `mxg.48xlarge`.
5. **DC label values:** `runner-dispatcher-flow.md` lists `dc=aws|kfs|csr` and `runner-dispatcher.md` gives `dc=kfs`, while `ci-config-reference`, troubleshooting and the FAQ use `dc=ocean`. Use `dc=ocean`.
6. **Odd kernel FAQ label:** the kernel-upgrade FAQ example `runs-on: [self-hosted, ubuntu-24-{MAJOR}-{MINOR}, xlarge]` uses an `xlarge` label that no pool registers.
7. **Naming convention page:** `resource-naming-convention.md` covers GitHub repo naming and supersedes Confluence 1261469707. AWS cost tags exist only in Confluence 501678139.
8. **packer-cleaner patterns** still include `github-runner-ubuntu24.04-arm64*` although the Graviton pools are retired. This is harmless.
9. **Karpenter:** Confluence 1698168861 is a go/no-go proposal, while the task framing implies Karpenter is live on EKS. Treat it as unconfirmed.
10. **Storage on Ocean:** Confluence lists **OpenEBS ZFS** as the DC storage and WEKA only as a planned item. No source documents NetApp or FSx.
