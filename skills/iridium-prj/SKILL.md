---
name: iridium-prj
description: 'Project expert for the Iridium project — architecture, PRM, NoC, address map, reset/boot flow, security, redundancy/binning, FW/SW integration, build & manifests. Use when: Iridium project, Iridium PRM, Iridium NoC, address map, scale-up/C2C, D2D isolation, reset flow, boot ROM/streaming boot, security level/group (SL/SG), CfgR/RR/PB, binning/redundancy, VCore/VME/PSOC/CCE/FIU, power/thermal loop, project variants/platforms, octl iridium workspace, project-specific build infra.'
argument-hint: 'Ask a question about the Iridium project — architecture, PRM/NoC spec, flows, security, or FW/SW integration'
---

# Iridium Project Expert

## When to Use

- Questions about the **Iridium project** as a whole — how the parts fit together, not just one IP
- Iridium **PRM** (Product Reference Manual) topics: FIU, binning, reset flow, trace, security,
  GPIO/straps, eFuse, D2D/C2C, ECC, interrupts/PLIC, MMU, VCore, PCIe, scheduler, VME,
  manageability, RAS, power/thermal loop, PSOC, CCE, margin tools, run-time FW security
- Iridium **NoC / fabric** topics: address map, memory regions, NPA, device coordinates,
  scale-up connectivity, routing, MEM fabric, virtual channels, end-to-end credits
- Project-wide concerns: **address space**, **reset/boot flow**, **security model (SL/SG, CfgR,
  RR, PB, RAC/WAC)**, **redundancy & binning**, **device coordinates / scale-up topology**
- **FW/SW integration** across IPs as deployed in Iridium (which IP owns what, init order,
  cross-IP dependencies)
- **Project build, variants, platforms, and manifests** (octl workspace, project-specific
  build infra, `project.iridium.*.star` files)
- Mapping a feature/requirement to the IP, spec section, and code that implements it

> This is a **project-scope** skill. For deep single-IP questions, defer to the dedicated IP
> skills (`pcie-ep-ip`, `d2d-ip`, `su-ip`, `ras-manageability`) and use this skill to provide
> the project context, the cross-IP picture, and where things live in the Iridium tree.
>
> **Iridium is a previous project.** The most recent project we work on is **Atlantic** — see
> the `atlantic-prj` skill. In the RTL repos, Atlantic's latest implementation is on the
> **`main`** branch, while **Iridium's RTL is frozen on its *freezed rtl* branch**. Use this
> skill for Iridium; use `atlantic-prj` for Atlantic.

## Project Context

- **Iridium** is Element Labs' multi-die AI accelerator SoC project. A device is built from
  multiple **dies** (chiplets) interconnected by **D2D**, organized into **trays** and **racks**
  with **scale-up (C2C)** connectivity between devices.
- **Compute**: VCore + **VME** (Vector Matrix Engine); on-die **NoC / MEM fabric** moves data
  between Global Memory, LMEM, sync objects, and collective address spaces.
- **Management plane**: **PSOC** (platform/management SoC block), **manageability** (PLDM/MCTP),
  **RAS**, and the **power/thermal loop**.
- **Connectivity IPs**: **PCIe EP** (host interface), **D2D** (die-to-die), **C2C / scale-up**
  (chip-to-chip), **CCE** (collective/communication engine), **FIU** (Fabric Interface Unit),
  **NoC**.
- **Security**: Security Level / Security Group (SL/SG), Configuration Ranges (CfgR), Range
  Registers (RR), Protection Bits (PB), Read/Write Access Control (RAC/WAC), Boot ROM.
- **Boot/reset**: Boot ROM → streaming boot / flashless, FW download per IP (D2D, PCIe),
  staged SW reset flow with engine halt and reset notifications.

## Document Catalog

### Iridium Project Documents (`~/docs/Iridium/`)

