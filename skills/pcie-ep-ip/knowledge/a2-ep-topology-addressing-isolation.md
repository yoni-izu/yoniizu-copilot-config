# A2 EP topology, addressing, die isolation (PCIE_EP_ISO) and remote access

- **Recorded:** 2026-11-09
- **Status:** Verified by Neo against A2 RTL/CSR (unknowns marked)
- **Tags:** a2, atlantic2, topology, x4, die stride, base address, pcie_ep_iso, psoc_misc_por, remote die, die id, strap
- **Source:** A2 RTL/CSR read by Neo (`neo-ask --chip a2`), 2026-11-09 mirror: ip.pcie.rtl@77b45f1, ip.pcie.csr@6904415, project.atlantic2.rtl@875d851, project.atlantic2.csr@2a12a536, eip_pcie@1b48d8dc, ip.psoc.rtl@2bcf174c, ip.psoc.csr@40fb726, ip.infra.rtl@8dd7f12f, ip.infra.csr@e16cbf5. Raw answers: HSI A2 PCIe plan review (session files pcie_review/a/NN_*.md).

## Findings
- **One x4 EP per die.** No bifurcation and no peer enumeration. A2 docs that say "x16" are wrong for this IP.
- **Die ID** is a 2-bit strap. SPI flash and Caliptra exist only on die0.
- **Die stride** is `0x200_0000` (address bits [26:25]).
- **Bases** (die0):
  - Management fabric: `0x0100_0000_0040_0000`.
  - Control fabric: `0x0100_0000_1040_0000`.
- **Offsets** inside the EP window:

  | Block | Offset |
  |---|---|
  | PIW | `0x8000` |
  | PEP | `0x4_0000` |
  | DBI gateway | `0x4_2000` |
  | PPW | `0x8_0000` |
  | PPW_CTRL | `0xD_0000` |

- **`PSOC_MISC_POR.PCIE_EP_ISO` (offset 0x218) resets to 1 on every die.** FW must clear it before any EP access or link-up.
  - Cite: `ip.psoc.rtl top/rtl/psoc_misc.v:1827`, `gen_rtl/top/rtl/csr_psoc_misc_por_fields.v`.
  - No EP-enable strap or eFuse was found. SKU bond-out is unknown.
- **Remote die:**
  - Each die has its own PSOC and its own ISO register.
  - PSOC routes other-die addresses to the NoC with no PSOC-side gate (`psoc_fiu.v`). PIW CfgR region enables reset to 1 (`csr_piw_ctrl_cfg_fields.v`, `piw_lbw_ctrl.v`).
  - Die0 can reach the other dies' PSOC structurally. Nothing in RTL says die0 owns all EPs.
  - **UNKNOWN:** whether remote EP CSR access is effectively open. This depends on CfgR/RR programming; it is not proven.
  - **Remote PHY FW load (plan B3.4):** this is needed. The PCIe ROM holds only the fallback/recovery PHY FW, and the production PHY FW will almost certainly differ from it. Die0 FLA_RT is expected to load the production FW into the dies 1–3 EPs over the NoC die stride. That plan depends on the UNKNOWN above. Remote link-up must fit the 100 ms budget.
