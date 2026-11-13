---
name: atlantic2-prj
description: 'Project expert for the Atlantic2 (A2) project — the NEXT project (successor to Iridium/A1). Architecture, PRM/overview, NoC, address map, reset/boot flow, security, redundancy/binning, D2D (new 5nm + 3nm PHY), PCIe GEN7, SU PCIe-compliant mode, FW/SW integration, build & manifests, and — critically — WHAT CHANGED IN A2 vs A1 (Iridium). Use when: Atlantic2 / A2 project, A2 vs A1 changes, A2 PRM/overview, A2 NoC, A2 address map, A2 D2D new PHY (5nm/3nm, UCIe), PCIe GEN7, SU PCIe compliant mode, A2 initiatives, A2 Rev2.x specs, A2 RTL questions (answered by Neo via neo-ask --chip a2), A2 HSI tickets, atlantic2 workspace/build.'
argument-hint: 'Ask about Atlantic2 (A2) — what it is, what changed vs Iridium (A1), an IP/spec/RTL area, or the project plan'
---

> **Portal handoff:** For Team Portal/dashboard questions, feature requests,
> UI/API changes, bugs or operations, load
> [portal-maintainer](../portal-maintainer/SKILL.md). Domain records and
> workflows remain owned by this skill; coordinate mixed changes with the maintainer.

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
- A2 project specs: select by verified document scope, not a Rev1/Rev2 heuristic.
- A2 **RTL**: A1 froze on a specific tag; everything on the RTL `main` branch **since that tag**
  is A2 work. Ask Neo (`~/bin/neo-ask --chip a2`) for the delta.

> **RTL access:** Follow [rtl-vm-policy](../rtl-vm-policy/SKILL.md). Every RTL
> question goes to **Neo** (neo-brain on `vm`, A1 + A2 mirrors) via
> `~/bin/neo-ask [--chip a1|a2] "<question>"`. Never read RTL from local/VM
> checkouts or GitHub; relay only paraphrased, cited findings. If Neo is
> unavailable, report the prerequisite and stop.
> Local FW and CSR software headers are not RTL.

> This is a **project-scope** skill. For deep single-IP questions, defer to the dedicated IP
> skills (`pcie-ep-ip`, `d2d-ip`, `su-ip`, `noc-ip`, `psoc-ip`, `ras-manageability`) — which have been updated
> with explicit A2 Git source mappings — and use this skill for the project context, the cross-IP
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

Use the [verified Git source map](../../installed-plugins/ai-toolkit/fw-soc/references/specification-sources.md):
general architecture is in `Elmnt-Internal/module.arch.hwarch.specs`; A2
project specs **and legacy A1** are in `Elmnt-Internal/project.atlantic2.hwarch.specs`,
both on `main`. The project repo has a deprecation notice; the user explicitly
approved retaining this split.

The map records exact Markdown paths for every IP, legacy `specs/iridium-*`
PRM/SoC/NoC/security, and the Git original of A2 Arch Enhancements v1.3.
An exact Atlantic2 Overview / A2 Scale-up Markdown counterpart was not verified.
Do not treat the general overview as the missing A2 original.

**NoC revisions require verification:** unversioned A2 Markdown says 1.0.1;
the Rev1.5.0 folder's Markdown says 1.4.0; version-specific v1.5.0 extracts
and a Rev1.7.0 Word original exist. No folder is automatically "latest".
Exact legacy D2D/PSOC/C2C originals, DMS/RAS and vendor manuals are gaps, not
permission to invent archive paths. Existing knowledge is historical secondary
evidence; revalidate before calling a claim A2-confirmed.

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
| `<repo>/.github/` | FW-repo skills / AI resources may be consulted locally; RTL questions go to Neo (`neo-ask`) only. |

**RTL strategy (critical for A2):**
- A1 (Iridium) **froze its RTL on a specific tag**. On each IP RTL repo, **everything on `main`
  since that A1 freeze tag is A2 work**.
