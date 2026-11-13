# Upstream access termination on PCIe hot reset

- **Updated:** 2026-11-10
- **Status:** A1 behavior verified through Neo against the PCIe RTL and CSR
  revisions pinned by fullchip tag `3_0_11_3p0_1389`; FW checked in the
  `iridium/two` workspace. A2 behavior verified only against the newer Neo
  mirror revision listed below, not against the workspace's A2 fullchip tag.
- **Tags:** upstream termination, ups_term, tlp_upstream_term, smlh_req_rst,
  hot reset, bus master enable, cfg_bus_master_en, viral, piw_main, fw release

## Bottom line

At A1 reset defaults, PIW **automatically terminates upstream
(device-to-host) AXI requests during hot reset**. The hot-reset condition
clears automatically when the controller's active-low `smlh_req_rst_not`
returns high; there is no latched PIW hot-reset termination that FW must
explicitly release. However, the independent Bus Master Enable (BME)
condition can keep termination active after the reset condition clears.

This corrects the earlier claim that the hot-reset trigger was probably
disabled by default and that FW had to release a latch.

## A1 behavior at the pinned fullchip tag

- `PIW_MAIN_CFG_TLP_UPSTREAM_TERM` is at PIW_MAIN + `0x148` in the
  `3_0_11_3p0_1389` generated headers. Reset value `0x3` enables the
  BME-low and hot-reset sources and disables the viral source.
- The field names contain `MASK`, but **1 enables** the associated
  termination condition and 0 disables it:
  - Bit 0 `CFG_BUS_MASTER_MASK`: terminate while BME is low.
  - Bit 1 `SMLH_REQ_RST_MASK`: terminate while `smlh_req_rst_not` is low.
  - Bit 2 `VIRAL_MASK`: terminate while viral is asserted.
- PIW combines the enabled conditions and routes upstream AXI requests to a
  local terminator rather than the PCIe path while termination is active.
  The three inputs are retimed before use.
- There is no sticky hot-reset termination latch in this logic. When the
  reset request is deasserted, its contribution clears automatically.
  **Overall termination ends only when every enabled condition is clear**:
  if BME remains low, the host must enable bus mastering before the BME
  condition clears. Software can disable a source by clearing its mask, but
  that is not required for normal release of the reset condition.
- Clearing a termination status or interrupt does not release an active
  condition. The interrupt is associated with a request accepted under an
  enabled termination condition, not merely with entering reset.

## Firmware and specification

The checked post-reset handler `handle_post_hot_reset()` re-enables LTSSM
when configured to delay link training and sets configuration readiness; it
does not explicitly clear PIW upstream termination. This is consistent with
the automatic release above. A source-name search found no
`TLP_UPSTREAM_TERM` writes in the searched FW sources; it does not exclude
numeric accesses or code outside the searched paths.

EP Spec Rev 2.0.0, "Upstream Access Termination", says FW must deassert the
hot-reset termination. That statement conflicts with the traced PIW RTL for
the revisions described here; do not infer a sticky PIW latch or a mandatory
FW clear from the spec alone. Controller-internal assertion/deassertion
timing and exact fullchip integration were not verified in the RTL trace.

The DWC controller's AXI-bridge flushing/reset flow is a separate mechanism.
The controller databook describes automatic flushing for segmented-buffer
RX configurations, but its exact configuration and implementation were not
verified for this build.

## Version and offset caveats

- The A1 fullchip tag's CSR manifest pins `ip.pcie.csr` at
  `6a8c52bd79f4d368f8819c72ea6ee5902eb854cd`; its VLSI manifest pins
  `ip.pcie.rtl` at `3afb411478d16490c0dcbada41bf040d19d0688f`.
  The generated A1 header matches the pinned CSR's `0x148` offset and `0x3`
  reset, although the header build provenance and fullchip integration were
  not independently verified.
- The local A2 `1_0_7_1p0_503` generated header instead places this register
  at `0x14c`, also with reset value `0x3`. Do **not** reuse the A1 offset.
