# A2 (Atlantic2) — RTL Analysis (iridium → main delta)

- **Recorded:** 2026-08-30
- **Status:** Verified from RTL (GitHub compare `iridium...main` per IP repo, via `gh api`)
- **A2-vs-A1 delta:** This is the **ground-truth** delta (what actually changed in RTL), vs what
  the docs/plan say. Confirms Gen7 (PCIe EP + SU dual-mode), EFEC, C2C aggregators, 3nm PHY on the
  SU PHY, and GPU/INC fabric channels.
- **RTL branch/tag:** **A1/Iridium = branch `iridium`; A2/Atlantic2 = branch `main`** (pre-freeze).
  Delta = `gh api repos/Elmnt-Internal/ip.<ip>.rtl/compare/iridium...main`.
- **Tags:** rtl, iridium, main, gen7, efec, c2c, aggregator, dual-mode, dm, dmp, n3 phy, piw,
  pep, ppw, pmmu, ucie, gpu, inc, vsp, aligner, cce, ltssm logger, compare

## How to reproduce
```bash
gh api repos/Elmnt-Internal/ip.<ip>.rtl/compare/iridium...main --jq '{ahead:.ahead_by, files:(.files|length)}'
gh api --paginate repos/Elmnt-Internal/ip.<ip>.rtl/compare/iridium...main --jq '.commits[].commit.message'
gh api --paginate repos/Elmnt-Internal/ip.<ip>.rtl/compare/iridium...main --jq '.files[].filename'
```
Delta magnitude (2026-08-30): pcie 241 commits/225 files · se 300/185 · d2d 78/154 · noc 535/300+
· psoc 544/300+ · infra 529/300+ (300 = GitHub compare file-page cap).

## Findings (A2) — per IP

### PCIe EP — `ip.pcie.rtl` (241 commits)
Change areas: `piw_macro/csr` (58), `piw_macro/rtl` (37), `pep_macro/rtl` (31), `pep_macro/misc`
(23), `ppw_macro/rtl` (13), `cluster/rtl`, `repo_sanity/atlantic2`.
- **Gen7 EP macro (confirmed at RTL)**: new top-level wrap **`element_labs_ep16sx4_gen7_axi4_subsys`**
  ("pep_macro: top level (wrap) for element_labs_ep16sx4_gen7_axi4_subsys") → EP, 16-slice, **×4,
  Gen7**. "PPW,PEP - A2 initial".
- **PIW (PCIe Integration Wrapper) rework** (the biggest area): RR (Range Register) integration,
  **suspension buffer**, **vrfid** (virtual function/routing id) + "trgt connect vrfid", **ID
  widened to 11-bit**, "tuc as mstr on ctrl+mng fabric", stubs.
- **`pcie_ep_top` integration of PIW + PPW + PMMU** (PCIe MMU integrated into the EP top).
- **Remove C2C7 from pcie_ep_cls**; **ETR integration in FTLB** (remove ETR MEM i/f, add **SE ATB
  i/f** — trace routing). "align to a2".

### Scale-Up / SE (C2C) — `ip.se.rtl` (300 commits)
Change areas: **`c2c_macro/rtl` (79)** dominant, `dm_macro/rtl` (22), `dmp_macro/rtl` (11),
`c2c_macro/csr` (15), `cluster/rtl`.
- **C2C aggregators (confirmed at RTL)**: **C2C Read Aggregator**, **C2C Write Aggregator +
  memories** — the write/read aggregation buffers from the arch/C2C spec.
- **Dual-Mode (DM) PCIe Gen7 controller**: **`element_labs_dm16sx2_gen7_axi4_subsys`** (DM,
  16-slice, **×2, Gen7**); Synopsys **DWC DM 6.60a-lca01**; "DM - A2 initial"; **DM - EFEC integ
  init** (Enhanced FEC integration — confirms the "enhanced custom FEC"); Flat Controller integ;
  **dm_ltssm_logger**; `dm_pcie_iip_device_wrap`; "hot rst + perst_n remove".
