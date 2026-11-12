# A2 PTM, AER, ECC, RAS-DES, FEI/FPM and error injection

- **Recorded:** 2026-11-09
- **Status:** Verified by Neo against A2 RTL/CSR (unknowns marked)
- **Tags:** a2, ptm, ptm_events, aer, efec, flit, viral_mask, ecc, ras-des, einj, fei, fpm, fber, vsecras, error injection
- **Source:** A2 RTL/CSR read by Neo (`neo-ask --chip a2`), 2026-11-09 mirror: ip.pcie.rtl@77b45f1, ip.pcie.csr@6904415, project.atlantic2.rtl@875d851, project.atlantic2.csr@2a12a536, eip_pcie@1b48d8dc, ip.psoc.rtl@2bcf174c, ip.psoc.csr@40fb726, ip.infra.rtl@8dd7f12f, ip.infra.csr@e16cbf5. Raw answers: HSI A2 PCIe plan review (session files pcie_review/a/NN_*.md).

## Findings
**PTM**
- PTM time is read only through CSR snapshots.
- Use `PTM_EVENTS` bit 3 as the qualifier (new in the CSR).

**AER and RAS**
- `PEP_CTRL_AUX` offsets shifted by 0x24.
- New AER causes for eFEC, flit and PM.
- `VIRAL_MASK` resets to 1, so FW must clear it.
- PEP memories have no ECC.
- PHY SRAM ECC is tied off. PHY CCM ECC status can be read only by polling `CPU_STAT_0`.

**Diagnostics available in the controller**
- RAS-DES: EINJ0–6, event counters, time-based analysis, silicon-debug (SD) status and equalization status.
- VSECRAS: the error tracer and its inject control.
- Flit error inject (FEI), flit performance (FPM) and the flit log, including FBER.
- `PEP_CTRL_CORE.CDM_RAS_DES_EINJ0_REQ` exists; its usage is unverified.

**Error injection outside DWC**
- Only interrupt-cause inject registers, in PEP and PIW: SEI, SPI, SERR, DERR, DPE, ECO, plus drain SEI and BP inject.
- Data-path injection is DWC only.
- SRAM ECC injection: unknown.
