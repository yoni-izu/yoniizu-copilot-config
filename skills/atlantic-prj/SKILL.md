---
name: atlantic-prj
description: 'Project expert for the Atlantic project — the current, most-recent project. Architecture, PRM, NoC, address map, reset/boot flow, security, redundancy/binning, FW/SW integration, build & manifests. Use when: Atlantic project, Atlantic PRM, Atlantic NoC, address map, scale-up/C2C, D2D isolation, reset flow, boot ROM/streaming boot, security level/group (SL/SG), CfgR/RR/PB, binning/redundancy, power/thermal loop, project variants/platforms, octl atlantic workspace, project-specific build infra.'
argument-hint: 'Ask a question about the Atlantic project — architecture, PRM/NoC spec, flows, security, or FW/SW integration'
---

> **Portal handoff:** For Team Portal/dashboard questions, feature requests,
> UI/API changes, bugs or operations, load
> [portal-maintainer](../portal-maintainer/SKILL.md). Domain records and
> workflows remain owned by this skill; coordinate mixed changes with the maintainer.

# Atlantic Project Expert

## When to Use

- Questions about the **Atlantic project** as a whole — how the parts fit together, not just one IP
- Atlantic **PRM** (Product Reference Manual) topics: FIU, binning, reset flow, trace, security,
  GPIO/straps, eFuse, D2D/C2C, ECC, interrupts/PLIC, MMU, VCore, PCIe, scheduler, VME,
  manageability, RAS, power/thermal loop, PSOC, CCE, margin tools, run-time FW security
- Atlantic **NoC / fabric** topics: address map, memory regions, NPA, device coordinates,
  scale-up connectivity, routing, MEM fabric, virtual channels, end-to-end credits
- Project-wide concerns: **address space**, **reset/boot flow**, **security model (SL/SG, CfgR,
  RR, PB, RAC/WAC)**, **redundancy & binning**, **device coordinates / scale-up topology**
- **FW/SW integration** across IPs as deployed in Atlantic (which IP owns what, init order,
  cross-IP dependencies)
- **Project build, variants, platforms, and manifests** (octl workspace, project-specific
  build infra, `project.atlantic.*.star` files)
- Mapping a feature/requirement to the IP, spec section, and code that implements it

> This is a **project-scope** skill. For deep single-IP questions, defer to the dedicated IP
> skills (`pcie-ep-ip`, `d2d-ip`, `su-ip`, `ras-manageability`) and use this skill to provide
> the project context, the cross-IP picture, and where things live in the Atlantic tree.
>
> **Atlantic is the current, most-recent project.** In the RTL repos, Atlantic's latest
> implementation lives on the **`main`** branch. Previous projects (e.g. **Iridium** — see the
> `iridium-prj` skill) are frozen on their own **freezed rtl** branch. When researching RTL for
> Atlantic, use `main`; for an earlier project, check out that project's freezed-RTL branch.

## Project Context

- **Atlantic** is Element Labs' current multi-die AI accelerator SoC project — the successor to
  Iridium. A device is built from multiple **dies** (chiplets) interconnected by **D2D**,
  organized into **trays** and **racks** with **scale-up (C2C)** connectivity between devices.
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

> Atlantic is early/ongoing: some specs, docs, and code paths may not be populated yet. When a
> project-scope fact is not yet available for Atlantic, note that explicitly and — where the IP
> is carried over from Iridium — fall back to the IP skills / `iridium-prj` for the inherited
> design, flagging it as inherited rather than Atlantic-confirmed.

## Document Catalog

### Atlantic Project Documents (`~/docs/Atlantic/`)

Project documents live under `~/docs/Atlantic/` (populated as the project ramps). Expect the same
document families as Iridium — a **PRM** (master project spec), a **NoC / fabric spec**, and a
**SoC spec** (reset domains, MMU/PMMU, virtualization). Discover what is currently present:

```python
import os, glob
d = os.path.expanduser("~/docs/Atlantic")
for f in sorted(glob.glob(os.path.join(d, "**", "*"), recursive=True)):
    if os.path.isfile(f):
        print(os.path.relpath(f, d), f"{os.path.getsize(f)//1024} KB")
```

### Per-IP Specs (defer to the IP skills, but useful as Atlantic project sources)

