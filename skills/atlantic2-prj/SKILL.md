---
name: atlantic2-prj
description: 'Project expert for the Atlantic2 (A2) project — the NEXT project (successor to Iridium/A1). Architecture, PRM/overview, NoC, address map, reset/boot flow, security, redundancy/binning, D2D (new 5nm + 3nm PHY), PCIe GEN7, SU PCIe-compliant mode, FW/SW integration, build & manifests, and — critically — WHAT CHANGED IN A2 vs A1 (Iridium). Use when: Atlantic2 / A2 project, A2 vs A1 changes, A2 PRM/overview, A2 NoC, A2 address map, A2 D2D new PHY (5nm/3nm, UCIe), PCIe GEN7, SU PCIe compliant mode, A2 initiatives, A2 Rev2.x specs, A2 RTL (main since A1 freeze tag), A2 HSI tickets, atlantic2 workspace/build.'
argument-hint: 'Ask about Atlantic2 (A2) — what it is, what changed vs Iridium (A1), an IP/spec/RTL area, or the project plan'
---

# Atlantic2 (A2) Project Expert

## When to Use

- Questions about the **Atlantic2 (A2)** project — the **next** project, successor to
  **Iridium (A1)** — how the parts fit together, not just one IP.
- The central lens of this skill: **"What changed in A2 vs A1 (Iridium)?"** For any topic,
  prefer answering with the A2 delta over the A1 baseline.
- A2 **overview / PRM** topics: architecture, dies/chiplets, NoC/fabric, address map, reset/boot
  flow, security (SL/SG, CfgR, RR, PB, RAC/WAC), redundancy/binning, power/thermal, manageability.
- A2 **connectivity IP deltas** (the known headline changes):
  - **D2D**: two new PHY IP versions — a 5nm one (same family as A1, possibly minor logic
    changes) and a **brand-new 3nm** PHY.
  - **PCIe EP**: **GEN7** support.
  - **SU / C2C (scale-up)**: a PCIe **"Compliant" mode**.
- A2 **HSI Jira** initiatives/epics (`ASIC Project == Atlantic2`) — plan, scope, ownership.
- A2 **Rev2.x specs** (the Rev2 docs are A2; Rev1 docs are A1/Iridium).
- A2 **RTL**: A1 froze on a specific tag; everything on the RTL `main` branch **since that tag**
  is A2 work. Diff the IP RTL repos to see A2 implementations.

> This is a **project-scope** skill. For deep single-IP questions, defer to the dedicated IP
> skills (`pcie-ep-ip`, `d2d-ip`, `su-ip`, `noc-ip`, `psoc-ip`, `ras-manageability`) — which have been updated
> with the A2 Rev2.x doc references — and use this skill for the project context, the cross-IP
> picture, the A2-vs-A1 delta, and where things live in the A2 tree.
>
> For the **A1/Iridium** baseline, use the `iridium-prj` skill. For the interim **Atlantic**
> project, use the `atlantic-prj` skill. A2 is the successor line beyond those.

## Project Context

