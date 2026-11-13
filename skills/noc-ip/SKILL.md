---
name: noc-ip
description: 'Answer NoC (Network-on-Chip) / fabric IP design, address-map, routing, and FIU questions using the internal Atlantic2 NoC specification, with RTL questions answered by Neo (neo-ask). Use when: NoC, network on chip, fabric, address map, memory regions, NPA, device coordinates, MEM fabric, mRTR/pRTR, virtual channels, data merging, MNG/CTRL/DBG/TRC/ECT/TS fabrics, AxUSER, FIU, address decode/hashing, memory isolation, traffic rate limiting, reset isolation, channel selection, scale-up connectivity, rail, routing algorithm, CfgR/RR, region selection, DieID.'
argument-hint: 'Ask about the NoC/fabric IP — address map, routing, a specific fabric (MEM/MNG/CTRL/DBG/TRC/ECT/TS), FIU, or the RTL'
---

> **Portal handoff:** For Team Portal/dashboard questions, feature requests,
> UI/API changes, bugs or operations, load
> [portal-maintainer](../portal-maintainer/SKILL.md). Domain records and
> workflows remain owned by this skill; coordinate mixed changes with the maintainer.

# NoC (Network-on-Chip) / Fabric IP Expert

## When to Use

- **NoC / fabric IP** design, integration, and behavior questions (die-internal interconnect).
- **Address map** topics: memory regions, Device / Host / Management / Control / Debug address
  spaces, **NPA** (Network Physical Address), DPA, LMEM/GMEM ranges, CfgR/RR alignment.
- **Routing**: device coordinates, inner/outer die routing, the **NoC routing algorithm**,
  region selection (X/Y + DieID), channel/plane selection.
- **MEM fabric**: Plane Selection, **Middle Router (mRTR)**, **PCIe Router (pRTR)**, Virtual
  Channels, Data Merging, VCore connectivity.
- **Sideband fabrics**: **MNG** (management, hashing, VME decode), **CTRL** (control), **DBG**
  (debug), **TRC** (trace), **ECT** (breakpoint), **TS** (timestamp).
- **AxUSER** bus semantics (PF/VF, DestID, ASID, MX/MXFP enable, RUSER).
- **FIU** (Fabric Interface Unit): address decode/hashing, security config, **memory isolation**,
  **traffic rate limiting**, debug/telemetry counters, RAS, **reset isolation**, credit handling,
  channel selection, outstanding-transaction limits, OAM connectivity.
- **Scale-up connectivity** at the fabric level (rail connectivity, redundant scale-up I/F on
  Die[3]) — for the scale-up *protocol* (C2C/PCIe DM), defer to `su-ip`; for the project-level
  scale-up domain (Ranks/GPU/M2M/INC), defer to `atlantic2-prj`.
- **NoC RTL** implementation questions.

> **Scope:** NoC is **not** an HSI-owned FW IP, but its address map, routing, FIU isolation, and
> reset-notification behavior directly shape HSI FW (PCIe/D2D/SE) integration — so this skill
> exists to answer "what does the fabric do / what changed" questions. For project context and
> the A2-vs-A1 picture, use `atlantic2-prj`; for register addresses use `csr-bot`.

## Project Context

- The NoC spec here is the **Atlantic2 (A2)** fabric spec. NoC owner: **Maxim Leshin** (per the
  spec revision history). The RTL repo is **`Elmnt-Internal/ip.noc.rtl`**; SW/CSR/FW repos are
  `ip.noc.sw.csr` and `ip.noc.sw.fw`.
- **A2 RTL = `main`**; **A1/Iridium RTL is frozen** on the `iridium` branch. For the delta,
  ask Neo (`~/bin/neo-ask --chip a2`) for the delta (see the Procedure).
- The A2 fabric is a set of **non-intrusive, low-latency fabrics**: one **MEM** data fabric plus
  the **MNG / CTRL / DBG / TRC / ECT / TS** sideband fabrics, glued to endpoints by the **FIU**.

## Document Catalog

