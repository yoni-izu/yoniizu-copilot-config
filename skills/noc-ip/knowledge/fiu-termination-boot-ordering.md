# FIU termination exit vs. driver boot ordering

- **Recorded:** 2026-11-01
- **Status:** Verified from spec + FW code (A1 power-on workspace)
- **RTL branch/tag:** n/a (FW-side finding; spec is A2, FW is A1 — behavior identical)
- **Tags:** fiu, termination, reset isolation, iso_sw_man, boot order, init priority, psoc, exit-termination

## Scope / Question
Is it mandatory to run the FIU driver (exit-termination) before non-PSOC drivers so they
can access blocks outside PSOC?

## Findings
1. **HW default: terminations are ON after reset.** A2 NoC spec Rev 1.4.0, FIU → "Reset
   Isolation" (para 2692): *"After reset is completed, the FW is expected to disable the
   termination logic (after programming all security related attributes)."* FW code agrees:
   `ip/noc/include/el_fiu_group.h:70` — "HW enters termination on reset; this brings engines
   out" (exit = write to `ISO_SW_MAN`, value don't-care). While a FIU port is in termination,
   transactions through it are terminated with an error response (termination classes in
   `el_fiu_intr.h`: RR/ATTR/MAN/DEC/E2E). ERRATA-011 Kconfig text confirms decoders also come
   out of reset in default states.
2. **So yes — any driver whose init does MMIO through a still-terminated FIU port must run
   after the exit-termination for that port's group.** The precise invariant is "no MMIO
   through a terminated FIU", not literally "FIU before every non-PSOC driver".
3. **Who exits what, when (A1 boot chain):**
   - **Boot ROM (`fla_rom`)** exits the PSOC FIU group's MEM/MNG/CTRL MST + MNG/CTRL SLV + DBG
     `ISO_SW_MAN` (`baremetal/fla_rom/src/common.c:733-890`) — that's why PSOC-local drivers
     can init before the FIU driver in later apps.
   - **FLA RT** sets `CONFIG_EL_FIU_MGR_DISABLE_INIT=y` and calls
     `el_fiu_mgr_exit_termination_all()` at an explicit point (PIW+PEP groups), after setting
     PCIe downstream termination on production parts
     (`zephyr_apps/fla_runtime/src/common.c:474`).
   - **mng app**: `EL_FIU_MGR` SYS_INIT (level 1, prio 45) runs the full `fiu_mgr_reinit()` —
     step 4 is exit-termination on all groups (`el_fiu_mgr.c` `mgr_reinit_filtered`). The
     per-app `Kconfig.boot_priorities` (MODINFRASW-20) places EL_FIU_GROUP 1/44, EL_FIU_MGR
     1/45 before the off-PSOC drivers: AXI_SEC/PLL 1/50, PCIE_EP 1/51, DWC_EP_CTL 1/52,
     D2D wrapper level 2, NOC/CCE/DMA/MMU level 3.
   - Drivers legally *before* 1/45: no-HW-at-init (EL_FIU_GROUP, EL_FIU_BINNING NULL init) or
     PSOC-local only (PINCTRL 34, GPIO 35, EFUSE 35, EL_PLIC 31 — MuC PLIC regs at
     0xF0086000/0x60087000 are PSOC-local, covered by the ROM's PSOC exit).
4. **Post-reset (die reset, not boot):** the reset driver first runs
   `prog_high_priority_term_exit` (PSOC is the only `post-reset-priority="high"` group) so the
   PSOC uC/memories come up, then must pair it with full `el_fiu_mgr_reinit()`
   (`el_fiu_mgr.h:53-128`). IPPSOCRTL-1408: PSOC FIU_DRAIN addr-dec ranges must be programmed
   right after exit-termination, before any PSOC uC/memory access (`el_fiu_mgr.c:179`).
5. **Spec qualifier:** termination should be dropped *after programming security-related
   attributes* — the FIU reinit sequence also programs TERM_TIMEOUT, HD2D/PCI BIN_ADDR, etc.
   around the exit step.
6. Caveat noted: `el_plic_init` with `CONFIG_3RD_LEVEL_INTERRUPTS` on multi-die also writes
   *remote* dies' PLIC via die MNG offsets — that path relies on the remote die's own ROM
   having exited its PSOC terminations (and the D2D path), not on the local FIU mgr init.

## Sources
- Doc: `Atlantic2 NoC Specification Rev 1.4.0.docx` — para 2691-2692 (FIU → "Reset Isolation")
- FW: `ip/noc/include/el_fiu_group.h:68-78`, `ip/noc/include/el_fiu_mgr.h:53-128`,
  `ip/noc/drivers/el_fiu/el_fiu_mgr.c` (steps 1-11, `fiu_mgr_init`),
  `ip/noc/drivers/el_fiu/Kconfig` (ERRATA-011, IPPSOCRTL-1408),
  `baremetal/fla_rom/src/common.c:733-890`, `zephyr_apps/fla_runtime/src/common.c:460-478`,
  `zephyr_apps/fla_runtime/boards/el_arm_a1_asic.conf:115-116` (A1 power-on workspace)