- `~/docs/PCIe EP/` → `pcie-ep-ip` skill
- `~/docs/D2D/` → `d2d-ip` skill
- `~/docs/C2C/` and scale-up → `su-ip` skill
- `~/docs/RAS/`, `~/docs/PSoC/` → `ras-manageability` skill
- `~/docs/NOC/` → fabric/NoC supporting material

> The IP specs are shared across projects. What differs per project is the **RTL branch** (`main`
> for Atlantic, freezed-RTL for Iridium) and the project-specific **FW infra / manifests**.

### External References (Confluence)

| Reference | Link | Content |
|-----------|------|---------|
| Boot flow | https://elementlabs.atlassian.net/wiki/spaces/SWENG/pages/598671382/Boot+flow | SWENG boot-flow page — per-stage (FLA.RT / PRIMEBOOT / MANAGEMENT / POWER&THERMAL) boot sequence and INIT vs RUNTIME responsibilities across IPs. |

## Project Layout & Source

The Atlantic project source is typically checked out locally under `~/projects/atlantic/`.
Each subfolder (`main/`, `ws-*`, and other variant checkouts) is a workspace/variant checkout.
A typical workspace contains:

| Path | Content |
|------|---------|
| `fw/` | Firmware tree (Zephyr apps + per-IP drivers under `fw/ip/<ip>/`) |
| `fw/proj_infra/` | **Project-specific** Atlantic FW infra: build infra, post-build scripts, lua scripts, regressions |
| `fw/tools/fw_host_streaming_boot/` | Host-side streaming/flashless boot tooling |
| `kmd/` | Kernel-mode driver(s) for the Atlantic Linux stack |
| `sim/`, `experiments/`, `evidence/`, `logs/` | Simulation, experiment runs, captured evidence |
| `<repo>/.github/` | Per-repo skills / AI resources. **Every** repo in the workspace carries its own `.github/` with additional skills and AI context — consult them alongside this skill. |

**Local-first strategy** (this skill is global / user-level — the workspace may or may not
contain the source):
1. Prefer reading from a local checkout under `~/projects/atlantic/<variant>/...`.
2. Identify the deployment path of an IP driver via the project manifest
   (`project.atlantic.sw.fw.infra.star` and other `.star`/`octl` manifests).
3. If a needed repo is not local, use the GitHub search tools against the relevant
   `Elmnt-Internal/ip.<ip>.*` repos (see the IP skills for exact repo URLs). **Use the `main`
   branch** for Atlantic RTL (the freezed-RTL branch is for previous projects like Iridium).
4. The workspace is multi-repo, with its repo set and layout defined by the `octl` manifest.
   Each repo has a `.github/` directory containing additional skills / AI resources — check
   the relevant repo's `.github/` for project- or IP-specific guidance before/while researching.

### Build / Workspace Conventions

- Workspaces are initialized with `octl ws init -p atlantic -m <manifest>`.
- Project-specific build behavior lives in `fw/proj_infra/build_infra/` and
  `project.atlantic.sw.fw.infra.star` (collects artifacts per variant).
- To build Atlantic FW apps for a board/variant/platform, use the **`build-fw`** skill.

## Knowledge Base (Expert Memory)

This skill keeps an **expert memory** of everything it has already researched and verified about
the Atlantic project, so repeat questions are answered fast without re-reading the (large) PRM,
NoC spec, or source tree.

- **Location:** `~/.copilot/skills/atlantic-prj/knowledge/`
- **Index:** `~/.copilot/skills/atlantic-prj/knowledge/INDEX.md` — catalog of recorded topics
- Detail files: `knowledge/<topic-slug>.md` — verified findings with full citations

The knowledge base is **always consulted first** (Step 0) and **always updated after**
non-trivial research (final step). This is what makes the skill grow into a true Atlantic project
expert over time.

## Procedure

### Step 0: Consult the Knowledge Base (do this first)

