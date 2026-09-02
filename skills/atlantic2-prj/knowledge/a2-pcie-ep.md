# A2 (Atlantic2) — PCIe EP Sub-System (Gen7)

- **Recorded:** 2026-08-30
- **Status:** Verified from spec (Overview + PCIe EP Spec Rev 2.0.0)
- **A2-vs-A1 delta:** **PCIe Gen7** (vs Gen6 on A1) — 128 GT/s/lane PAM4; Enhanced custom FEC +
  increased voltage swing; new integration features (PTM, per-engine SL, INC BAR, PME_TO_Ack app
  control).
- **RTL branch/tag:** RTL diff TODO. n/a for spec.
- **Tags:** pcie, pcie ep, gen7, pam4, flit, fec, piw, uc, elbi, bmu, ide, tee, ptm, msi-x,
  reset isolation, single ep, host interface

## Scope / Question
PCIe EP changes for A2 vs A1: the Gen7 upgrade and the integration-feature surface.

## Findings (A2)
- **Gen7 ×4, 64 GB/s per direction** (A2 Overview). **PAM4** signaling (Gen6/7), **256-byte FLIT**
  mode (236B TLP + 6B DLP + 6B FEC, 3 interleave groups × 2 FEC bytes, ~92% TLP efficiency).
- **Signal integrity**: **Enhanced (custom) FEC** + **increased voltage swing**; credits
  fine-tuned for **1 µs round-trip latency**.
- **Single PCIe EP** used for the host link, **shared among the 4 dies** (A2 Overview para 94).
- **EP subsystem** = PCIe PHY + PCIe EP controller IP + PCIe wrapper (PIW). The EP macro has its
  own **micro-controller** (executes from ICCM, connected to all EP status/control IFs), which:
  answers FLR / hot reset, can **extend the PCIe capability linked-list in SW**, and implements a
  **generic mailbox mapped to DCCM** to talk to **PSOC** FW. All interrupts go to both PSOC and
  the PCIe uC.
- **Capability superset** (Rev2 spec H2 list): ACS, ASPM, AER, AtomicOps, ARI, ATS, BAR, DMWr,
  DSN, DOE, DPA, ECRC, **FEC**, **FLIT**, FLR, **IDE**, IDO, **L0p**, L2/L3 power-down, Lane
  Reversal/Resizing, LCRC, LN, LTR, MSI, **MSI-X**, OBFF, **PAM4**, PASID, PCRC, PRS, ordering
  rules, PWRBRK, **PTM**, RBAR, RN, RO, **SR-IOV**, **TEE**, TLP Prefix, TPH, UIO, VPD.
- **Integration features** (Rev2 spec "PCIe Integration Features" + "Controller Enhancements"):
  CIC (Config Intercept Controller), DBI, Data-Path & SRAM-ECC protection, iATU, Link-Down/Hot
  Reset, PCIe DMA, Downstream Access **Aggregation/Termination**, Upstream Access Termination,
  Error Response Masking, Downstream Message, Traffic Serialization, **Cache Line Split**, **BMU**
  (BAR Mapping Unit), **ELBI** (External Local Bus Interface), Perf Monitors, **LTSSM Logger**,
  Indirect/Partial Transaction gen, Traffic Generator, Error Counters, PF↔VF comm, TEE, Device
  Info, FW Mailbox, IDE, **Reset Isolation**, Instruction Cache.

## A2 vs A1 (Iridium) delta
- **Gen6 → Gen7** is the headline (128 GT/s/lane). A1/Iridium PCIe EP was Gen6-class; A2 adds
  Gen7 data rate with enhanced custom FEC + increased voltage swing.
- Architect A2 PCIe initiatives (Confluence): **Add PTM** (needs SW), **PME_TO_Ack app control**,
  **dedicated SL config per engine/function**, **INC BAR mapping**, **LMEM Address
  Virtualization**. (see a2-architect-initiatives.md)
