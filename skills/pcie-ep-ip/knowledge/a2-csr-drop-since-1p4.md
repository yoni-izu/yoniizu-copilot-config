# A2 PCIe CSR changes since the 1p4 drop

- **Recorded:** 2026-11-09
- **Status:** Verified by Neo against A2 RTL/CSR (unknowns marked)
- **Tags:** a2, csr drop, 1p4, phy_status, ptm_events, bar_addr_mapping, pm_curnt_state, trgt_lookup, fork0, rename, pep_ctrl_aux, piw_dccm, ral
- **Source:** A2 RTL/CSR read by Neo (`neo-ask --chip a2`), 2026-11-09 mirror: ip.pcie.rtl@77b45f1, ip.pcie.csr@6904415, project.atlantic2.rtl@875d851, project.atlantic2.csr@2a12a536, eip_pcie@1b48d8dc, ip.psoc.rtl@2bcf174c, ip.psoc.csr@40fb726, ip.infra.rtl@8dd7f12f, ip.infra.csr@e16cbf5. Raw answers: HSI A2 PCIe plan review (session files pcie_review/a/NN_*.md).

## Findings
**Added**
- `PPW_CTRL.PHY_STATUS`.
- `PTM_EVENTS` bit 3.

**Removed**
- The INC `BAR_ADDR_MAPPING` registers.
- `PM_CURNT_STATE`.
- `TRGT_LOOKUP`.
- The BMU all-miss interrupts.

**Renamed or moved**
- FORK0 was renamed.
- `PEP_CTRL_AUX` offsets shifted by 0x24.
- `PIW_DCCM` moved from 0x30000 to 0x10000.

**Note:** fullchip tag `atlantic2_fullchip_1_4_0_1p4_1047` was not found in the mirror; the `a2_rev*` names are branches.