1. Read `~/.copilot/skills/atlantic-prj/knowledge/INDEX.md` and scan the topic table.
2. If the question overlaps a recorded topic, read that detail file. Also full-text search the
   `knowledge/` directory for the question's keywords:
   ```python
   import os, glob
   kb = os.path.expanduser("~/.copilot/skills/atlantic-prj/knowledge")
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
| Address map, memory regions, NPA, routing, fabric, credits | Atlantic NoC spec | Atlantic PRM |
| Device coordinates, scale-up topology, redundancy/yield | Atlantic NoC spec | PRM (Binning) |
| FIU, reset/boot flow, trace, eFuse, GPIO/straps | Atlantic PRM | source `fw/` |
| Security (SL/SG, CfgR, RR, PB, RAC/WAC, Boot ROM) | Atlantic PRM | `fw/proj_infra/scripts/lua/sec_access_ctrl/` |
| Binning / redundancy (VCore, PBOX, D2D/PCIe isolation) | Atlantic PRM | NoC spec (Redundancy) |
| ECC, interrupts/PLIC, MMU, I-cache, scheduler | Atlantic PRM | source `fw/` |
| VCore / VME / datapaths | Atlantic NoC spec | PRM |
| PSOC, CCE, manageability, RAS, power/thermal | Atlantic PRM | `ras-manageability` skill |
| Single-IP deep dive (PCIe EP, D2D, C2C/SU) | the IP skill | PRM/NoC for project context |
| Build, variants, manifests, project infra | `fw/proj_infra/`, `.star` manifests | `build-fw` skill |
| RTL implementation (any IP) | IP RTL repo, **`main` branch** | the IP skill |

### Step 2: Search the Source

Use `python-docx` for `.docx` specs and the workspace search tools (grep/file/semantic) for the
source tree.

#### DOCX keyword search across paragraphs:
```python
import docx
doc = docx.Document("/home/yoniizu/docs/Atlantic/<spec>.docx")
for i, para in enumerate(doc.paragraphs):
    if "<keyword>" in para.text.lower():
        print(f"Para {i} [{para.style.name}]: {para.text[:300]}")
```

#### DOCX heading map (locate a section, then read its range):
```python
import docx
doc = docx.Document("/home/yoniizu/docs/Atlantic/<spec>.docx")
for i, p in enumerate(doc.paragraphs):
    if p.style.name.lower().startswith("heading") and p.text.strip():
        print(i, p.style.name, "|", p.text.strip())
```

#### DOCX search tables (register/field definitions live in tables):
```python
import docx
doc = docx.Document("/home/yoniizu/docs/Atlantic/<spec>.docx")
for t_idx, table in enumerate(doc.tables):
    for r_idx, row in enumerate(table.rows):
        row_text = " | ".join(cell.text.strip() for cell in row.cells)
        if "<keyword>" in row_text.lower():
            print(f"Table {t_idx}, Row {r_idx}: {row_text}")
```

For the **source tree**, prefer the workspace tools (`grep_search`, `file_search`,
`semantic_search`, `read_file`) under `~/projects/atlantic/<variant>/`. Fall back to GitHub
search tools (RTL on the **`main`** branch) for repos not checked out locally.

### Step 3: Extract and Answer

1. Search broad keywords first, then narrow to the right section/table.
2. For project flows (reset, boot, security), trace the PRM description **and** confirm against
   the implementing code/scripts when available.
3. When a question spans multiple IPs, give the project-level picture here and point to the
   specific IP skill for the deep dive.
4. Where Atlantic material is not yet populated but the IP is inherited from Iridium, answer from
   the IP skill / `iridium-prj` and **flag it as inherited**, not Atlantic-confirmed.
5. **Always cite** the source: PRM/NoC paragraph or table index, source file + path + symbol,
   or manifest/`.star` path.

### Step 4: Record Verified Findings (do this last)

After answering a non-trivial question, persist what you verified so the skill remembers it:

1. Create or update `~/.copilot/skills/atlantic-prj/knowledge/<topic-slug>.md` using the template
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
- **Address map / NPA / regions**: NoC spec "Address Map" section.
- **Reset & boot**: PRM "Reset Flow" and "Boot Time Estimation"; for the overall FW boot sequence
  and per-stage INIT vs RUNTIME responsibilities, see the SWENG **Boot flow** Confluence page
  (id 598671382).
- **Security**: PRM "Security" (SL/SG, CfgR, RR, PB, RAC/WAC, Boot ROM); implementing scripts
  under `fw/proj_infra/scripts/lua/sec_access_ctrl/`.
- **Redundancy/binning**: PRM "Binning" cross-referenced with NoC spec "Redundancy".
- **Scale-up / C2C**: PRM "C2C" + NoC spec "Scale-Up Connectivity" / "Device Routing".
- **RTL**: use the IP repo's **`main`** branch for Atlantic (freezed-RTL branch is for Iridium
  and earlier projects).
- **Tables**: Register, field, and mapping definitions are usually in tables — search both
  paragraphs AND tables.
