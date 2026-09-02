# A2 HSI FW — Jira work-breakdown structure (the planning template)

**Scope:** where A2 SW work is filed and how it is shaped. Companion to
[a2-fw-planning-gaps.md](a2-fw-planning-gaps.md) (which lists *what* the work is).
Verified from Jira 2026-08-30. **Nothing has been written to Jira** — the drafted plan lives at
`~/a2-plan/` (`README.md`, `a2-fw-plan.yaml`, `pure-sw-work.md`, `mirror/jira-mirror.json`).

## Hierarchy constraint (hard)

`Initiative = hierarchyLevel 2` · `Epic = 1` · **`Story = 0` and `Attribute = 0`**.
A Story/Attribute therefore **cannot** be parented to an Initiative — an Epic is mandatory.
This is the root cause of the 81 orphaned A2 L0 items.

## Three generations of A1 structure — only the third is live

| Gen | Where | Example | Children today |
|---|---|---|---|
| 1 — per-IP *feature* Initiatives | IPPCIESW-11…76, IPD2DSW-1…27, IPSESW-1xx | "PCIe EP: BAR Mapping Unit (BMU)" | **0** (dead) |
| 2 — per-IP *category* Initiatives | IPPCIESW-491…500, IPD2DSW-141…149 | "Boot & Link Bring-Up" | **0** (abandoned) |
| 3 — **project-level HSI category Initiatives** | **PRJIRDMSW-3187…3199 + 4958…4960** | "HSI Boot & Link Bring-Up" | **~100 Epics** (LIVE) |

Corrects an earlier note that said category initiatives "must live in IPPCIESW/IPSESW/IPD2DSW" —
that was Gen 2, and it was dropped. The live A1 shape is:

```
PRJIRDMSW  Initiative (L2)  "HSI <category>"
  └─ IPPCIESW / IPSESW / IPD2DSW  Epic (L1)   <- carries the component (= owning team)
       └─ Story / Attribute (L0)
```

## A1 Epic field template (verified on IPPCIESW-390, Done)

`parent = PRJIRDMSW-3187` · `components = [ip-pcie-firmware-ep]` · `Team (cf 10001) = HSI Firmware` ·
`ASIC Project (cf 10334) = Iridium` · `fixVersion = iridium-system-sw-6.0` ·
`sprint (cf 10020) = FW HSI MS6.4` · `original estimate = 1w 3d` · priority, duedate, assignee set.

## A2 home: `PRJATLSW` (project-atlantic2-sw)

Already exists and already holds **40 project-level HW/arch Initiatives** (PRJATLSW-1…62: address
map, TP4/TP8/TP16, reset domains, routing, CCE, EP selection…), 12 errata Stories and 5 PLDM
Attributes. It is the A2 twin of PRJIRDMSW ⇒ the A2 **SW Feature Initiatives** belong there.
(Other A2 projects: PRJATLH hwarch, PRJATLVAL, PRJATLDFT, PRJATLPROD. A3 exists: PRJATL3H.)

## Rules adopted for A2 (team-lead directive, 2026-08-30)

- The **FW HSI team lead opens the Initiatives**; the agent's job is to break architectural
  initiatives down into Epics and component-level FW work.
- **HW/Arch Initiatives are LINKED to SW Feature Initiatives — never used as parents.** We do not
  plan our work under HW initiatives.
- One Initiative may hold Epics from **several teams**; the **component decides ownership**
  (`ip-pcie-firmware-ep`, `ip-se-firmware`, `ip-d2d-firmware`, `ip-pcie-kmd`, `ip-pcie-sival`,
  `compute-kmd`, `ip-psoc-kmd`, `ip-psoc-firmware-bootrom`, `ip-noc-firmware`, `device-profiler`).
- If work has no initiative that can parent it, **create one** (→ the three SW-only initiatives).

## The HSI feature categories — a taxonomy, NOT a template to clone

The 13 categories (Address Translation · Boot & Link Bring-Up · Config Space & Register Access ·
Data Path · Error Handling & RAS · Interrupt Handling · MSI/MSI-X Interrupt Configuration ·
Messaging & Mailbox · Power Management · Reset Handling · Security & Access Control ·
Trace, Debug & Monitoring · Collective Operations) are good vocabulary and a bad template.