- **Atlantic2 (A2)** is Element Labs' **next** multi-die AI accelerator SoC project, the
  successor to **Iridium (A1)**. A device is built from multiple **dies** (chiplets)
  interconnected by **D2D**, organized into **trays**/**racks** with **scale-up (C2C)**
  connectivity between devices.
- **Compute**: VCore + **VME** (Vector Matrix Engine); on-die **NoC / MEM fabric** moves data
  between Global Memory, LMEM, sync objects, and collective address spaces.
- **Management plane**: **PSOC**, **manageability** (PLDM/MCTP), **RAS**, power/thermal loop.
- **Connectivity IPs**: **PCIe EP** (host interface, now **GEN7**), **D2D** (die-to-die, new
  5nm + 3nm PHY), **C2C / scale-up** (chip-to-chip, adds PCIe-**Compliant** mode), **CCE**,
  **FIU**, **NoC**.
- **Security**: SL/SG, CfgR, RR, PB, RAC/WAC, Boot ROM (carried from the Atlantic/Iridium line —
  confirm A2 deltas against the Rev2 specs).
- **Boot/reset**: Boot ROM → streaming/flashless boot, per-IP FW download, staged SW reset flow.

> **A2 is early / in planning.** Much of A2 is a delta on the Iridium (A1) baseline. When an
> A2-specific fact is not yet populated, fall back to the IP skills / `iridium-prj` for the
> inherited design and **flag it as inherited (A1 baseline), not A2-confirmed**. Always try to
> express findings as the **A2-vs-A1 delta**.

## Document Catalog

> **Rule of thumb: Rev2.x docs are A2; Rev1.x docs are A1 (Iridium).** The IP spec folders are
> shared across projects and now hold BOTH revisions — always pick the Rev2 doc for A2 and diff
> it against the Rev1 doc for the A2-vs-A1 delta.

### A2 project documents

| Doc | Path | Notes |
|-----|------|-------|
| **Atlantic2 Overview** | `~/docs/A2/Atlantic2 Overview.docx` | Top-level "what A2 is" + high-level deltas |
| **A2 Arch Enhancements v1.3** | `~/docs/A2/A2_Arch_Enhancments_v1.3.docx` | **Key A2-delta doc** — scale-up domain (16K Ranks), GPU/A2 disaggregation (workers/experts, MoE), M2M dataflow, INC collective acceleration, dynamic multicast |
| **A2 Scale-up** | `~/docs/A2/A2 Scale-up.docx` | Scale-up domain overview, platform variants, routing & address-space model (subset of Arch Enhancements) |
| **A2 NoC Specification Rev 1.4.0** | `~/docs/NOC/Atlantic2 NoC Specification Rev 1.4.0.docx` | **Newest** A2 NoC/fabric + address map (supersedes Rev 1.2.0 in `~/docs/A2/NOC/`). See the `noc-ip` skill. |

### Per-IP A2 (Rev2.x) specs — used to update the IP skills

| IP | A2 doc (Rev2) | A1 baseline (Rev1) | IP skill |
|----|---------------|--------------------|----------|
| PCIe EP | `~/docs/PCIe EP/PCIe EP Specification Rev 2.0.0.docx` | (prior Rev) | `pcie-ep-ip` |
| D2D | `~/docs/D2D/D2D Specification Rev 2.0.0.docx` | `~/docs/D2D/D2D Specification Rev1.docx` | `d2d-ip` |
| C2C / SU | `~/docs/C2C/PCIe C2C Rev 2.1.1.docx` (newest), `Rev 2.0.0.docx` | `~/docs/C2C/PCIe C2C.docx` | `su-ip` |
| PSoC | `~/docs/PSoC/PSOC Specification Rev2.0.1.docx` | `~/docs/PSoC/PSOC Specification Rev1.2.0.docx` | `psoc-ip` |
| RAS / Device Mgmt | `~/docs/RAS/RAS Specification.docx`, `Device Manageability Specification.docx` | (same) | `ras-manageability` |

> Discover what is currently present (docs are added as A2 ramps):
> ```python
> import glob, os
> for f in sorted(glob.glob(os.path.expanduser("~/docs/**/*.docx"), recursive=True)):
>     print(os.path.relpath(f, os.path.expanduser("~/docs")), f"{os.path.getsize(f)//1024} KB")
> ```

### External References (Confluence)

| Reference | Link | Content |
|-----------|------|---------|
| A2 HW Initiatives — Qs | https://elementlabs.atlassian.net/wiki/spaces/~712020589feaff768144a78fb51f9ac2542856/pages/1362952569/A2+HW+Initiatives+-+Qs | Architect's A2 HW initiatives (mostly HW — likely little/no FW work). Open questions per initiative. |
| Boot flow (SWENG) | https://elementlabs.atlassian.net/wiki/spaces/SWENG/pages/598671382/Boot+flow | Per-stage boot sequence, INIT vs RUNTIME responsibilities (inherited baseline). |

## Project Layout & Source

The A2 project source (when checked out) is expected under `~/projects/atlantic2/` (mirroring the
`~/projects/atlantic/` and `~/projects/iridium/` conventions). A typical workspace:

| Path | Content |
|------|---------|
| `fw/` | Firmware tree (Zephyr apps + per-IP drivers under `fw/ip/<ip>/`) |
| `fw/proj_infra/` | Project-specific A2 FW infra: build infra, post-build scripts, lua, regressions |
| `kmd/` | Kernel-mode driver(s) |
| `<repo>/.github/` | Per-repo skills / AI resources — consult alongside this skill |

**RTL strategy (critical for A2):**
- A1 (Iridium) **froze its RTL on a specific tag**. On each IP RTL repo, **everything on `main`
  since that A1 freeze tag is A2 work**.
- To find A2 RTL changes: identify the A1 freeze tag/branch (see `iridium-prj` / the IP skills for
  the freezed-RTL branch), then diff `main` against it:
  `git log <a1-freeze-tag>..origin/main` and `git diff <a1-freeze-tag>..origin/main -- <path>`.
- Prefer a local checkout; otherwise use the GitHub tools against `Elmnt-Internal/ip.<ip>.*`
  repos (see the IP skills for exact repo URLs).

### Build / Workspace Conventions

- Workspaces initialized with `octl ws init -p atlantic2 -m <manifest>` (expected convention).
- Project build behavior in `fw/proj_infra/build_infra/` + `project.atlantic2.sw.fw.infra.star`.
- To build A2 FW apps, use the **`build-fw`** skill.

## Knowledge Base (Expert Memory)

This skill keeps an **expert memory** of everything researched/verified about A2, emphasizing the
**A2-vs-A1 delta**, so repeat questions are answered fast.

- **Location:** `~/.copilot/skills/atlantic2-prj/knowledge/`
- **Index:** `~/.copilot/skills/atlantic2-prj/knowledge/INDEX.md`
- Detail files: `knowledge/<topic-slug>.md` — verified findings with full citations.

Always consult it first (Step 0) and update it after non-trivial research (final step).

## Procedure

### Step 0: Consult the Knowledge Base (first)

1. Read `~/.copilot/skills/atlantic2-prj/knowledge/INDEX.md` and scan the topic table.
2. Full-text search `knowledge/` for the question's keywords. If a recorded finding answers it,
   use it (cite it); otherwise use it to narrow the search.

### Step 1: Classify the Question

| Topic | Primary Source | Secondary |
|-------|---------------|-----------|
| What A2 is / high-level deltas | `Atlantic2 Overview.docx` | A2 initiatives (Confluence) |
| Address map, fabric, routing, credits | A2 NoC spec Rev 1.4.0 (`noc-ip`) | Overview |
| D2D (new 5nm/3nm PHY, UCIe) | D2D Spec Rev 2.0.0 + `d2d-ip` | D2D RTL diff |
| PCIe EP (GEN7) | PCIe EP Spec Rev 2.0.0 + `pcie-ep-ip` | PCIe RTL diff |
| SU/C2C (PCIe compliant mode) | PCIe C2C Rev 2.1.1 + `su-ip` | C2C RTL diff |
| PSoC / RAS / manageability | PSoC Rev 2.0.1 (`psoc-ip`) / RAS specs + `ras-manageability` | — |
| Plan / scope / ownership | Jira `ASIC Project == Atlantic2` (HSI) | `jira-hsi-planning` |
| HW initiatives (often no FW) | A2 HW Initiatives Confluence page | — |
| RTL implementation (any IP) | IP RTL repo, `main` since A1 freeze tag | the IP skill |

### Step 2: Search the Source

- **DOCX** (Rev2 for A2, Rev1 for A1): use `python-docx` — heading map, paragraph search, table
  search (register/field defs live in tables). Diff Rev2 vs Rev1 for the A2 delta.
- **RTL**: `git diff <a1-freeze-tag>..origin/main` in the IP repo (or GitHub tools).
- **Source tree**: workspace tools under `~/projects/atlantic2/<variant>/` (fall back to GitHub).

### Step 3: Extract and Answer

1. Always frame the answer as the **A2-vs-A1 delta** where possible.
2. Where A2 material is not yet populated but the IP is inherited from A1, answer from the IP
   skill / `iridium-prj` and **flag it as inherited (A1 baseline)**.
3. Cross-IP questions: give the project picture here, point to the IP skill for the deep dive.
4. **Always cite** the source (Rev2 doc + para/table, RTL repo/commit/path, manifest/`.star`).

### Step 4: Record Verified Findings (last)

1. Create/update `knowledge/<topic-slug>.md` (template in `INDEX.md`). Emphasize the A2-vs-A1
   delta and cite sources.
2. Add/refresh the topic row in `INDEX.md` (newest at top).
3. If new research contradicts a prior entry, **correct** it.

### Search Strategy Tips

- **A2-vs-A1**: always keep the Rev1 (A1) doc handy to diff against the Rev2 (A2) doc.
- **Acronyms**: each spec has a Terminology/Acronyms section near the top.
- **Address map / regions**: A2 NoC spec "Address Map".
- **RTL delta**: diff `main` against the A1 freeze tag (per IP; see IP skill for the tag/branch).
- **Tables**: register/field/mapping defs are in tables — search paragraphs AND tables.
