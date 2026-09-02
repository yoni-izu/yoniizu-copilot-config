# A2 (Atlantic2) — HSI FW Planning: Anchors, Gaps, Redundancies

- **Recorded:** 2026-08-30
- **Status:** Verified from Jira (`project in (IPSESW, IPPCIESW, IPD2DSW) AND cf[10334] =
  "Atlantic2"` → **155 issues**) + RTL (`iridium...main`) + A2 KB
- **A2-vs-A1 delta:** A1 has a full FW execution hierarchy (PRJIRDMSW "HSI …" initiatives →
  ~100 IP-SW **Epics** → stories). A2 has **74 architecture Initiatives + 81 orphan L0 items and
  ZERO Epics** — i.e. an architecture layer with **no FW execution layer under it**.
- **Tags:** jira, planning, firmware, hsi, a2, atlantic2, initiatives, epics, attributes, gaps,
  duplicates, redundant, ipsesw, ippciesw, ipd2dsw, pldm, palladium

## Scope / Question
Which A2 tickets in IPSESW / IPPCIESW / IPD2DSW can FW work be filed under; what FW work is
missing from Jira; which existing tickets need no FW attention at all.

## The shape of the A2 backlog (verified counts, 2026-08-30)

| Project | Initiative (L2) | Story (L0) | Attribute (L0) | Total |
|---------|-----------------|------------|----------------|-------|
| IPSESW | 62 | 5 | 12 | 79 |
| IPPCIESW | 12 | 4 | 56 | 72 |
| IPD2DSW | 0 | 4 | 0 | 4 |
| **Total** | **74** | **13** | **68** | **155** |

- Status: 102 Backlog, 41 Triage, 7 Done, 1 In Planning, 1 In progress, 2 Reopened, 1 Closed.
- **0 Epics.** **80 of 81 L0 items have no parent** (only IPPCIESW-614 is parented, and to
  MODINFSDKSW-12948). Jira hierarchy here: Initiative = level 2, **Epic = level 1**,
  Story **and** Attribute = level 0 → stories/attributes **cannot** hang off an Initiative
  directly; an Epic is mandatory in between.
- Components: 45 `ip-pcie-firmware-ep`, 9 `software_arch`, 5 `ip-pcie-sival`, 3 `ip-pcie-kmd`,
  2 `ip-se-firmware`, 1 each `ip-d2d-firmware` / `ip-psoc-kmd` / `ip-psoc-firmware-bootrom` /
  `device-profiler` / `compute-kmd`; **86 items carry no component at all** (all 74 Initiatives
  + 12 IPSESW attributes).

### A1 baseline for comparison (the pattern A2 is missing)
`PRJIRDMSW-3187…3199 + 4958/4960` = 15 program-level "**HSI …**" initiatives (Boot & Link
Bring-Up, Error Handling & RAS ×2, Data Path ×2, Messaging & Mailbox, Power Management, Reset
Handling, Trace/Debug/Monitoring ×2, Security & Access Control, Address Translation, Config Space
& Register Access, Interrupt Handling, MSI/MSI-X, Collective Operations). ~100 FW **Epics** in
IPPCIESW/IPSESW/IPD2DSW hang off them. **No A2 equivalent exists** (JQL
`issuetype = Initiative AND summary ~ "HSI"` returns only PRJIRDMSW keys).
Other A2 IP teams mirror initiatives into `IP<ip>RTL-*` and `IP<ip>VAL-*` Epics — **HSI has no
SW/FW mirror**.

## 1. Anchors — initiatives FW work SHOULD be filed under

> File an **Epic per initiative** (component `ip-pcie-firmware-ep` / `ip-se-firmware` /
> `ip-d2d-firmware`), then stories under it. Long-term: create the A2 program initiatives
> mirroring PRJIRDMSW-3187…3199 first.