| File | Size | Content |
|------|------|---------|
| `Iridium PRM.docx` | ~2.7MB | Product Reference Manual — the master project spec. 3287 paras, 40 tables. Covers FIU, binning, memory scrubbing, reset flow, trace (ETR/BMON/STM/SPMU), security (SL/SG, CfgR, RR, PB, RAC/WAC, Boot ROM), GPIO/straps, eFuse, D2D, C2C, ECC, interrupts/PLIC, I-cache, MMU, VCore, PCIe, scheduler, VME, manageability, RAS, power/thermal loop, PSOC, logical BIST, CCE, margin tools, run-time FW security, boot-time estimation |
| `Iridium NoC Specification.docx` | ~7.1MB | NoC / fabric spec. 1662 paras, 18 tables. Covers address map (memory regions, device/host/management/control/debug address spaces, NPA), redundancy (inner-die & platform), device coordinates, scale-up connectivity & routing, device routing, MEM fabric (planes, mRTR/pRTR, virtual channels, data merging, E2E credits), VME & datapaths |
| `Iridium SoC spec.docx` | — | SoC architecture spec. 487 paras, 11 tables. Covers Architectural Design (**Reset Domains** — authoritative **Table 4-2** reset-domain matrix, *not* in the PRM; **Software Reset Flow**), MMU/PMMU (TLB, PTW, layers), Virtualization (PF/VF: PCIe BMU/iATU/CfgR/MSI-X/TEE, PSOC mailbox/FLR, MMU ASID/gateways, NoC), and PCIe EP capability/RAS support tables. Authority for the reset-domain matrix and SoC-level architecture. |

### Related Project Data (`~/docs/`)

| File | Content |
|------|---------|
| `HSI_Features.csv` / `HSI_Features_Enriched.csv` | HSI feature inventory (and enriched mapping) used for Iridium planning/traceability |
| `enrich_csv.py` | Script that produced the enriched feature CSV |

### Per-IP Specs (defer to the IP skills, but useful as Iridium project sources)

- `~/docs/PCIe EP/` → `pcie-ep-ip` skill
- `~/docs/D2D/` → `d2d-ip` skill
- `~/docs/C2C/` and scale-up → `su-ip` skill
- `~/docs/RAS/`, `~/docs/PSoC/` → `ras-manageability` skill
- `~/docs/NOC/` → fabric/NoC supporting material

### External References (Confluence)

| Reference | Link | Content |
|-----------|------|---------|
| Boot flow | https://elementlabs.atlassian.net/wiki/spaces/SWENG/pages/598671382/Boot+flow | SWENG boot-flow page — per-stage (FLA.RT / PRIMEBOOT / MANAGEMENT / POWER&THERMAL) boot sequence and INIT vs RUNTIME responsibilities across IPs. Authority for the overall FW boot/reset sequencing the PRM "Reset Flow" / "Boot Time Estimation" sections describe. |

## Project Layout & Source

The Iridium project source is typically checked out locally under `~/projects/iridium/`.
Each subfolder (`main/`, `one/`, `two/`, `three/`, `four/`, `customer_repro/`, `ws-*`) is a
workspace/variant checkout. A typical workspace contains:

| Path | Content |
|------|---------|
| `fw/` | Firmware tree (Zephyr apps + per-IP drivers under `fw/ip/<ip>/`) |
| `fw/proj_infra/` | **Project-specific** Iridium FW infra: build infra, post-build scripts, lua scripts (e.g. `sec_access_ctrl`), `pldm_regressions` |
| `fw/tools/fw_host_streaming_boot/` | Host-side streaming/flashless boot tooling |
| `kmd/` | Kernel-mode driver(s) for the Iridium Linux stack |
| `sim/`, `experiments/`, `evidence/`, `logs/` | Simulation, experiment runs, captured evidence |
| `<repo>/.github/` | Per-repo skills / AI resources. **Every** repo in the workspace carries its own `.github/` with additional skills and AI context — consult them alongside this skill. |

**Local-first strategy** (this skill is global / user-level — the workspace may or may not
contain the source):
1. Prefer reading from a local checkout under `~/projects/iridium/<variant>/...`.
2. Identify the deployment path of an IP driver via the project manifest
   (`project.iridium.sw.fw.infra.star` and other `.star`/`octl` manifests).
3. If a needed repo is not local, use the GitHub search tools against the relevant
   `Elmnt-Internal/ip.<ip>.*` repos (see the IP skills for exact repo URLs).
4. The workspace is multi-repo, with its repo set and layout defined by the `octl` manifest.
   Each repo has a `.github/` directory containing additional skills / AI resources — check
   the relevant repo's `.github/` for project- or IP-specific guidance before/while researching.

### Build / Workspace Conventions

