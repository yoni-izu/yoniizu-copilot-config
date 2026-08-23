# BMU BAR-to-SoC mapping — Iridium vs Atlantic RTL delta

- **Recorded:** 2026-08-12
- **Status:** Verified from RTL (`ip.pcie.rtl` `origin/iridium` vs `origin/main`; Atlantic codename `atlantic2`, pre-RTL-freeze)
- **Tags:** bmu, bar map, bar to soc, piw_bmu, piw_bmu_lkp, piw_tlp_bar_map, bar2sl_map, compare width, bmu_cfg_compare_wdt, bmu_mem_compare_wdt, vrfid, vf, pf, virtualization, lmem address virtualization, suspension buffer, gen7, ep16sx4, ep8sx8, iridium, atlantic, iatu, mng ctrl fabric

## Scope / Question
What changed in the PCIe EP BAR-to-SoC memory-mapping mechanism (BMU) between Iridium (frozen
RTL branch `iridium`) and Atlantic (`main`), and what it means for FW.

## Findings
- **Subsystem swap:** `element_labs_ep8sx8_gen6_axi4_subsys` (Gen6, x8) → `element_labs_ep16sx4_gen7_axi4_subsys` (Gen7, 16-lane/×4). Controller/PHY move to Gen7 databook.
- **BMU compare width decoupled from address width** (`piw_pkg.v`):
  - NEW `BMU_CFG_COMPARE_WDT = 32`, `BMU_MEM_COMPARE_WDT = 64`; `BMU_ADDR_WDT` still = `PEP_ADDR_WDT` (dest).
  - `piw_bmu` ports: MEM `base/mask` now 64-bit compare; **CFG `base/mask` now 32-bit** (was `BMU_ADDR_WDT`); `dst_addr` stays full width.
  - CFG value re-expanded in HW: `*_pad = BMU_ADDR_WDT'(sel << BMU_CFG_MASK_LSB)` → a CFG region's 32-bit field = address bits **[43:12]** (4 KB gran, 44-bit match space). FW must write `(addr>>12)&0xFFFFFFFF`, NOT the raw 64-bit value.
  - MEM: 8 regions, `BMU_MEM_MASK_LSB=20` (1 MB). CFG: 32 regions (MNG+CTRL), `BMU_CFG_MASK_LSB=12` (4 KB). Region counts/LSBs unchanged vs Iridium.
- **VF/virtualization plumbing added** (Iridium was single-function — the per-fn region-select `.sel({pfid,vf_active})` was commented `// Iridium - single function - sagiz`):
  - New AXI sideband `vrfid` (`VRFID_WDT=MEM_VRFID_WDT`) threaded src→lkp→fork→dst through `piw_bmu.v` / `piw_bmu_result_handler.v`. One MEM dst path ties it off: `// Can't pass vrfid in mem.` and uses `wr_represtative_vrfid` (representative, not raw per-TLP).
  - `piw_pkg` fid struct gains `vf_active`, `vfid`(N_VF_MAX_L2), `pfid`(N_PF_L2); new `piw_intr_coalesing_t` for per-PF/VF MSI-X coalescing. `PF_NUM` still `=1` default in both (plumbing added, count may stay 1 for A1).
- **Static BAR map retired:** `piw_tlp_bar_map.v` (`input [5:0][4:0] bar2sl_map`) deleted. BAR→dest is now BMU-region-CSR + address virtualization only.
- **New companion blocks:** `piw_tlp_dns_lmem_addr_virtualization.v` (per-range compare + per-output-bit translation table, `ADDR_IN_W=27→ADDR_OUT_W=38`, carries vrfid, gated by `addr_translation_en`); `piw_tlp_dns_suspension_buffer.v` (buffer inbound AR/AW to external SRAM to suspend/replay — quiesce during reprogram/link events; author S. Zamir). `piw_iip_device_top_stub.v` added.
- **CSR RTL reorg:** `csr_pep_ctrl*`/`csr_ppw*` moved from `*/rtl/` to `*/csr/rtl/` and regenerated → FW CSR headers (`ip.pcie.sw.csr`) must be regenerated; CFG base/mask fields shrink to 32-bit.
- **FW impact (no Atlantic FW branch yet):** `el_pcie_ep_bar_map_init` = `dwc_ep_ctl_iatu_init` + `el_piw_bmu_init` (`el_piw.h`: `bmu_type{MEM,CFG}`, `bmu_region_info{base,dst,mask,enable}`). Plan: regen CSR/Gen7 headers behind a project gate; split MEM(64)/CFG(32) programming with `(addr>>12)` for CFG; validate 4 KB/1 MB alignment; re-map BMU decode IRQs; add LMEM-virt + suspension-buffer init; keep single-PF defaults benign (vrfid=0, translation off) until VFs enabled.