### PCIe EP (IPPCIESW) — 10 of 12 initiatives are FW-bearing
| Initiative | FW work it should host |
|---|---|
| **382** Transition to Gen7 PHY (ICCM/DCCM) | New DWC Gen7 PHY (1.10a) FW blob + `fw_sram_copy_handler` sizing (`PPW_FW_ICCM_SRAM_DPT=32768`), PHY init/bring-up sequence, artifact/flash packaging |
| **383** Transition to Gen7 x4 controller | Controller cfg deltas (single x4 vs Gen6 dual x4/x8), link training, capability list, MPS/MRRS, FLIT/256B, error handling |
| **381** Add external FEC | FW enable/select of the 4 standard + 3 custom FEC modes |
| **384** Add general-purpose TuC | **NEW FW surface** — TuC sequences for workarounds; RTL already has `piw - add tuc as mstr on ctrl+mng fabric` (+ `piw tuc remove multiply and divide capabilities`) |
| **386** Add PTM | PTM driver: enable, context, clock sync, error paths ("needs SW work") |
| **387** Application control over PME_TO_Ack | `CX_APP_CTRL_PME_TO_ACK=1` → FW must drive `app_ready_xmt_pme_to_ack`; L2/L3 entry/exit |
| **464** LMEM Address Virtualization | Program the virtualization mapping (HW exists: `piw_tlp_dns_lmem_addr_virtualization.v`) |
| **546** Align BAR mapping to A2 address space | BMU config: 4×64 GB BAR → SledID=0xFF, ChipID from ModuleID straps, OpType=0 |
| **551** INC BAR mapping | Addr[38] device-vs-INC select, CfgMask, Scorpio-2 (10b) vs Scorpio-3 (12b) ContextID |
| **552** Dedicated SL per engine/function | Per-PF/VF **and ELBI** security-level configuration |

### Scale-Up / C2C (IPSESW) — ~35 of 62 initiatives are FW-bearing
Grouped by the FW capability they imply:
- **Address-translation / table provisioning (largest FW cluster):** 273 (A2 BAR for inter-sled),
  274 (8-entry INC LUT, SW-configurable index), 280 (256-entry nLUT DVA→NPA), 283 (reserved
  ranges: E2E credit / EOC / GPU-MC-update / EOB base+limit), 314/315 (OpType + source id),
  316 (A2↔GPU dest translation), 317 (RS/AR/AG-MC INC BAR+Mask pairs), 207 (DPA→NPA, deprecated
  ticket but the FW work is real).
- **Multicast:** 269 (256-entry MC table), 267 (GPU-written MC-table update region BASE/MASK),
  284 (MC-table usage enable per Rx port — "software configurable feature"), 296 (FDup/EBR).
- **Mode / ordering configuration:** 285 (intra-sled mode register), 265 (non-compliant ordering),
  281/282 (P/C compliant ordering + flag-aggregation structures), 210 (SL override value),
  209 (RO control), 325 (AxUSER propagation policy).
- **Aggregation buffers:** 278 (per-opcode enable/disable register), 279 (flag-flush policy
  register), 200 (Rx aggregation), 277 (4KB crossing).
- **INC / CCE operation:** 263 (**FW reads MPS and configures the controller**; interrupt on
  MPS<256B), 264 (EOC), 298 (**EOB delivered over CTRL fabric → FW**), 268, 270, 275, 276,
  291/292/293/294 (DMT enable + result handling), 299 (INC/A2 duplication coexistence).