- Workspaces are initialized with `octl ws init -p iridium -m <manifest>`
  (e.g. `spark-ci`, `spark-ci` variants seen as `ws-fb-iridium-fw-ci`).
- Project-specific build behavior lives in `fw/proj_infra/build_infra/` and
  `project.iridium.sw.fw.infra.star` (collects artifacts per variant).
- To build Iridium FW apps for a board/variant/platform, use the **`build-fw`** skill.

## Knowledge Base (Expert Memory)

This skill keeps an **expert memory** of everything it has already researched and verified about
the Iridium project, so repeat questions are answered fast without re-reading the (large) PRM,
NoC spec, or source tree.

- **Location:** `~/.copilot/skills/iridium-prj/knowledge/`
- **Index:** `~/.copilot/skills/iridium-prj/knowledge/INDEX.md` — catalog of recorded topics
- Detail files: `knowledge/<topic-slug>.md` — verified findings with full citations

The knowledge base is **always consulted first** (Step 0) and **always updated after**
non-trivial research (final step). This is what makes the skill grow into a true Iridium project
expert over time.

## Procedure

### Step 0: Consult the Knowledge Base (do this first)

1. Read `~/.copilot/skills/iridium-prj/knowledge/INDEX.md` and scan the topic table.
2. If the question overlaps a recorded topic, read that detail file. Also full-text search the
   `knowledge/` directory for the question's keywords:
   ```python
   import os, glob
   kb = os.path.expanduser("~/.copilot/skills/iridium-prj/knowledge")
   for f in glob.glob(os.path.join(kb, "*.md")):
       txt = open(f, encoding="utf-8").read()
       if "<keyword>" in txt.lower():
           print(f"== {os.path.basename(f)} ==")
   ```
3. If a recorded finding fully answers the question, use it (cite it). Otherwise use it to
   **narrow** the search before diving into the docs/source.

### Step 1: Classify the Question

Determine which source(s) are most relevant:

| Topic | Primary Source | Secondary Source |
|-------|---------------|------------------|
| Address map, memory regions, NPA, routing, fabric, credits | `Iridium NoC Specification.docx` | `Iridium PRM.docx` |
| Device coordinates, scale-up topology, redundancy/yield | `Iridium NoC Specification.docx` | PRM (Binning) |
| FIU, reset/boot flow, trace, eFuse, GPIO/straps | `Iridium PRM.docx` | source `fw/` |
| Security (SL/SG, CfgR, RR, PB, RAC/WAC, Boot ROM) | `Iridium PRM.docx` | `fw/proj_infra/scripts/lua/sec_access_ctrl/` |
| Binning / redundancy (VCore, PBOX, D2D/PCIe isolation) | `Iridium PRM.docx` | NoC spec (Redundancy) |
| ECC, interrupts/PLIC, MMU, I-cache, scheduler | `Iridium PRM.docx` | source `fw/` |
| VCore / VME / datapaths | `Iridium NoC Specification.docx` | PRM |
| PSOC, CCE, manageability, RAS, power/thermal | `Iridium PRM.docx` | `ras-manageability` skill |
| Single-IP deep dive (PCIe EP, D2D, C2C/SU) | the IP skill | PRM/NoC for project context |
| Build, variants, manifests, project infra | `fw/proj_infra/`, `.star` manifests | `build-fw` skill |
| Feature → owner/section/code mapping | `HSI_Features*.csv` | PRM + source |

### Step 2: Search the Source

Use `python-docx` for the `.docx` specs. Use the workspace search tools (grep/file/semantic)
for the source tree.

#### DOCX keyword search across paragraphs:
```python
import docx
doc = docx.Document("/home/yoniizu/docs/Iridium/Iridium PRM.docx")
for i, para in enumerate(doc.paragraphs):
    if "<keyword>" in para.text.lower():
        print(f"Para {i} [{para.style.name}]: {para.text[:300]}")
```

#### DOCX heading map (locate a section, then read its range):
```python
import docx
doc = docx.Document("/home/yoniizu/docs/Iridium/Iridium PRM.docx")
for i, p in enumerate(doc.paragraphs):
    if p.style.name.lower().startswith("heading") and p.text.strip():
        print(i, p.style.name, "|", p.text.strip())
```