- **DMP (Dual-Mode PHY)**: **n3 PHY initial integ** (+ srams, sva) → the **scale-up PCIe7 PHY is
  implemented in N3/3nm**; **DWC_PCIE7PHY_PMD_X4**; **bifurcation support** ("DMP,DM - initial
  bifurcation support, whoami"); **EL_DMP define support for x2**; address decode; CSR cleanup.

### D2D — `ip.d2d.rtl` (78 commits)
Change areas: `d2d/rtl` (95), `d2d/misc` (25), mid/side cls, `ucie_ip_links` (NS/EW + emul).
- Mostly **"align to a2" / "align to project a2_rev0p5 branch" / "more a2 alignments"** +
  **"update ucie links"**. Data-path refactors: embed packed unions
  (`el_mem_addr_t`/`el_mem_awuser_t`/`el_mem_wstrb_t`), checksum accessor fixes.
- **PHY macros currently stubbed**: "temp stub d2d phy macros", "redefine phys as stubs", "temp
  mask d2d phy from sanity", "add EL_USE_HARD_MACRO to d2d_side_phy_macro" → the new D2D PHY(s)
  (5nm + 3nm) are being integrated as hard-macro stubs (not yet dropped in). "remove CCE trace
  logic from d2d mid cntrl macros". Confirms: D2D **logic** delta is modest / alignment-heavy;
  the real change is the **PHY macro swap** (matches a2-d2d.md — spec unchanged, PHY is the delta).

### NoC — `ip.noc.rtl` (535 commits)
Change areas: **`vsp` (114)**, **`algn` (81)**, `d2d_fabric` (34), `d2d_channel` (18), mid/side cls.
- **GPU/INC fabric channels (confirmed at RTL)**: **`AXI_UC_GPU` / `AXI_UC_INC`** uncached
  channels added (noc_alg_pkg struct fields, alg_cce.v) — matches the new INC + remote-GPU address
  spaces.
- **VSP (vector/VME datapath)**: **VXM dual vcore-group (16G / 32G) feature onto main** — the
  16/32-VCore group support; own CSR block (`vsp_alg_mng_csr_wrap`) since 2026-08-10.
- **Aligner + CCE tables (`algn`)**: CCE-tables assigned addr/user, **CG0 ranks table** (jira
  IPALGRRTL-45), group-mode propagation, CCE credits-counters per chip, **distribute for
  multicast** (IPALGRRTL-12), burst support for CCE (IPALGRRTL-69/103), addressing widened to 64b
  from VC. **CCE-desc detect now uses `addr.noc.op_type`** (was `addr.fmt.fmt1.collective_op`) —
  matches the NoC-spec CCE-descriptor rev history.
- **MRTR** (Middle/debug Router): module_id 3-valued (MRTR_W=0, MRTR_E=1, MRTR_DUP=2); driven by
  CSR instead of tie-offs.

## A2 vs A1 (Iridium) delta — RTL confirms the plan
- **Gen7 is real**: `element_labs_ep16sx4_gen7` (PCIe EP) + `element_labs_dm16sx2_gen7` (SU DM).
- **SU PCIe-compliant mode is real**: DM dual-mode controller (DWC 6.60a), C2C read/write
  aggregators, LTSSM logger, bifurcation.
- **EFEC (enhanced FEC) is real**: "DM - EFEC integ".
- **3nm PHY is real** and appears on the **SU PHY (DMP n3 + DWC_PCIE7PHY)**; the **D2D** PHYs are
  present as **hard-macro stubs** (swap in progress) — so D2D's A2 delta is dominated by the PHY
  macro, consistent with the unchanged D2D spec.
- **GPU/INC integration is real at the fabric**: NoC `AXI_UC_GPU`/`AXI_UC_INC` channels + Aligner
  CCE-table/multicast/ranks work.

## Open follow-ups
- `ip.psoc.rtl` (544) and `ip.infra.rtl` (529) not yet mined (RAS/manageability/infra — less
  HSI-central; do next for a full picture).
- File pages cap at 300 for noc/psoc/infra — use `--paginate` on `.files` or per-directory
  compare to enumerate all.
- For any specific module, pull the actual diff:
  `gh api repos/Elmnt-Internal/ip.<ip>.rtl/compare/iridium...main --jq '.files[]|select(.filename|test("<re>")).patch'`.

## Sources
- GitHub compare API `iridium...main` on `ip.pcie.rtl`, `ip.se.rtl`, `ip.d2d.rtl`, `ip.noc.rtl`
  (commit messages + changed-file lists), 2026-08-30, account yoni-izu.