- Ask Neo: `neo-ask --chip a2 "What changed in <IP> <feature> between the A1
  freeze and current A2 main?"`. Relay its paraphrased delta and citations
  (repo, path, symbol, mirror commit), never a diff or source text. No local,
  VM-worktree or GitHub RTL fallbacks.

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

Consult source-free knowledge first when available (Step 0) and update it with
paraphrased findings after non-trivial research (final step).

## Procedure

### Step 0: Consult the Knowledge Base (first)

1. Use the topic names in this skill. Consult an index or note only if it is
   already known not to contain HDL excerpts; do not bulk-search `knowledge/`.
2. If a source-free finding answers the question, cite it; otherwise use the
   specifications or ask Neo (`neo-ask`) for a paraphrased finding.

### Step 1: Classify the Question

| Topic | Primary Source | Secondary |
|-------|---------------|-----------|
| What A2 is / high-level deltas | Verified project IP docs; exact A2 Overview unavailable | A2 initiatives (Confluence); general overview is context only |
| Address map, fabric, routing, credits | Mapped A2 NoC, intended revision verified (`noc-ip`) | General overview (not the missing A2 original) |
| D2D (new 5nm/3nm PHY, UCIe) | D2D Spec Rev 2.0.0 + `d2d-ip` | D2D RTL diff |
| PCIe EP (GEN7) | PCIe EP Spec Rev 2.0.0 + `pcie-ep-ip` | PCIe RTL diff |
| SU/C2C (PCIe compliant mode) | PCIe C2C Rev 2.1.1 + `su-ip` | C2C RTL diff |
| PSoC / RAS / manageability | PSoC Rev 2.0.1 (`psoc-ip`) / RAS specs + `ras-manageability` | — |
| Plan / scope / ownership | Jira `ASIC Project == Atlantic2` (HSI) | `jira-hsi-planning` |
| HW initiatives (often no FW) | A2 HW Initiatives Confluence page | — |
| RTL implementation (any IP) | IP RTL repo, `main` since A1 freeze tag | the IP skill |

### Step 2: Search the Source

- **Specifications**: resolve a Git commit, fetch the exact mapped Markdown
  with `gh api` as shown in the source map, then search headings/text/tables.
  Verify cover revision and project scope; compare only verified A1/A2 baselines.
- **RTL**: ask Neo (`neo-ask --chip a2`) to compare the A1 freeze with A2 `main`.
- **FW/software source**: workspace tools under `~/projects/atlantic2/<variant>/`
  (fall back to GitHub for non-RTL content only).

### Step 3: Extract and Answer

1. Always frame the answer as the **A2-vs-A1 delta** where possible.
2. Where A2 material is not yet populated but the IP is inherited from A1, answer from the IP
   skill / `iridium-prj` and **flag it as inherited (A1 baseline)**.
3. Cross-IP questions: give the project picture here, point to the IP skill for the deep dive.
4. **Always cite** Git repo/commit/path + section/table, Neo's cited RTL findings,
   or the manifest/`.star`; historical paragraph indexes are secondary locators.

### Step 4: Record Verified Findings (last)

1. Create/update `knowledge/<topic-slug>.md` (template in `INDEX.md`). Emphasize the A2-vs-A1
   delta and cite sources.
2. Add/refresh the topic row in `INDEX.md` (newest at top).
3. If new research contradicts a prior entry, **correct** it.

### Search Strategy Tips

- **A2-vs-A1**: compare verified project documents; if either exact baseline is
  unavailable, report the gap rather than deriving a delta from general snapshots.
- **Acronyms**: each spec has a Terminology/Acronyms section near the top.
- **Address map / regions**: A2 NoC spec "Address Map".
- **RTL delta**: ask Neo (`neo-ask --chip a2`) for the A1-freeze → A2-`main` delta per IP.
- **Tables**: register/field/mapping defs are in tables — search paragraphs AND tables.