#### DOCX extract a paragraph range for context:
```python
import docx
doc = docx.Document("/home/yoniizu/docs/Iridium/Iridium PRM.docx")
for i in range(START, END):
    if i < len(doc.paragraphs):
        print(f"[{i}] {doc.paragraphs[i].text}")
```

#### DOCX search tables (register/field definitions live in tables):
```python
import docx
doc = docx.Document("/home/yoniizu/docs/Iridium/Iridium PRM.docx")
for t_idx, table in enumerate(doc.tables):
    for r_idx, row in enumerate(table.rows):
        row_text = " | ".join(cell.text.strip() for cell in row.cells)
        if "<keyword>" in row_text.lower():
            print(f"Table {t_idx}, Row {r_idx}: {row_text}")
```

For the **source tree**, prefer the workspace tools (`grep_search`, `file_search`,
`semantic_search`, `read_file`) under `~/projects/iridium/<variant>/`. Fall back to GitHub
search tools for repos not checked out locally.

### Step 3: Extract and Answer

1. Search broad keywords first, then narrow to the right section/table.
2. For project flows (reset, boot, security), trace the PRM description **and** confirm against
   the implementing code/scripts when available.
3. When a question spans multiple IPs, give the project-level picture here and point to the
   specific IP skill for the deep dive.
4. The docx may contain diagrams that cannot be rendered — call out when a diagram would help.
5. **Always cite** the source: PRM/NoC paragraph or table index, source file + path + symbol,
   or manifest/`.star` path.
6. If the specs don't cover it, check the source tree (local first, then GitHub).

### Step 4: Record Verified Findings (do this last)

After answering a non-trivial question, persist what you verified so the skill remembers it:

1. Create or update `~/.copilot/skills/iridium-prj/knowledge/<topic-slug>.md` using the template
   in `INDEX.md`. Keep it concise, durable, and reusable.
2. Add or refresh the topic's row in `INDEX.md` (newest at top) with tags, status, and date.
3. Always include citations (doc + paragraph/table, source repo/path + symbol, manifest path) so
   the finding can be re-verified and trusted.
4. If new research contradicts a prior entry, **correct** that entry rather than leaving stale
   information.

Skip recording only for trivial lookups already fully covered by an existing entry.

### Search Strategy Tips

- **Acronyms**: The PRM and NoC spec each have a "Terminology / Acronyms" section near the top —
  resolve unfamiliar terms there first.
- **Address map / NPA / regions**: NoC spec "Address Map" section (memory/device/host/management/
  control/debug address spaces, Network Physical Address).
- **Reset & boot**: PRM "Reset Flow" (component mapping, SW reset flow, engine halt, reset
  notification errata) and "Addendum → Device Reset / Hard Reset" + "Boot Time Estimation". For
  the overall FW boot sequence and per-stage INIT vs RUNTIME responsibilities, see the SWENG
  **Boot flow** Confluence page (id 598671382).
- **Security**: PRM "Security" (SL/SG, CfgR, RR, PB, RAC/WAC, Boot ROM) and "Run-Time FW
  Security"; implementing scripts under `fw/proj_infra/scripts/lua/sec_access_ctrl/`.
- **Redundancy/binning**: PRM "Binning" (VCore/PBOX redundancy, D2D/PCIe isolation, degraded
  mode) cross-referenced with NoC spec "Redundancy".
- **Scale-up / C2C**: PRM "C2C" + NoC spec "Scale-Up Connectivity" / "Device Routing".
- **Tables**: Register, field, and mapping definitions are usually in tables — search both
  paragraphs AND tables.

## Important Notes

- The PRM is the **master project spec**; the NoC spec is the authority for **address map,
  fabric, routing, and scale-up topology**. Cross-reference both for system-level questions.
- This skill provides the **cross-IP, project-level** view. For exhaustive single-IP register/
  PHY/protocol detail, defer to: `pcie-ep-ip`, `d2d-ip`, `su-ip`, `ras-manageability`.
- For building Iridium FW, use the `build-fw` skill; for planning/tracking, the
  `jira-hsi-planning` / `team-planner` skills.
- Source under `~/projects/iridium/` has multiple variant checkouts — confirm which variant a
  finding came from, and prefer `main/` unless a specific variant is requested.
- DOCX tables may contain merged cells — handle gracefully when parsing.
