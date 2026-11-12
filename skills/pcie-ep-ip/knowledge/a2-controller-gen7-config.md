# A2 DWC controller configuration (Gen7 x4, flit, iATU, eFEC, straps)

- **Recorded:** 2026-11-09
- **Status:** Verified by Neo against A2 RTL/CSR (unknowns marked)
- **Tags:** a2, dwc 6.60a, ep16sx4_gen7, gen7, flit, iatu, efec, presets, app_ctrl, straps, link status, speed change
- **Source:** A2 RTL/CSR read by Neo (`neo-ask --chip a2`), 2026-11-09 mirror: ip.pcie.rtl@77b45f1, ip.pcie.csr@6904415, project.atlantic2.rtl@875d851, project.atlantic2.csr@2a12a536, eip_pcie@1b48d8dc, ip.psoc.rtl@2bcf174c, ip.psoc.csr@40fb726, ip.infra.rtl@8dd7f12f, ip.infra.csr@e16cbf5. Raw answers: HSI A2 PCIe plan review (session files pcie_review/a/NN_*.md).

## Findings
- **Controller:** DWC 6.60a, configuration `ep16sx4_gen7`. x4, Gen7 (128 GT/s), flit mode.
- **Functions:** 1 PF and 1 VF.
- **iATU:** 8 inbound and 8 outbound regions (not 144).
- **Equalization:**
  - Rate-4 presets are RO.
  - The 750 ns timing is a build-time parameter, so FW has nothing to program.
- **No speed-change interrupt.** FW must poll Link Status (see `speed-change-fw-notification.md`).
- **`EFEC_CTRL`** is at offset `0x120`.
- **Straps** are driven from the `APP_CTRL` CSR, not from pins.
- **Disabled capabilities:** ATS, PASID, TPH, IDE, DPC.
- **Device Serial Number (DSN)** is absent. **Data Object Exchange (DOE)** is disabled.
- **Margining:**
  - `APP_CTRL.APP_MARGINING_READY` is bit 9, reset 1.
  - `APP_CTRL.APP_MARGINING_SOFTWARE_READY` is bit 10, reset 0. FW must set it.
  - A stub ties both to 1. Which variant the build uses is unverified.
  - The MARGIN_LANE registers are in DWC, accessed over DBI.
