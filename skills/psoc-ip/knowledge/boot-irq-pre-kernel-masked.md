# Boot IRQ handling on MuC (Cortex-M7, mng/primeboot): PRE_KERNEL IRQs stay pending until the main-thread switch

- **Recorded:** 2026-11-01
- **Status:** Verified from FW workspace source (Zephyr 3.7 fork + A1 FW tree)
- **RTL branch/tag:** n/a (SW behavior)
- **Tags:** boot irq, basepri, pre_kernel, post_kernel, plic, el_plic, nvic, primeboot, mng, cortex-m7, d2d interrupt, zero latency, zli, arch_switch_to_main_thread, reset.s

## Scope / Question
On A1 (Cortex-M7 MuC, mng/primeboot): is an interrupt that is latched and enabled during
PRE_KERNEL_1/2 masked until POST_KERNEL, and does anything (SoC init, PLIC, primeboot handoff)
unlock interrupts earlier?

## Findings
1. **Reset masks IRQs via BASEPRI.** `zephyr/arch/arm/core/cortex_m/reset.S:115-120` — on
   ARMv7-M mainline (M7): `movs r0, #_EXC_IRQ_DEFAULT_PRIO; msr BASEPRI, r0` ("lock interrupts:
   will get unlocked when switch to main task"). Baseline parts use `cpsid i` instead.
2. **Unlock happens only in `arch_switch_to_main_thread`.**
   `zephyr/arch/arm/core/cortex_m/thread.c:520-601` — sets PSP, then
   `arch_irq_unlock_outlined(0)` (`__enable_fault_irq()` + `__enable_irq()` + BASEPRI→0), then
   jumps to `z_thread_entry`/`bg_thread_main`.
3. **Ordering nuance:** `zephyr/kernel/init.c` — `z_cstart` runs PRE_KERNEL_1 (l.778) and
   PRE_KERNEL_2 (l.779), then `switch_to_main_thread` (l.795). POST_KERNEL init runs *inside*
   `bg_thread_main` (l.519) **after** the unlock. So a pending IRQ is serviced at the unlock,
   i.e. **before the POST_KERNEL init functions run** — "serviced once POST_KERNEL starts" is
   right at the boundary, but an ISR must not depend on POST_KERNEL-initialized state.
4. **"PLIC" on A1 = EL's own 2nd/3rd-level aggregator**, not the RISC-V PLIC:
   `ip/infra/drivers/el_plic/intc_el_plic.c` (`CONFIG_EL_PLIC=y` in primeboot a1 boards). In
   primeboot it inits at **PRE_KERNEL_1**, prio 31 (`zephyr_apps/primeboot/Kconfig.boot_priorities:414-421`,
   level 1 → PRE_KERNEL_1 per `infra/include/el_dt_device.h:30-36`). Its init `irq_enable()`s
   the NVIC lines (intc_el_plic.c:679) — so interrupts latch pending in the NVIC during
   PRE_KERNEL but execution stays masked by BASEPRI. Nothing is lost (edge IRQs latch in the
   NVIC pending register; level IRQs stay asserted).
5. **Nothing unlocks earlier on A1.** Workspace-wide grep of `ip/`, `infra/`, `baremetal/`,
   `zephyr_apps/` found no `__enable_irq` / `cpsie i` / `__set_BASEPRI` outside tests. The
   FLA→primeboot handoff is irrelevant: Zephyr's reset.S re-asserts BASEPRI at entry.
6. **Exception — zero-latency IRQs bypass BASEPRI** (`CONFIG_ZERO_LATENCY_IRQS`, prio 0 above
   BASEPRI). Enabled **only in pwr_thrm (PTuC)** a1 boards (`zephyr_apps/pwr_thrm/boards/*.conf`,
   `ptl_power_sample_isr` with `IRQ_ZERO_LATENCY`). Not in primeboot or mng — so the "masked
   until main" statement holds for primeboot/mng; on PTuC the ZLI would fire during PRE_KERNEL
   once NVIC-enabled.
7. Confirms FW owner statement (Teams, Pardha Saradhi Kesapragada, 2026-11-01): PLIC enabled in
   primeboot during driver init phase; switch-to-main changes BASEPRI; pending IRQs delivered
   after reaching main.

## FLA RT → primeboot handoff (chain-load does not leak IRQ state)
FLA RT is itself a full Zephyr app and runs the same cycle *before* primeboot: its own reset.S
masks, its own main-switch unmasks, and it **does service interrupts during its lifetime** (it
deliberately keeps PSoC reset / ROM reboot disabled on warm reset so it can service the
reset-manager and PERSTn IRQs itself — `zephyr_apps/fla_runtime/src/common.c:312-346`).
Handoff sequence (`fla_rt_jump_to_primeboot`, common.c:275-355):
`el_pcie_ep_reset_prepare_handoff()` (restores reset behavior; code comment: *"from here until
PrimeBoot's PCIe EP init runs, no image is servicing those interrupts"* — a real coverage gap
window) → `fla_rt_signal_boot_done()` → `irq_lock()` (BASEPRI up) → `__set_MSP` → jump.
Primeboot entry then re-runs reset.S with `CONFIG_INIT_ARCH_HW_AT_BOOT=y` (board default for all
A1 ARM boards, `proj_infra/boards/arm/a1/Kconfig.el_arm:59`) → `z_arm_init_arch_hw_at_boot`
(`zephyr/arch/arm/core/cortex_m/scb.c:91`) **disables every NVIC line (ICER=~0) and clears every
pending bit (ICPR=~0)**, clears FAULTMASK/MPU/caches; reset.S then re-asserts BASEPRI.
⇒ Each image's IRQ world is self-contained. Any edge interrupt latched during FLA RT but not
serviced by it is **wiped** at primeboot entry; level-triggered sources simply re-pend once
el_plic re-enables them in primeboot's PRE_KERNEL_1. The per-image "masked until main" model
holds unchanged for primeboot regardless of FLA RT running first.

## Sources
- `fw/zephyr/arch/arm/core/cortex_m/reset.S:111-123`
- `fw/zephyr/arch/arm/core/cortex_m/thread.c:520-605` (`arch_switch_to_main_thread`,
  `arch_irq_unlock_outlined`)
- `fw/zephyr/kernel/init.c:508-795` (`bg_thread_main` POST_KERNEL, `z_cstart`)
- `fw/ip/infra/drivers/el_plic/intc_el_plic.c:679,942`
- `fw/zephyr_apps/primeboot/Kconfig.boot_priorities:414-421`; `fw/infra/include/el_dt_device.h:24-36`
- `fw/zephyr_apps/fla_runtime/src/common.c:275-355` (`fla_rt_jump_to_primeboot`)
- `fw/zephyr/arch/arm/core/cortex_m/scb.c:91-150` (`z_arm_init_arch_hw_at_boot`)
- `fw/proj_infra/boards/arm/a1/Kconfig.el_arm:59` (`INIT_ARCH_HW_AT_BOOT` default y)
- `fw/zephyr_apps/pwr_thrm/boards/el_arm_a1_asic.conf:71`; `fw/zephyr_apps/pwr_thrm/src/ptl_power_isr.c:447`