- Neo's newer A1 and A2 mirrors place it at `0x174`; the map moved as
  earlier registers were added. Neo found the same active-high masks and
  automatic release in those newer PIW revisions. The A2 workspace tag was
  not mapped to its exact RTL revision in this investigation.

## Sources

- Neo, A1 tag-pinned `ip.pcie.rtl` commit
  `3afb411478d16490c0dcbada41bf040d19d0688f`:
  `piw_macro/rtl/piw_ups_term.v:35-119` and
  `piw_macro/rtl/piw_tlp.v:1916-1929`.
- Neo, A1 tag-pinned `ip.pcie.csr` commit
  `6a8c52bd79f4d368f8819c72ea6ee5902eb854cd`:
  `yml/piw_macro/PIW_TLP_CFG.yml` and
  `scripts/headers/piw_main_cfg_regs.h`.
- Neo, A2 newer mirror `ip.pcie.rtl` commit
  `77b45f10863c5291fa43729345ff6e9f8fad6805`:
  `piw_macro/rtl/piw_ups_term.v:38-133`. This is not established as the
  RTL revision used by the local A2 fullchip tag.
- FW: `ip/pcie/ep/drivers/snps_ep_ctl/dwc_ep_ctl.c:1650-1725,1748-1794`.
- Generated headers:
  `build_artifacts/include/ips/pcie/v1/3_0_11_3p0_1389/prototype/piw_main_cfg_regs.h:1195`
  and `piw_main_cfg_regs_attr.h:591`;
  `build_artifacts/include/ips/pcie/v2/1_0_7_1p0_503/prototype/piw_main_cfg_regs.h:1197`
  and `piw_main_cfg_regs_attr.h:660`.
- Revalidation source: `project.atlantic2.hwarch.specs:specs/pcie-ep-specification-Rev2.0.0/pcie-ep-specification.md`; historical secondary document locators follow,
  "Upstream Access Termination"; Synopsys EP controller databook pp. 107-109.

## Is HW-vs-FW release configurable? (checked 2026-11, Neo A2 @ ip.pcie.rtl 77b45f10 / ip.pcie.csr 6904415b)
- **No.** `TLP_UPSTREAM_TERM` holds only 3 enable masks (b0 BME, b1 hot-reset, b2 viral; reset 0x3). No latch/sticky,
  FW-release, or auto-release mode field exists (piw_ups_term.v:51-132, piw_tlp.v:1964-2014). Mask=1 → terminate while
  condition holds, HW auto-release; mask=0 → that cause never terminates. A1 tag rev 3afb4114 same (earlier Neo answer).
  A1 mirror-tip diff [unverified — Neo hinted "RTL differs between revs" then VM dropped].
- Spec Rev 2.0.0 §"Upstream Access Termination" ("hot reset de-assertion must be done by FW") does NOT match RTL.
- Not to confuse with: `PEP_CTRL_AUX_DS_TERM_CTRL` (0x34, rst 0x55) — PF/VF SW vs UNCERR-mask *downstream* termination;
  and `PEP_CTRL_CORE_LINK_DN_CTRL` (0x40, rst 0x202: DELAY_LINK_DN_RST=0, DELAY_LINK_TRAINING=1) — the hot-reset
  *reset-delay* (HW clears app_ltssm_enable, FW re-sets it). FW `dwc_ep_ctl_linkup_impl` (dwc_ep_ctl.c:1806-1830)
  overwrites both delay bits from DT `delay-link-dn-rst`/`delay-link-training`; no production DT sets them → both 0,
  so hot-reset handlers (dwc_ep_ctl.c:1681-1722) are no-ops for the delay.
- FW never writes TLP_UPSTREAM_TERM, never reads UPS_TERM_STATUS; PIW SPI mask resets 0x1ffff (all masked) and only
  SV/test code (el_piw_sv.c:609) unmasks it; UPS_TERM cause bits have no binding (el_piw_spi_tree.c:70-78).
