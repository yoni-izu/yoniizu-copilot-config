# A2 PHY CPU start (PHY0_CPU_CTRL) and PHY-FW copy FSM

- **Recorded:** 2026-11-09
- **Status:** Verified by Neo against A2 RTL/CSR (unknowns marked)
- **Tags:** a2, phy cpu, phy0_cpu_ctrl, run req, halt, ack, copy fsm, iccm, dccm, ccm fast access, boot_from_rom, cont_on_prst_assert, phy 1.06b, sw sram copy, fw_csr_8
- **Source:** A2 RTL/CSR read by Neo (`neo-ask --chip a2`), 2026-11-09 mirror: ip.pcie.rtl@77b45f1, ip.pcie.csr@6904415, project.atlantic2.rtl@875d851, project.atlantic2.csr@2a12a536, eip_pcie@1b48d8dc, ip.psoc.rtl@2bcf174c, ip.psoc.csr@40fb726, ip.infra.rtl@8dd7f12f, ip.infra.csr@e16cbf5. Raw answers: HSI A2 PCIe plan review (session files pcie_review/a/NN_*.md).

## Findings
**PHY FW source:** the ROM image is the fallback for recovery only; production PHY FW is loaded by FW, including into remote dies (see a2-ep-topology-addressing-isolation.md).

**PHY version:** 1.06b. The EP-side report that says 1.10a_pre1 is stale metadata.

**Starting the PHY CPU**
- Use the PHY's own PCS register `PHY0_CPU_CTRL` (offset 0xCC in the `PHY_UPCS_CTRL` window). Cite: `el_dwc_pcie7phy_upcs_x4_ns_pcs_phy_reg_regs.h:168`, `dwc_pcie7phy_upcs_x4_ns_x4_x4_pipe_apb.v`.

  | Bit | Function |
  |---|---|
  | 0 | ECC enable |
  | 1 | Halt request |
  | 2 | Run request |
  | 3 | Bypass of the internal ref_clk_req FSM |

- Acknowledges are in `PHY0_PMA_PWR_N_CPU_ACK_STS` (0xF8): bit 16 = halt ack, bit 18 = run ack.
- Sequence: set run-req, wait for the run ack, then clear run-req.
- The absolute address is unverified.
- **PIW `cpu_run_req` is dead.** It is unconnected at `piw_macro.v:1178`, marked "TODO remove". Do not use the A1 PPW_CTRL CPU run path on A2.
- **Unverified:** whether the internal ref_clk_req FSM starts the CPU on its own.
- **PHY reset:** no FW-writable PHY reset was found. PHY reset follows `phy_perst_n` from pep_wrap (`pep_wrap.v:1454`).

**PHY boot-done and error status**
- `CPU_STAT_0` (PMD 0xC004) has **no** boot-done bit. It holds the watchdog, sleep, ICCM/DCCM ECC and exception status.
- Boot-done candidates:
  - `FW_CSR_8.FW_STATIC_CFG_DONE` (0xC028, bit 2).
  - `CM_FW_CSR_0.CM_FW_INIT_DONE` (0xE428, bit 0).
- Error check: `FW_CSR_0.FW_ERROR` (0xC008, bit 9).
- Unverified: that the PHY FW is what sets these done bits.

**Copy FSM** (`fw_sram_copy_handler.v`; same design as A1)
- Triggers on PERST deassert once `INIT.DONE` is set, or when `BOOT_FROM_ROM` is set (reset 0).
- Order:
  1. Read-modify-write of `CCM_FAST_ACCESS_SEL` and the CPU ECC enable, through the PIW CSRs `PPW_CCM_FAST_ACCESS_*` and `PPW_CCM_CPU_ECC_EN_*`.
  2. Copy DCCM.
  3. Copy ICCM.
- It never clears fast-access afterwards.
- It does not need the PHY to be in reset.
- It aborts if PERST reasserts, unless `CONT_ON_PRST_ASSERT` is set.
- All other PHY configuration is left to FW.
- The copy base address is PARAMETER-reset, so FW must not hard-code it.
- There are 4 copy-error interrupts.
- A SW copy alternative exists through the `PPW_SW_SRAM_COPY_*` CSRs.

**ROM and memory map**
- The only ROM is the PIW mask ROM (`fw_sram_copy_iccm_rom.v`). The ROM hex in the repo is stale (Gen6).
- **Unknown:** whether the ROM brings the link up at Gen1.
- Each die has its own ROM, FSM and PSOC.
- `PIW_DCCM` moved from `0x30000` to `0x10000`.
- PHY SRAM ECC is tied off.
- There is no HW 100 ms timer and no HW link-recovery path.
- `DELAY_LINK_TRAINING` resets to 1, so FW must release it.

**PPW_CTRL**
- 4 registers: `MISC_CTRL`, `PHY_CTRL`, `CFG_OB_CLK_DIV`, `PHY_STATUS` (`PHY_STATUS` is new in the CSR).
- A single aggregate PHY interrupt goes to SEI.
- The PHY register map (RAL) is in `project.atlantic2.csr`.