- **Platform / RAS / perf:** 144 (Gen7 x2), 146 (external FEC), 205 (RR memory protection),
  208 (rate limit), 214 (compression), 215 (isolation + drop mode), 216 (3×64b BAR — "alternatively
  trigger an interrupt to be handled by FW"), 306 (**C2C IDLE status to FW**).

### D2D (IPD2DSW) — no initiatives at all
Only 4 `Check Errata - CLONE` stories (193-196). Consistent with the architect's "keep D2D as
is", but see gap G-9.

## 2. Gaps — FW work that is real but NOT filed

**Structural**
- **G-0 (blocking):** no A2 program initiatives ("HSI Boot & Link Bring-Up", …) and **no Epics
  anywhere** → the 68 attributes + 13 stories are orphans; nothing can be milestone-planned,
  and FW stories have no legal parent (Initiative→Story is not allowed at L2→L0).
- **G-1:** 86 items have **no component** → no FW/SIVAL/KMD ownership split; the 74 initiatives
  are all unassigned to a discipline.
- **G-2:** **no A2 FW project bring-up work item** — atlantic2 workspace/manifest, board+variant
  defs, CSR header regeneration from A2 regmaps, CI. The only A2 infra ticket is IPINFRASW-1921
  (dynamic runners); "FW product separation: PCIe/SE/D2D" (IPPCIESW-558 / IPSESW-302 /
  IPD2DSW-192, under IPINFRASW-1773) are tagged **Iridium**, not A2.

**PCIe EP** (keyword scan over all 155: 0 hits for margining / SR-IOV / L0p / equalization /
ECC / viral / trace; 1 hit for AER)
- **G-3:** Gen7 **link bring-up quality** — equalization presets, lane margining, PRBS/loopback,
  RAS-DES event counters. A1 had IPPCIESW-419 (Lane Margining), 409 (RAS DES), 389/390 (PHY
  bring-up + spec compliance). A2: nothing.
- **G-4:** **RAS / manageability re-baseline** — AER handling, error status/threshold/health
  attributes, SRAM-ECC + viral, crash dump. A1: IPPCIESW-411/414/415/343/286. A2: only
  IPPCIESW-561 (PLDM verify AER) and 600 (error counters on PLDM).
- **G-5:** **Security/virtualization carry-over** — IDE integration, TEE, SR-IOV/PF-VF
  (A1: IPPCIESW-291/181/369). A2: nothing (552 covers only SL config).
- **G-6:** **Power management** — L0p, L2/L3, PCIe power interrupts (A1: IPPCIESW-309). A2: only
  577 Emergency Power Break + 613 PME_TO_Ack (both PLDM-verify attributes).
- **G-7:** **4-die single-EP ownership model** — A2 shares ONE EP across 4 compute dies (A1 was
  2-die). Who owns EP config/reset/interrupts across dies, per-die reset isolation, drain. Nothing
  filed. (Related but insufficient: 603/604 reset isolation, 563/564 drain.)
- **G-8:** EFEC (381) and per-engine SL (552) have **no FW or verification item** under them at
  all — initiative-only.

**Scale-Up / C2C**
- **G-9 (biggest single gap):** the **N3/3nm DWC PCIe7 PHY on the DMP** — new PHY IP ⇒ new PHY FW
  download + init sequence + bring-up hardening. A1 had IPSESW-74 (C2C PCIe PHY FW download),
  49/50/51 (PHY/controller/bridge bring-up), 186 (PHY init hardening), 83 (Dual-Mode integration).
  A2 has **one attribute** (IPSESW-355 "PLDM - Work with C2C in Gen7"). No FW epic/story.
- **G-10:** **Dual-Mode Root-Port mode FW** — PCIe-Compliant Mode makes the SU port an RP toward
  third-party PCIe/GPU: enumeration, config-space access (ECAM, A1 had IPSESW-87), remote BAR
  programming, hot-plug, RP-side AER. Nothing filed.
- **G-11:** **LUT/table provisioning FW** — nLUT (256 entries), INC LUT (8), MC table (256),
  reserved-range base/limit registers, DMT enable. A1 had IPSESW-177 (Dynamic Scale-Up LUT
  provisioning from BMC); A2 has only PLDM-verify attributes 332/333. This is the single largest
  concrete FW deliverable implied by the C2C spec and it has no FW ticket.
- **G-12:** **C2C RAS re-baseline** — isolation/drop mode (A1: 76/86), ECC/viral in C2C RAMs
  (A1: 73/153), error + RAS-DES counters (A1: 154/155/156), lane margining (A1: 160), link-info /
  health attributes (A1: 150/151/152). A2: none.
- **G-13:** **Lane failover / multi-link resiliency** (C2C Rev2.1.1 RAS; NoC IPNOCSW-139 "TP8 –
  C2C port resiliency") — FW must react to dynamic width reduction / port loss. Nothing filed.
- **G-14:** **C2C IDLE + power** — IPSESW-306 exposes IDLE *to FW*; only PLDM-verify attribute 334
  exists. No FW feature story; no L0p/scale-up power story.
- **G-15:** **Compression** (IPSESW-214) — A1 had IPSESW-89 (compression engine). No A2 FW item.
- **G-16:** **INC / CommLib boundary undefined** — who programs commGroups / INC contexts (FW vs
  CommLib vs KMD) is not captured anywhere. IPSESW-324 only says "verify INC operation with a
  switch". Needs an explicit ownership decision + ticket.

**D2D**
- **G-17:** even under "keep as-is", the AlphaWave PHY swap (5nm + N3; RTL shows the PHY macros
  currently **stubbed**) needs at minimum a FW item "assess/adapt D2D PHY FW + link-init config
  for the new AW IP" (A1: IPD2DSW-134/35/125). Nothing filed.
- **G-18:** **4 compute dies + ring** vs A1 dual-die → D2D link-init topology/sequence FW work
  (A1: IPD2DSW-87 "Dual Die bringup"). Nothing filed.

## 3. Redundant / no-FW-attention tickets

**Self-declared duplicates or deprecated (IPSESW initiatives, all already `Done`)** — they inflate
the A2 initiative list and mislead planning:
| Key | Why |
|---|---|
| IPSESW-194 | description: "**duplicate with** IPSEH-86" |
| IPSESW-202 | "This is a **duplicate** one, already covered" |
| IPSESW-204 | "**Already covered** in IPSEH-67" |
| IPSESW-207 | "**Deprecated here.** Covered in the compliant Mode bucket" |
| IPSESW-209 | "**Covered in** IPSEH-67" (RO control config still real → fold into 281/325) |

**Explicitly "no design implementation needed — validation only" (IPSESW)** → SIVAL/verif, **not
FW**: **195** (ordering models — "handled by the Controller in AXI Mode"), **197** (MRRS/MPS
decomposition — "handled by the SNPS DM controller"), **199** (non-contiguous byte enables — same),
**201** (RCB — same).

**HW-internal, no FW hook:** IPSESW-**266** (flag aggregation — architect: "supposed to be fully
HW-internal without FW intervention"), IPSESW-**145** (voltage swing 0.9→1.1 V, analog/PHY),
IPSESW-**196/198/211/212** (TLP↔AXI datapath, HoL credit management), IPPCIESW-**385**
(suspension RX buffer — HW, `piw_tlp_dns_suspension_buffer.v`), IPPCIESW-**480** (750 ns flow
control = FC credit + retry-buffer depth sizing, CoreConsultant/HW).

**Duplicate attributes (PCIe PLDM backlog)** — same work filed twice under two naming waves
(`X on PLDM` vs `PLDM - X`):
| Keep | Duplicate to close |
|---|---|
| IPPCIESW-560 | **568** (and legacy **97**, already Closed) — "Check Cfg interception mechanism on Emulation" ×3 |
| IPPCIESW-562 | **609** — "work with secure BAR" |
| IPPCIESW-582 | **606** — "Hot reset delay" |
| IPPCIESW-583 | **607** — "Link Training Delay" |
| IPPCIESW-584 | **596** — "DS message mapping to INT" / "DS messages mapping to interrupts" |
| IPPCIESW-588 | **595** — "Indirect Upstream Message Generation" vs "Upstream messages generation through CSR" (overlap — merge or scope-split) |

**Premature:** IPPCIESW-**611** "Verify PIW TUC - PLDM" — the TuC initiative (384) is still
`Triage` and there is **no TuC instance in `ip.pcie.rtl` `main`** yet (only the PIW fabric-master
commits); keep, but block on 384.

**Misfiled (hygiene, not redundancy):** IPPCIESW-**625** "A2 PLDM – D2D enablement on PLDM"
carries component `ip-d2d-firmware` but lives in **IPPCIESW** → should be IPD2DSW.

## Recommended sequence
1. Create A2 program initiatives mirroring PRJIRDMSW-3187…3199 (or a `PRJ<A2>SW` project).
2. Create **one FW Epic per FW-bearing initiative** (section 1) and re-parent the 68 orphan
   attributes + 13 stories under them.
3. Set components on all 86 component-less items (FW / SIVAL / KMD split).
4. Close/merge the redundancies in section 3 (≈ 6 duplicate attributes + 5 deprecated
   initiatives), and label the 4 "validation-only" IPSESW initiatives as SIVAL-only.
5. File the gaps, priority order: G-9 (SU N3 PHY FW), G-11 (LUT/table provisioning),
   G-10 (RP-mode FW), G-3/G-4 (Gen7 bring-up + RAS), G-7 (4-die EP ownership), G-2 (A2 FW project
   bring-up), G-17/G-18 (D2D PHY + 4-die link init).

## Sources
- Jira JQL (cloudId c155e965-159d-4564-a0a0-16eb85204b7f), 2026-08-30:
  `project in (IPSESW, IPPCIESW, IPD2DSW) AND cf[10334] = "Atlantic2"` (155);
  `... AND issuetype = Initiative` (74, with descriptions);
  `project in (IPSESW, IPPCIESW, IPD2DSW) AND issuetype = Epic AND cf[10334] = "Iridium"` (A1
  baseline, 100+); `issuetype = Initiative AND summary ~ "HSI"` (15, all PRJIRDMSW).
- RTL: `Elmnt-Internal/ip.pcie.rtl` `iridium...main` — commits `piw - add tuc as mstr on ctrl+mng
  fabric`, `piw tuc remove multiply and divide capabilities`; files
  `piw_macro/rtl/piw_tlp_dns_lmem_addr_virtualization.v`, `piw_tlp_dns_suspension_buffer.v`;
  `piw_core.v` still a `gen_stub.py` stub on `main`.
- KB: a2-vs-a1-summary.md, a2-architect-initiatives.md, a2-jira-landscape.md, a2-scaleup-c2c.md,
  a2-pcie-ep.md, a2-d2d.md, a2-rtl-analysis.md. Confluence 1362952569.
