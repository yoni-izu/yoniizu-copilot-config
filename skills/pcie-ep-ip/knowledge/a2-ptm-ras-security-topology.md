# A2 PCIe EP: PTM, RAS, security programming, topology, PSOC hooks

- **Recorded:** 2026-11-12
- **Status:** Verified from RTL via Neo (r07/r08). Two offset conflicts are open and flagged below.
- **Tags:** a2, ptm, ecc, viral, margining, ltssm logger, bar2sl, elbi_sl, cfgr, rr, tee, topology, single ep, psoc reset mask, drain, efec interrupt, tlp generator, serializer, hbw, bmu, flr, vrfid

- **Companion note:** [a2-ptm-ras-diagnostics.md](a2-ptm-ras-diagnostics.md) covers AER, RAS-DES and error injection.

## Topology
- **A2 has a single `pcie_ep_cls` with an x4 PHY.**
  - The second PCIe reset bit is spare (`a2_chip.v:5637`).
  - The 4 DTS EP nodes (`a2_compute.dtsi:815/849/883/917`) are per die, not multiple EPs per die.
  - A multi-EP-per-die DT is not needed.

## PTM
- PTM is requester-only; its registers are at AUX 0x124–0x144.
- A manual update is dropped unless `PTM_STATUS.TRIGGER_ALLOWED` = 1.
- Several Gen7 PTM ports are tied or open: the manual-update input, and the current-rate and PTM-status outputs.

## RAS
- **ECC:**
  - PEP has no CSR-memory ECC.
  - Flit ECC raises SEI when uncorrectable and SPI when correctable.
- **Viral:**
  - PIW `MAIN_CFG.VIRAL_MASK` resets to 1; `UPSTREAM_TERM.VIRAL_MASK` resets to 0.
  - Viral goes to PSOC from PIW, FTLB and PDMA.
- **Margining (APP_CTRL):**
  - Bit 9 is `READY` (resets to 1).
  - Bit 10 is `SW_READY` (resets to 0).
  - Bits [12:11] are `TBA_CTRL`.
- **LTSSM logger:**
  - 256 entries. Enable resets to 1, halt-on-full to 0, threshold to 15.
  - **Offset conflict:** r07 says CORE 0x84–0xa4, r08 says CORE 0x5C–0x7C, and 0x5c is also MSG_RX. Verify the address in the CSR header before coding.
- **eFEC:**
  - `EFEC_CTRL` is at AUX 0x120.
  - The interrupt group is at 0x3C0, and its enable resets to 0, so FW must enable it.
- **Debug:**
  - FTLB interrupts are on PMMU0 lines 992–1072.
  - The observation bus is at PIW_MAIN_CFG 0xE8.
  - `cxpl_debug_info` has no consumer, and the SE-side ECT is tied off.
- **Suspension buffer:** status only (PIW_TLP_CFG 0x978).

## Security: FW must program
- **BAR2SL:**
  - 5 bits per BAR, resetting to 0.
  - **Address conflict:** r07 says 0xa84/0xa88, r08 says TLP 0x914.
- **SL resets:**
  - `ELBI_SL` is at 0xe4 and resets to 0.
  - `CFG_ISO_SW_MAN_SL` resets to 0x1F.
  - `RANGE_REG.FW_PRIV_SL` resets to 0x1f, with the terminate enables at 1.
- **CfgR gateway:**
  - The regions reset to 1 (commit 693ce54).
  - They have no effect until `CFGR_RR_ACTIVE_EN` is set; it resets to 0.
- **TEE:** `TEE_CONFIG`, `TEE_PF` and `TEE_VF` FSM registers exist.
- **Not present:** DOE. DSN is not visible in the CSRs.

## Controller and init reset values
- `APP_LTSSM_EN` is at CORE 0x18 and resets to 0. HW auto-clears it, so FW must re-arm it.
- CORE APP_CTRL is at 0x1C; config-retry and DRS both reset to 0.
- `LANE_FLIP_EN` (0x24) resets to 0.
- Delayed link training resets to 1.
- L1-substate disable resets to 1.
- `PHY_STATUS` (0x40) is new; it holds PLL0/1 lock and refclk detect.
- **BMU:**
  - 12+12 MEM regions and 32+32 CFG regions; enables at 0x718–0x724, `TLP_BMU_CONF` at 0x728.
  - `MEM_MULT_HIT_ALLOWED` resets to 1; `CFG_MULT_HIT_ALLOWED` resets to 0.
  - No-hit and multi-hit are SEI causes 25 and 26.
- **LMEM:** `LMEM_ADDR_VIRT` is at 0x928, with an 11-entry table at 0x94C.
- `PIW_SRAM_INFO` is 8 KB.
- `WHO_AM_I_FIU` is at PIW_MAIN_CFG 0xF0.
- **CII_CTRL:**
  - ELBI-indirect resets to 1; CII resets to 0.
  - `LBC_HALT_CLR` is new.
  - CII is not VF-aware (`pep_cic_handler.v:113` TODO).
- **FLR:**
  - HW mode is the default.
  - The watchdog threshold is 4980736 (~99.6 ms).
  - `WD_CNT_CLR` is bit 4.
- `LINK_DN_CTRL` count resets to 64.

## Blocks the FW plan may miss
- **TLP generator:** PIW_TLP_CFG 0x72C–0x7FC; `TLP_UPS_GEN_CFG` at 0x730; it has done and error causes.
- **Serializer:** regions and bypass at 0x804–0x914.
- **PIW_MEM_CFG:** HBW aggregation and flag-delay registers.
- **Range registers:** fork and ELBI ranges.
- **PPW CCM:** fast-access registers at 0x44–0x58.
- **Extra AUX causes:** CII, PM_L1, ASPM, RAS tracer, flit latency/log MSI.
- **AUX `CFG_INFO`:** BAR read-back at 0x44–0x94.
- **`INT_SIG` (0xA0):** latency and trigger bits.
- **Completion-timeout tags:** CORE 0x44–0x54. The `TRGT_LOOKUP` registers were removed.

## PSOC-side hooks
- **Reset masks:**
  - The PSOC hot-reset and FLR request masks, including the D2D variants, default to masked (`boot_pkg.v:158-169`). PSOC FW must unmask them.
  - A reboot config bit also exists: FLR resets to 1, hot reset to 0.
- **Drain:** three drain back-pressure interrupts go to PSOC and feed a drain reset type (`psoc_boot_rst_mng_top.v`).
- **No cross-die path:** there is no HW cross-die (D2D) hot-reset path.
- **VRFID:** tied or open on the PEP master (`pep_axi_mstr.v` TODO).

## Sources
- RTL/CSR via Neo, round-2 questions r06/r07/r08 (2026-11-12): ip.pcie.rtl, ip.pcie.csr, project.atlantic2.rtl, eip_pcie.
