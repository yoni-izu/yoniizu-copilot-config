---
name: noc-ip
description: 'Answer NoC (Network-on-Chip) / fabric IP design, address-map, routing, and FIU questions using the internal Atlantic2 NoC specification and NoC RTL. Use when: NoC, network on chip, fabric, address map, memory regions, NPA, device coordinates, MEM fabric, mRTR/pRTR, virtual channels, data merging, MNG/CTRL/DBG/TRC/ECT/TS fabrics, AxUSER, FIU, address decode/hashing, memory isolation, traffic rate limiting, reset isolation, channel selection, scale-up connectivity, rail, routing algorithm, CfgR/RR, region selection, DieID.'
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
- **A2 RTL = `main`**; **A1/Iridium RTL is frozen** on the `iridium` branch. To see A2 NoC RTL
  changes, diff `origin/iridium..origin/main` (see the Procedure).
- The A2 fabric is a set of **non-intrusive, low-latency fabrics**: one **MEM** data fabric plus
  the **MNG / CTRL / DBG / TRC / ECT / TS** sideband fabrics, glued to endpoints by the **FIU**.

## Document Catalog

> **Rule of thumb:** the highest-revision NoC spec is the current A2 doc. As of 2026-08-30 the
> newest is **Rev 1.4.0** in `~/docs/NOC/` (supersedes Rev 1.2.0 in `~/docs/A2/NOC/`). The spec
> structure is stable across 1.2.0→1.4.0; recent changes are content refinements (see rev
> history: CCE descriptor, FIU outstanding-txn limit, NoC routing algorithm, device coordinates).

| File | Path | Content |
|------|------|---------|
| **Atlantic2 NoC Specification Rev 1.4.0** | `~/docs/NOC/Atlantic2 NoC Specification Rev 1.4.0.docx` | **Current A2 NoC spec** — address map, redundancy, device coordinates, scale-up connectivity, routing, MEM fabric, sideband fabrics, AxUSER, FIU, performance, RAS, security |
| Atlantic2 NoC Specification Rev 1.2.0 | `~/docs/A2/NOC/Atlantic2 NoC Specification Rev 1.2.0.docx` | Earlier A2 revision (diff for recent changes) |
| FIU uArch | `~/docs/NOC/FIU_uArc.docx` | FIU micro-architecture |
| FIU AXI Integration | `~/docs/NOC/FIU AXI Intergration.pptx` | FIU ↔ AXI integration deck |

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

Consult it first (Step 0); update it after non-trivial research (final step).

## Procedure

### Step 0: Consult the Knowledge Base
Read `knowledge/INDEX.md`; full-text search `knowledge/` for the question's keywords.

### Step 1: Classify the Question
- Address map / regions / NPA → Address Map section (+ `csr-bot` for absolute addresses).
- Routing / coordinates / region selection → Device Coordinates + Device Routing + routing algo.
- A specific fabric → that fabric's section (MEM / MNG / CTRL / DBG / TRC / ECT / TS).
- Isolation / rate-limit / reset-iso / security → FIU section.
- RTL behavior → `ip.noc.rtl` (diff `iridium..main` for the A2 delta).

### Step 2: Search the Spec (`python-docx`)
```python
import docx
doc = docx.Document("/home/yoniizu/docs/NOC/Atlantic2 NoC Specification Rev 1.4.0.docx")
# heading map:
for i,p in enumerate(doc.paragraphs):
    if p.style.name.lower().startswith("heading") and p.text.strip():
        print(i, p.style.name, "|", p.text.strip())
# keyword search across paragraphs AND tables (address-map / field defs live in tables).
```

### Step 3: Search the RTL
```bash
# A2 delta = what landed on main since the Iridium (A1) freeze:
gh api repos/Elmnt-Internal/ip.noc.rtl/compare/iridium...main --jq '.files[].filename'
# or, in a local checkout:
git -C <ip.noc.rtl> log --oneline origin/iridium..origin/main
git -C <ip.noc.rtl> diff origin/iridium..origin/main -- <path>
```

### Step 4: Answer + cite (spec para/table index, RTL repo/branch/commit, FIU doc).

### Step 5: Record Verified Findings
Create/update `knowledge/<slug>.md`; add a row to `INDEX.md` (newest at top); cite sources; when
a finding is contradicted later, correct it.

### Search Strategy Tips
- **Acronyms**: spec has a Terminology/Acronyms section near the top.
- **Tables**: address-map ranges and field/bit definitions live in tables — search both.
- **A2-vs-A1 / recent changes**: read the spec **Revision History** table and diff Rev 1.2.0 vs
  1.4.0; for implementation reality diff `ip.noc.rtl` `iridium..main`.
