# PCIe Diagnostic CSR Read Safety

- Recorded: 2026-10-04.
- Status: Verified from generated A1 CSR headers and driver code; candidate not hardware-qualified.
- Tags: diag, regs_dump, precise bus fault, bfar, write-only, interrupt injection, sparse dump, ltssm.

## Findings

- A1 `diag pcie_ep` continuous register dumping faulted in `shell_uart` at
  BFAR `0xf0440010`, after successfully printing PEP core offsets 0 through 0xc.
- Core offset `0x10` is implemented: `PEP_CTRL_INTERRUPT_INTR_SEI_INJECT`, with
  generated `__SW_ACCESS=1` (write-only). It is not a hole or readable status CSR.
  Offset `0x14` is `PEP_CTRL_INTERRUPT_INTR_SEI_MASKED_INTR`, access attribute 0.
- A devicetree register window is not a promise that every contained word is
  readable. Never scan its entire range on silicon or catch-and-continue faults.
  Enumerate reviewed readable addresses, filter optional ranges against that
  enumeration, and reject unsupported checked-read offsets before MMIO.
- Array elements may share access metadata: logger mask offsets `_0` and `_1`
  have the common `LTSSM_LOGGER_STATE_MASK__SW_ACCESS` attribute, not per-element
  attributes. CTRL PIW does not have the MNG PIW `DRAIN_FORK` register.
- Runtime LTSSM names can share the existing 36-state enum. A following retained
  logger entry is an observed successor, not necessarily the immediate transition
  when masks omit states. Do not infer reason-selector semantics without evidence.
- Driver-owned sparse lists and checked readers were added in PCIe commit
  `d4d9ee1`. An isolated host harness compiled the actual helper bodies with
  generated CSR headers and MMIO spies: injection, holes, alignment, bounds and
  ownership rejection issued no MMIO. This is not silicon fault-free validation.
- Commit `0cf4ca1` moves register readers/lists into default-off
  `DWC_EP_CTL_REG_DUMP` / `EL_PIW_REG_DUMP` sources. The bring-up field table
  covers all 121 fields in the active headers across 15 registers, including
  reserved fields. `FILTER_MASK_2_OFF` exposes one aggregate `MASK_RADM_2`
  field in this package; do not invent additional CSR-defined subfields.

## Sources

- Active A1 CSR package `ips/pcie/v1/3_0_11_3p0_1389/prototype/pep_ctrl_regs.h`,
  defines at lines 70-80; common logger state-mask access attribute.
- Same package `piw_ctrl_cfg_regs.h` and `piw_mng_cfg_regs.h`: per-port register maps.
- `ip.pcie.sw.fw.ep`, `drivers/snps_ep_ctl/dwc_ep_ctl_reg_dump.c`:
  `dwc_ep_ctl_debug_reg_offset`, `dwc_ep_ctl_debug_read_reg`,
  `dwc_ep_ctl_read_bringup_status`; `dwc_ep_ctl_bringup_fields.c` field descriptors;
  `dwc_ep_ctl.c` LTSSM state naming; `drivers/el_piw/el_piw_reg_dump.c` checked readers.
- User-supplied A1 UART fault log during `diag pcie_ep`, 2026-10-04.