Use the [NoC Git source map and pinned lookup](../../installed-plugins/ai-toolkit/fw-soc/references/specification-sources.md#noc):
general architecture in `Elmnt-Internal/module.arch.hwarch.specs`; A2 and
legacy A1 NoC in `Elmnt-Internal/project.atlantic2.hwarch.specs`, both on `main`.
The deprecated project repo is retained by explicit approval.

**Verify the intended revision, never assume latest:** the unversioned A2
Markdown cover says 1.0.1; the Rev1.5.0 folder's Markdown says 1.4.0.
Version-specific v1.5.0 extracts and a Rev1.7.0 Word original exist; verify
conversion provenance and project intent before choosing. Exact FIU uArch and
AXI integration deck counterparts were not verified. Historical notes retain
old locators as secondary evidence, not current spec authority; no local fallback.

### Spec section map (H1/H2)

`Address Map` (Memory Regions, Device / Host / NPA / Management / Control / Debug Address Space) ·
`Redundancy` (Inner Die, Platform) · `Device Coordinates` · `Scale-Up Connectivity` (Rail) ·
`Device Routing` (Inner/Outer Die) · `MEM Fabric` (Plane Selection, mRTR, pRTR, Fabric
Connectivity, Virtual Channels, Data Merging, VCore Connectivity) · `BW and Latency Requirements`
(LMEM, GMEM) · `Management Fabric (MNG)` (Hashing Function, VME Decode) · `Control Fabric (CTRL)` ·
`Debug Fabric (DBG)` · `Trace Fabric (TRC)` · `Breakpoint Fabric (ECT)` · `Timestamp Fabric (TS)` ·
`Fabric Connectivity Summary` · `AxUSER` · `FIU` (Address Decode, Address Hashing, Security Related
Configurations, Memory Isolation, Traffic Rate Limiting, Debug/Telemetry Counters, RAS, Reset
Isolation, Credit Handling, Channel Selection) · `Performance` · `Trace and Debug` · `RAS` ·
`Security`.

## Knowledge Base (Expert Memory)

- **Location:** `~/.copilot/skills/noc-ip/knowledge/`
- **Index:** `~/.copilot/skills/noc-ip/knowledge/INDEX.md`
- Detail files: `knowledge/<topic-slug>.md` — verified findings with citations.

Consult source-free knowledge first when available (Step 0); update it with
paraphrased findings after non-trivial research (final step).

## Procedure

### Step 0: Consult the Knowledge Base
Use the topic names in this skill; consult an index or note only if it is
already known not to contain HDL excerpts. Do not bulk-search the knowledge
directory. If none is verified source-free, use the specification or ask Neo
(`neo-ask`) for a paraphrased finding.

### Step 1: Classify the Question
- Address map / regions / NPA → Address Map section (+ `csr-bot` for absolute addresses).
- Routing / coordinates / region selection → Device Coordinates + Device Routing + routing algo.
- A specific fabric → that fabric's section (MEM / MNG / CTRL / DBG / TRC / ECT / TS).
- Isolation / rate-limit / reset-iso / security → FIU section.
- RTL behavior → ask Neo (`~/bin/neo-ask --chip a1|a2`).

### Step 2: Search the Git specification

Resolve a commit, fetch the exact mapped NoC Markdown using `gh api` from the
source map and inspect its revision history. Search headings, text and tables
(including address maps and fields). Cite repository, commit, path and
section/table. Folder names alone do not establish revision or currency.

### Step 3: Search the RTL
Follow [rtl-vm-policy](../rtl-vm-policy/SKILL.md): ask **Neo** —
`~/bin/neo-ask --chip a1|a2 "<NoC question>"` (A1 = Iridium freeze, A2 = `main`).
Relay only its paraphrased findings and citations, never HDL or patches. If Neo
is unavailable, report the prerequisite and stop; no local, VM-worktree or
GitHub RTL fallback.

### Step 4: Answer + cite (spec para/table index, RTL repo/branch/commit, FIU doc).

### Step 5: Record Verified Findings
Create/update `knowledge/<slug>.md`; add a row to `INDEX.md` (newest at top); cite sources; when
a finding is contradicted later, correct it.

### Search Strategy Tips
- **Acronyms**: spec has a Terminology/Acronyms section near the top.
- **Tables**: address-map ranges and field/bit definitions live in tables — search both.
- **A2-vs-A1 / recent changes**: compare the verified intended A2 revision with
  mapped Iridium NoC, stating provenance discrepancies. For implementation
  reality ask Neo (`neo-ask --chip a2`) for the RTL delta.
