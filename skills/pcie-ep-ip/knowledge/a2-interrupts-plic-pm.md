# A2 interrupt lines (PLIC), cause clearing, PM and PME_TO_ACK

- **Recorded:** 2026-11-09
- **Status:** Verified by Neo against A2 RTL/CSR (unknowns marked)
- **Tags:** a2, plic, interrupt, derr, dpe, eco, sei, serr, spi, bottom-up clear, pm, d3cold, pme_to_ack, radm_pm_turnoff, msi-x
- **Source:** A2 RTL/CSR read by Neo (`neo-ask --chip a2`), 2026-11-09 mirror: ip.pcie.rtl@77b45f1, ip.pcie.csr@6904415, project.atlantic2.rtl@875d851, project.atlantic2.csr@2a12a536, eip_pcie@1b48d8dc, ip.psoc.rtl@2bcf174c, ip.psoc.csr@40fb726, ip.infra.rtl@8dd7f12f, ip.infra.csr@e16cbf5. Raw answers: HSI A2 PCIe plan review (session files pcie_review/a/NN_*.md).

## Findings
**PLIC lines**

| Group | PLIC line |
|---|---|
| DERR | 192 |
| DPE | 208 |
| ECO | 224 |
| SEI | 240 |
| SERR | 256 |
| SPI | 272 |

Cite: `puc_intr_aggr.v:123`, `psoc_cpu_wrap.v:1415`.

**Clearing:** clear causes bottom-up, leaf first, then the aggregate.

**Power management**
- No D3cold. `PM_CURNT_STATE` was removed.
- **PME_TO_ACK:**
  - The real build path is pep_wrap → subsys, and `APP_CTRL.APP_READY_XMT_PME_TO_ACK` takes effect. The tied-low wrapper is not used.
  - FW flow: take the `INTR_SPI.RADM_PM_TURNOFF` interrupt, then set the ack.

**Mailbox:** message only.