**Rule: create an Initiative only when the category has ≥1 Epic with A2-SPECIFIC content.**
Categories whose A2 content is only "re-run the A1 test on A2 silicon" are SIVAL/PLDM execution,
not feature development — they become Epics under a single **Carry-Over Re-Verification** Initiative
and graduate out when real A2 content appears.

**Evidence the set was never uniform:** A1's own per-IP attempt already trimmed it —
`IPPCIESW-491…500` = **10** categories (no Data Path / Interrupt Handling / Collective Ops);
`IPD2DSW-141…149` = **9** (no Address Translation / Config Space / MSI-X / Collective Ops).
Only the flat PRJIRDMSW generation used all 13, across all IPs at once.

**A2 scoring (A2 HW inits linked · A2 L0 items adopted), 2026-08-30:**
| Verdict | Categories |
|---|---|
| Create | Boot & Link (10·8) · Address Translation (16·4) · Data Path (17·4) · Collective Ops (17·6) · Security (6·2) · RAS (2·8+22 errata) · Reset (1·9) · Config Space (3·12, thin) |
| Merge | Interrupt Handling → MSI/MSI-X (only real A2 item is the PIW TuC MSI-X trigger) |
| Defer | Trace/Debug (1 real gap: Gen7 lane margining → park under Boot & Link) · Power Mgmt (L0p/PPW carry-over) |
| Drop | Messaging & Mailbox (0 A2 HW inits, 6 pure carry-over L0s) |

⇒ **9 feature Initiatives, not 13**, + 3 SW-only + 1 carry-over holding pen.

A1 also split two categories by consumer ("- for RIV" / "- for product") late in the program — a
milestone artifact, **not** something to replicate up front in A2.

## Plus three SW-only Initiatives A2 needs (no HW counterpart)

1. **A2 HSI SW Platform & Product Separation** — ⚠️ this is *applying* an already-decided org design,
   not inventing one. See "FW Cross Product Separation" below.
2. **A2 HSI Driver Architecture & Code Convergence** — shared DWC PCIe core for EP + SU DM (+ new RP
   personality), common PHY-FW layer, init/runtime split, MW scaffolding, capability flags.
3. **A2 HSI Verification & Test Infrastructure** — SE/D2D unit tests (currently zero), A2 PLDM
   enablement, SVE parity, A1/A2 dual regression, coverage-matrix upkeep.

## FW Cross Product Separation — the decided design (Confluence 1126858785, SWENG)

**"One branch, one family manifest, multiple builds."** A single `main` serves A1 (`v1`) plus up to
three parallel A2 FC-tag drops (`v2.1/v2.2/v2.3`) — 4 HW versions, zero code duplication.
Chip families are separated by **integration manifests, not branches**; all family chips build and
test from one workspace.

- Terminology (from "New CSR Design — ips/ + projects/ include trees", page 1333166302):
  **project** = per-customer silicon build (`a1`,`a2`,`b1`); **version** = shared CSR generation
  (`v1`,`v2`); **tag** = one VLSI fullchip drop. `csr_versions.star` holds `current`/`next` and
  `stable`/`unstable` slots.
- Constraints: new CSR drop **~weekly** during A2 · 4 active HW versions on `main` · **A1 logic must
  keep working, its DT is frozen** · zero code duplication (multiple `.c` per IP is fine) · use
  Zephyr tooling (DT/Kconfig/CMake) · **no `#if`/`#ifdef` in driver or application C** · maximum
  abstraction, differences pushed into DT overlays + version-specific `.c`.
- `#ifdef <fc_tag>` (+ `-D<fc_tag>`) is allowed **only** inside generated CSR headers in `shared_defs/`.
- Layers: **DTS** — common project `.dtsi` + per-version overlays (`csr_v1.dtsi`, `csr_v2.dtsi`) via
  `-DDTC_OVERLAY_FILE` (STM32-family pattern). **HAL/Drivers** — multiple `.c` per IP chosen by
  Kconfig (Zephyr `i2c_ll_stm32.c` + `_v1.c`/`_v2.c` precedent). **Application** — thin, event-driven.
