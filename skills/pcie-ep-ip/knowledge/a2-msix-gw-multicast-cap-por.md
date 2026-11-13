# A2 MSI-X gateway CSRs, multicast BAR, capability POR and recovery eFuse

- **Recorded:** 2026-11-12
- **Status:** Verified from doc (MSI-X GW uarch, Arch v1.3, A2 overview). Recovery and topology are Partial.
- **Tags:** a2, msix gateway, csr_cfg_msix_en, msix_index_param_mask, sec_lvl, coalescing, multicast bar, rbar, aspm, ecrc, sris, mps, capability, ptm trace timestamp, pldm over mmio, mctp vdm, recovery, efuse, boot budget, ide

## Findings
- **MSI-X GW** has no standalone FW driver today (the EP calls it at `el_pcie_ep.c:549`, and the DTS GW addresses are in `a2_compute.dtsi`). Its CSRs:
  - `csr_cfg_msix_en`.
  - `msix_index_param_mask` (11 bits).
  - A per-vector `sec_lvl` table.
  - Coalescing is on by default. The GW supports 2048 vectors against a POR of 256.
- **Multicast BAR** (Arch v1.3): 128 KB, 256 × 512 B.
- **The capability/config POR items have no FW plan coverage:** RBAR, ASPM, ECRC, PB, SRIS, MPS 512, credit settings and MSI 32.
- **From the A2 overview:**
  - The boot budget is 87 ms.
  - PTM provides the trace timestamps.
  - PLDM over MMIO is supported; MCTP-VDM is not.
  - An eFuse selects either in-band (Gen1) or out-of-band (I2C/SPI_S/UART) recovery.
- **Skip-isolation eFuse:** `EFUSE_EXTN_SKIP_PCIE_ISOLATION_CFG` (PCIE_CFG_1 bit 30) lets the boot ROM skip `PCIE_EP_ISO` (PSOC_MISC_POR+0x218, resets to 1, one per die).
- **IDE:** the IP block is listed, but it is not supported.
- **PF/VF conflict:** the POR says 1 PF with no SR-IOV, but the RTL build has 1 PF + 1 VF with SR-IOV/ARI enabled.

## Round-2 RTL facts (Neo r06, 2026-11-12)
- **Correction: the MSI-X GW is NOT new in A2.** A1 has it with an identical register map.
- **A2 deltas from A1:**
  - The CS2 reset value changed from 1 to 0.
  - The AXI ID and VRFID fields are wider.
- **Registers in `PIW_MNG_CFG`:**

  | Register | Offset | Notes |
  |---|---|---|
  | `DBI_ADDR` | 0xc | Doorbell at 0x948 |
  | `TIMER_MAX` | 0x14 | Resets to 0x1000 |
  | `VECTOR_MASK` | 0x18 | Resets to 0x700 |
  | `SEC_LVL_CHK` | 0x1c | |
  | PF `SEC_LVL` | 0x20–0x11c | |
  | VF `SEC_LVL` | 0x120–0x21c | |
  | `MSI_GW` | 0x220–0x22c | `TRUST_MODE_EN` resets to 0 |

- **Vector table and PBA:** these live in the DWC `PF0_MSIX_CAP`, not in the GW.
- **Coalescing:** there is one coalescing timer for the whole GW.
- **No GW interrupt cause:** errors are not reported through any cause register.

## Sources
- Doc: MSI-X gateway uarch, Atlantic2 Arch v1.3, A2 overview, PCIe EP Spec Rev 2.0.0.
- RTL via Neo (PF/VF, eFuse, ISO).

## Round-2 spec-conflict verdicts (Neo r09, mirror 2026-11-09; pcie.csr@6904415, eip@1b48d8dc)
- **EP per die:** every die instantiates one `pcie_ep_cls` with no die-conditional logic (`a2_chip.v:5637`). Gating is only `PSOC_MISC_POR.PCIE_EP_ISO`, which resets to 1 and is a secure write; no eFuse or strap. Whether die0 alone or die0+1 are used is **FW/board policy**, not RTL.
- **IDE:** not present (`CX_PCIE_IDE_ENABLE` off, no DWC_pcie_ide files). FW does nothing.
- **PHY:** 1.06b (eip `phy/version`). The "1.10" in Arch Enhancements is wrong.
- **Capabilities (PF0_* reset values):**
  - ASPM advertises **L1 only** (`AS_LINK_PM_SUPT`=2); the Overview's "L0s/L1" is wrong.
  - ECRC capable; the host enables it.
  - PB cap is at 0x100 with base power 0: FW fills it via DBI or unlinks it.
  - SRIS is compiled in; FW sets the PHY_CTRL SRIS mode per board.
  - Upconfigure = 1; MPS 512 B; MSI 32 vectors, 64-bit, PVM.
  - MSI-X: 256 entries in BAR4; PBA offset 512.
  - NFUNC = 1, but **SR-IOV (1 VF), ARI, L1SS (0x210), DOE (0x3dc) and PTM caps are present**. FW must hide them by next-pointer unlink (needs `DBI_RO_WR_EN`) if the "not supported" POR is to hold.
  - LTR is off. DSN is unclear (`DSN_NFUNC`=1, no block seen).
- **Credits:** not hardwired. The `VC0_P/NP/CPL_RX_Q_CTRL_OFF` registers are writable, and their reset values differ from the Overview (P: data 750, header 47; NP: data 64, header 47; with scales). The retry buffer is fixed at build time (RBUF 627, flit 168); the "250 flits" figure is not in RTL.
- **Multicast / scale-up BAR:** there is no multicast logic in RTL, so FW owns the whole mapping. The BARs enabled at reset are BAR0 (mask 0x1fffffff) and BAR2 (mask 0x7ffffffff); routing is through `piw_bmu`, `TLP_BAR_MAP` and the iATU.
- **Boot-ROM Gen1 recovery:** there is no dedicated RTL. `APP_LTSSM_EN` resets to 0 and `DELAY_LINK_TRAINING` to 1, so the ROM must enable LTSSM itself and force Gen1 via Link Control 2 / `GEN2_CTRL_OFF`.
- **MSI-X GW:** `FEATURE_EN`, the security check and coalescing are all on (`piw_pkg.v:455-457`). With the check on and the SEC_LVL tables at 0, interrupts carrying a non-zero security level are **dropped**, so FW must program the tables or set `MSIX_GW_SEC_LVL_CHK.DIS`. `csr_cfg_msix_en` is the host's config bit, not a FW CSR.
- **OPEN CONFLICT:** r09 cites live `piw_tlp_dns_bar_addr_mapping.v` with `TLP_BAR_MAP.BAR_ADDR_MAPPING_EN` resetting to 0, while [a2-bmu-lmem.md](a2-bmu-lmem.md) and [a2-efec-appctrl-reset-traps.md](a2-efec-appctrl-reset-traps.md) call it dead RTL. Resolve before F1.4 adds its guard.
