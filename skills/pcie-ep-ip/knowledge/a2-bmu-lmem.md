# A2 BMU regions and LMEM address virtualization

- **Recorded:** 2026-11-09
- **Status:** Verified by Neo against A2 RTL/CSR (unknowns marked)
- **Tags:** a2, bmu, bar, region, pass-through, bar_addr_mapping_en, lmem, virtualization, trans_en, comp_en, bar_vec, suspension buffer, tlp_ups ns
- **Source:** A2 RTL/CSR read by Neo (`neo-ask --chip a2`), 2026-11-09 mirror: ip.pcie.rtl@77b45f1, ip.pcie.csr@6904415, project.atlantic2.rtl@875d851, project.atlantic2.csr@2a12a536, eip_pcie@1b48d8dc, ip.psoc.rtl@2bcf174c, ip.psoc.csr@40fb726, ip.infra.rtl@8dd7f12f, ip.infra.csr@e16cbf5. Raw answers: HSI A2 PCIe plan review (session files pcie_review/a/NN_*.md).

## Findings
**BMU** (`piw_bmu.v`)
- 12 regions.
- If every region is disabled, traffic passes through. Once any region is enabled, a miss is terminated.
- `BAR_ADDR_MAPPING_EN` is dead.
- The INC `BAR_ADDR_MAPPING` registers and the BMU all-miss interrupts were removed in the CSR drop after 1p4.

**LMEM** (`piw_tlp_dns_lmem_addr_virtualization.v`, `piw_main_cfg_regs.h`)
- 44 entries, each a 6-bit selector. Entry g drives output address bit 9+g, so bits 9–52 are remapped.
- Registers `TLP_LMEM_ADDR_VIRT_TRANS_TABLE_0..10` (0xABC–0xAE4) pack 4 entries each, at bits 0, 8, 16 and 24.
- A TLP is translated only if all gates pass:
  - `TRANS_EN` (0xA98 bit 0, reset 0).
  - `COMP_EN[7:4]`: 4 compare ranges on address bits 40:9, with mask/value registers at 0xA9C–0xAB8.
  - `BAR_VEC[13:8]`: per-BAR enable.
- Applies to reads and writes. Anything that fails a gate is not translated.

**Other**
- The suspension buffer is status only (`piw_tlp_dns_suspension_buffer.v`).
- `TLP_UPS.NS` (0x89C bit 3, reset 0) is a single global No-Snoop value. FW writes it only if it wants No-Snoop.