- Build selection: `CSR_PROJECT` / `CSR_VERSION` / `CSR_FC_TAG` assemble the `-I` list over the
  `ips/` + `projects/` trees in `el_zephyr.cmake`; CI does parallel builds + filtering.
- **The page's worked example is our IP:** `dwc.c` (common) + `dwc_100x.c` + `dwc_200x.c`.
- Children: Why FW Needs the Linux Kernel Model (1145045011) · Strategy & Process (1130823711) ·
  FW Architecture (1130168377) · Build Integration (1129840764) · Code Examples (1130168408) ·
  Decision Tree & Enforcement (1131085839).

HSI gap vs that target: no version split in any driver, no `csr_v*.dtsi`, flat `pcie/include/`, and
`grep -rn "IRIDIUM|ATLANTIC|CONFIG_EL_PROJ" --include=Kconfig* --include=CMakeLists.txt pcie se d2d`
returns **nothing**. A1 epics IPPCIESW-558 / IPSESW-302 / IPD2DSW-192 (under IPINFRASW-1773) are the
"prepare" half and are tagged Iridium; the "deliver" half is unfiled.
**Synergy:** the mandated "common `.c` + version-specific `.c`" shape is exactly what the EP/DM
shared core needs — do product separation and driver convergence in the PCIe/SE drivers as one motion.

Measured evidence for #2 (A1 tree `~/projects/iridium/main/fw/ip`): `snps_ep_ctl/dwc_ep_ctl.c` 877
LOC vs `snps_dm_ctl/dwc_dm_ctl.c` 1024 LOC, **zero shared symbols**, but semantically paired APIs
(DBI access, link-up, LTSSM enable/decode, link speed, retrain/EQ, BAR/iATU, init). EP uses the full
SNPS PMD SDK (`snps_pcie6_phy/`); SE hand-rolls `el_se_phyfw.c` (477 LOC) and does not reference the
SDK at all. Also: **no project conditionals exist anywhere** in `pcie/ se/ d2d/` Kconfig/CMake.

## Open questions for the PMs before creation

1. PRJATLSW vs per-IP for the SW Feature Initiatives.
2. Naming: `A2 HSI <category>` vs bare `HSI <category>` (A1 used the bare form inside PRJIRDMSW).
3. Initiative↔Initiative link type for SW↔HW traceability.
4. Home for the 3 SW-only initiatives (PRJATLSW vs IPINFRASW, where A1 put IPINFRASW-1773).
5. A2 milestone / fixVersion scheme before epics get dates.

## Sources
- Jira (cloudId c155e965-159d-4564-a0a0-16eb85204b7f), 2026-08-30:
  `project in (IPSESW,IPPCIESW,IPD2DSW) AND issuetype = Initiative AND cf[10334] = "Iridium"` (100+,
  all childless); `parent in (IPPCIESW-491…500, IPD2DSW-141…149)` → **0**;
  `parent in (IPPCIESW-32,52,64,45, IPD2DSW-1,13)` → **0**; `project = PRJATLSW` (57);
  `getJiraIssue IPPCIESW-390 fields=*all`; `getVisibleJiraProjects searchString=PRJ`.
- Local A1 FW tree `~/projects/iridium/main/fw/ip/{pcie,se,d2d}`.
- Confluence: **FW Cross Product Separation** 1126858785 (+ children) · New CSR Design 1333166302.
- **A2 TuC verified in RTL** 2026-08-30: `ip.pcie.rtl` `main`,
  `piw_macro/rtl/piw_lbw_mng.v` ~L871 — `tuc #(.PM_LINE_NUM(64), .DIV_SUPPORT(0), .MUL_SUPPORT(0)) tuc(…)`,
  master on CTRL (`tuc2ctrl_axi_if`) + MNG (`mng_ups_axi[1]`), program loaded from CSR
  (`csr2tuc_pm[64]`). Absent on branch `iridium` (0 hits) ⇒ genuinely new in A2. Module source
  `ip.psoc.rtl tuc_wrap/rtl/tuc_wrap.v`; A1 precedent `psoc_dw/psoc_spi` (`tuc_code.hex`,
  `psoc_spi_tuc_fw.txt`, `tuc_convrt*.pl`). Corroborates PIW designer Sagi Zamir (Teams, 2026-08-30).
  Full detail in `/memories/repo/pcie-uc-reality.md`.
