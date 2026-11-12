# A2 debug (LTSSM logger, dump bus, FTLB) and security (SL, CfgR gateway)

- **Recorded:** 2026-11-09
- **Status:** Verified by Neo against A2 RTL/CSR (unknowns marked)
- **Tags:** a2, ltssm logger, polled, pop, trgt_lookup, dump bus, ect, ftlb, security level, sl, elbi_sl, cfgr, gateway, sec_bitmap, terminate_en, fw_priv_sl, dbi, elbi, cii
- **Source:** A2 RTL/CSR read by Neo (`neo-ask --chip a2`), 2026-11-09 mirror: ip.pcie.rtl@77b45f1, ip.pcie.csr@6904415, project.atlantic2.rtl@875d851, project.atlantic2.csr@2a12a536, eip_pcie@1b48d8dc, ip.psoc.rtl@2bcf174c, ip.psoc.csr@40fb726, ip.infra.rtl@8dd7f12f, ip.infra.csr@e16cbf5. Raw answers: HSI A2 PCIe plan review (session files pcie_review/a/NN_*.md).

## Findings
**Debug**
- The LTSSM logger is polled only. `POP` and `TRGT_LOOKUP` were removed.
- The dump bus is selected by a pin.
- ECT is tied off.
- FTLB is new.

**Security**
- The security level (SL) is set per BAR class, plus `ELBI_SL`.
- CfgR gateway fields reset to 1: `sec_bitmap`, `terminate_en`, `FW_PRIV_SL`.

**Access paths**
- ELBI is CII-based, split into a management path and a debug path.
- The device-info SRAM is on the **CTRL** fabric, not MNG.