- The 44 A2 HSI PCIe **Attributes** (Palladium enablement) target these integration features:
  ELBI, FLR, MSI-X coalescing, reset isolation, secure BAR, error counters, downstream access
  termination, BMU, FW mailbox, LTSSM logger, etc. (see a2-jira-landscape.md)

## Micro-controller / CPU core (corrected 2026-08-30, per PCIe FW owner)
There are **three distinct things** — do not conflate them:
1. **PCIe uC (PCIe EP spec §4.1)** — a dedicated PCIe integration micro-controller *option* in
   the EP macro (own ICCM/DCCM + boot ROM, ELBI unknown-config handling, FLR/hot-reset, PSOC
   mailbox). RTL = the **PIW core** (`piw_macro/rtl/piw_core.v`, `babel_piw_rom.yml`,
   `piw_iip_device_top` → `async_iip_core_*` signals). **STATUS: NOT POPULATED / NO FW.**
   `piw_core.v` is a **`gen_stub.py` stub on BOTH `iridium` and `main`**, and its
   `async_piw2ppw_cpu_run_req` is tied off. **The PCIe FW owner confirms there is no PCIe-uC FW
   handling** — this uC is a HW flexibility/bug-workaround *option* the design does not
   instantiate as a running CPU. True in A1 and A2.
2. **PCIe integration = HARDWARE handlers, not a uC.** `pcie_iip_device_wrap` implements FLR,
   hot-reset, MSI, LTSSM logging, CIC, PB, etc. as dedicated HW modules (`pep_hot_rst_handler`,
   `pep_flr*`, `pep_msi_handler`, `ltssm_logger`, `pep_cic_handler`, `pep_pb_handler`). This is
   the spec's "hardware approach", NOT the optional "firmware approach".
3. **PCIe FW runs on the PSOC MuC (ARM Cortex-M7)** — the drivers in `ip.pcie.sw.fw.ep` run on
   the embedded ARM target (and an x86 host target), reaching the EP over the fabric / **ELBI**
   (`el_pcie_safe_read32/write32` = cfg-space → ELBI). No driver targets a PCIe-local core.
4. **PHY embedded CPU (separate!)** — the Synopsys **Gen7 PHY** (`1.10a_pre1`,
   `dwc_pcie7phy_upcs_x4_ns_x4_x4_pipe_wrap`) has its own FW CPU; PHY FW is streamed via the
   **PIW SRAM → PHY ICCM/DCCM** path (`mcu_iccm`/`mcu_dccm`, `CPU_RUN_REQ` in `ppw_ctrl`). This
   runs PHY firmware only, not integration FW. In A2: single-x4 macro (Gen6 was dual x4/x8),
   ICCM/DCCM now inside the PHY IP, `PPW_FW_ICCM_SRAM_DPT=32768`; CPU-run-request handshake marked
   "Vestigial"/tied-off in this pre-release drop.

**Bottom line for "new uC in A2 PCIe?": NO.** No new/added FW-bearing micro-controller. The §4.1
PCIe uC exists in the spec but is stubbed/FW-less (A1 and A2); integration is HW; PCIe FW lives on
the PSOC MuC; only the PHY has a FW-running CPU (a new Gen7 PHY IP version, not an added core).
(SU/DMP mirrors this with the same DWC PCIe7 PHY in **N3/3nm**.)

## Sources
- Doc: `~/docs/A2/Atlantic2 Overview.docx` (PCIe 198-212, feature set 130-131, sharing 94).
- Doc: `~/docs/PCIe EP/PCIe EP Specification Rev 2.0.0.docx` — Overview (209-227), FEC (378-393),
  FLIT (385-397), PAM4 (567-571), capability + integration-feature H2 lists.
- Also: `~/docs/PCIe_EP_Iridium_to_Atlantic_Changes.md` (prior Iridium→Atlantic change notes).
- **RTL (confirmed)**: `ip.pcie.rtl` `iridium...main` — Gen7 macro `element_labs_ep16sx4_gen7_axi4_subsys`,
  PIW rework (RR integ, suspension buffer, vrfid, 11-bit id), PMMU integration, ETR/SE-ATB. See
  a2-rtl-analysis.md.