## Sources
- RTL: `Elmnt-Internal/ip.pcie.rtl` `git diff origin/iridium origin/main`
  - `piw_macro/rtl/piw_bmu.v`, `piw_bmu_lkp.v`, `piw_bmu_result_handler.v`, `piw_pkg.v`
  - removed `piw_macro/rtl/piw_tlp_bar_map.v`; added `piw_tlp_dns_lmem_addr_virtualization.v`, `piw_tlp_dns_suspension_buffer.v`, `piw_iip_device_top_stub.v`
  - `pep_macro/rtl/element_labs_ep16sx4_gen7_axi4_subsys_wrap.v` (was `ep8sx8_gen6`)
- FW (Iridium baseline): `ip.pcie.sw.fw.ep` — `drivers/el_pcie_ep/el_pcie_ep_bar_map.c`, `include/el_piw.h`
- Full write-up: `~/docs/PCIe_EP_Iridium_to_Atlantic_Changes.md`

## FW Change Plan (BMU BAR-to-SoC mapping, Atlantic)
Target: `ip.pcie.sw.fw.ep` — `drivers/el_pcie_ep/el_pcie_ep_bar_map.c`, `drivers/el_piw/*`,
`include/el_piw.h`, regenerated CSR headers (`ip.pcie.sw.csr`) + `dwc_ep_ctl.h`. Phased so each
step is independently testable; gate everything behind a project switch so Iridium stays
bit-identical.

- **Phase 0 — Regenerate interfaces (blocking):** regen CSR headers from Atlantic spec (BMU CFG
  base/mask fields shrink to 32-bit, MEM stays 64-bit); regen `dwc_ep_ctl.h` from the **Gen7**
  controller databook (iATU offsets/caps move); gate via `CONFIG_EL_PCIE_PROJECT_ATLANTIC` /
  manifest variant.
- **Phase 1 — BMU CFG compare width (highest correctness risk):** split region model by type —
  MEM `base/mask` = 64-bit compare (bits [63:20], 1 MB); **CFG = 32-bit compare = `(addr>>12)&0xFFFFFFFF`**
  representing bits [43:12] (HW re-shifts `<< BMU_CFG_MASK_LSB(12)`). Do NOT write the raw 64-bit
  value for CFG. Add BUILD_ASSERT: CFG windows fit low 44 bits + 4 KB aligned; MEM 1 MB aligned;
  extend `el_piw_bmu_init` malformed-map rejection. Update `el_piw_bmu_read()` dump to shift CFG
  for display.
- **Phase 2 — iATU (Gen7):** re-validate `dwc_ep_ctl_iatu_init()` against Gen7 offsets / inbound
  region count; keep two-stage contract (iATU coarse BAR window → BMU fine region).
- **Phase 3 — VF/VRFID:** for single-PF A1 bring-up keep sideband inert (VRFID source=0,
  `addr_translation_en=0`); verify the `// Can't pass vrfid in mem` MEM path yields the expected
  representative VRFID. When VFs enabled: program `2*PF_NUM` region banks per (PF, vf_active), wire
  VRFID source (PMMU/PIW), add `el_piw_lmem_virt_init()` for `piw_tlp_dns_lmem_addr_virtualization`
  (per-range compare + translation table).
- **Phase 4 — Suspension buffer:** init/enable `piw_tlp_dns_suspension_buffer` (external SRAM
  sizing, AR/AW mem i/f); define suspend/replay policy (BMU reprogram / FLR / link-down) for atomic
  map updates vs in-flight inbound TLPs.
- **Phase 5 — IRQ + bring-up order:** remap BMU decode IRQs (`bmu_no_hit`, `bmu_multiple_hit`,
  `bmu_addr_dec_*`) into `el_piw_sei_tree.c` against regenerated CSR bits; `el_pcie_ep_bar_map_init`
  order = iATU → BMU → LMEM-virt → suspension-buffer, in FLA.RT/PRIMEBOOT INIT path.
- **Phase 6 — Verify:** unit compare-width round-trip (MEM 64 / CFG 32-shifted) via
  `el_piw_bmu_read`; directed per-window MEM/CFG → correct fabric (MNG/CTRL) + SoC dest, no-hit/
  multi-hit IRQs; VF (if enabled) overlapping BARs resolve distinctly via VRFID + LMEM virt;
  regression: unchanged Iridium variant build/run.
- **Risks:** Gen7 IP delta unknown until Gen7 headers land (Phase 0); confirm VRFID-in-MEM
  representative semantics with RTL owner (S. Zamir); `main` is pre-RTL-freeze — re-diff before
  locking FW field offsets.
