# PRSTn edge interrupt — rise=deassert / fall=assert (errata PRJIRDMSW-2221) & FW correctness

- **Recorded:** 2026-08-16
- **Status:** Verified from FW + generated headers + errata
- **Tags:** prstn, perst, prst_n_rise, prst_n_fall, psoc_misc interrupt, critical interrupt, rise fall edge, active low, assertion, deassertion, prstn_deassertion, el_pcie_ep_perstn_deassertion_isr, el_reset_set_prstn_mask, el_reset_clear_prstn_cause, el_reset_prstn_irq_edge_rising, errata prjirdmsw-2221, gpio_status, warm reset callback

## Scope / Question
Errata **PRJIRDMSW-2221** "Critical interrupt PRSTn assertion / deassertion are swapped":
the critical-interrupt status register has **bit [8] = PRSTn rise**, **bit [9] = PRSTn fall**.
Because PRSTn is **active-low (AL)**, "fall" (1→0) = **asserted** and "rise" (0→1) = **deasserted**.
The *spec table* (image in the ticket) labeled these the opposite way (bit8=assert / bit9=deassert),
so the errata is a **spec-labeling correction**, not a HW/RTL wiring bug — the HW bits genuinely are
edge bits. Question: **does FW use this interrupt correctly?**

## Findings
**Yes — FW is correct.** FW never relies on the swapped spec "assert/deassert" labels; it uses the
physically-unambiguous **edge-named** PSOC_MISC registers and correctly associates
**rise → deassertion**, exactly matching the errata's corrected statement.

Ground truth mapping (AL PRSTn):
- **PRST_N_RISE** (rising edge 0→1) = **deassertion** = link may start / run post-reset handling.
- **PRST_N_FALL** (falling edge 1→0) = **assertion** = host is holding the EP in reset.

FW chain (all self-consistent on the **RISING** edge for the "deassertion" path):
- **Arm/mask:** `el_reset_set_prstn_interrupt_mask(EL_RESET_PRSTN_IRQ_EDGE_RISING, mask)`
- **Clear cause:** `el_reset_clear_prstn_interrupt_cause(EL_RESET_PRSTN_IRQ_EDGE_RISING)`
- **ISR:** `el_pcie_ep_perstn_deassertion_isr()` clears the RISE cause then runs
  `el_pcie_ep_reset_perstn_deasserted()` (post-reset bring-up). DT IRQ name = `prstn_deassertion`
  (MuC NVIC vector `0xA8` in the A1 Palladium overlay; source block = PSOC_MISC via the
  `pcie_ep0` node's `psoc_pio_misc_regs` reg).
- **Level companion check:** `el_pcie_ep_is_perstn_deasserted()` → `el_reset_get_perstn_status()`
  → `(BOOT_AND_RST_TOP_RST_MISC_PRSTN_GPIO_STATUS & VAL_MSK) ? true : false`. GPIO bit set = line
  high = **deasserted → true**. Consistent (true = deasserted).

Notes / caveats:
- The low-level `el_reset` driver (`el_reset_api.c`) exposes a **generic edge API**
  (`EL_RESET_PRSTN_IRQ_EDGE_RISING`/`FALLING`) mapping straight to the separate
  `PSOC_MISC_INTERRUPT_PRST_N_RISE_*` / `PRST_N_FALL_*` cause+mask registers (each a single-bit
  edge cause, **not** one combined bit-8/bit-9 status word in the FW headers). No assert/deassert
  naming there → immune to the spec swap.
- FW only registers the **deassertion (rise)** edge IRQ. There is **no fall-edge "assertion"
  IRQ handler**; the assertion side is handled via the **warm-reset callback**
  (`el_pcie_ep_reset_callback` PRE/POST_CFG), not an edge interrupt. So no code path treats fall
  as deassert (or rise as assert) → no swap bug.
- **Errata component is `ip-hsm-firmware` (SE/Security), but this looks mis-assigned** (the ticket
  even has an automation comment asking to fix Components). No SE FW code consumes PRST rise/fall;
  the real consumers are the **PCIe EP driver** + **PSOC `el_reset` driver**.
- **Action item for any FUTURE consumer** (e.g. an SE handler, or a new "PERSTn asserted" edge
  path): do **not** follow the old spec labels — use `PRST_N_RISE`=deassert, `PRST_N_FALL`=assert.

## Sources
- Jira: **PRJIRDMSW-2221** (Errata Bug, Open, Medium) — "Critical interrupt PRSTn assertion /
  deassertion are swapped"; Actual: [8]=PRSTn rise, [9]=PRSTn fall; AL → fall=asserted, rise=deasserted.
- FW: `ip.pcie.sw.fw.ep` → `drivers/el_pcie_ep/el_pcie_ep_irq.c`
  (`el_pcie_ep_perstn_deassertion_isr` — clears `EDGE_RISING`)
- FW: `drivers/el_pcie_ep/el_pcie_ep.c` (`el_pcie_ep##inst##_set_perstn_irq_mask` — arms/clears
  `EDGE_RISING`; `EL_CPU_CRITICAL_IRQ_PRSTN_DEASSERTION` critical-IRQ mask)
- FW: `drivers/el_pcie_ep/el_pcie_ep_reset.c` (`el_pcie_ep_is_perstn_deasserted` →
  `el_reset_get_perstn_status`; warm-reset callback for the assertion side)
- FW: `ip/psoc/drivers/el_reset/el_reset_api.c` (`el_reset_set_prstn_mask`,
  `el_reset_clear_prstn_cause`, `el_reset_get_perstn_sts`)
- Headers: `psoc_misc_regs.h` — `PSOC_MISC_INTERRUPT_PRST_N_RISE_CAUSE/MASK`,
  `..._PRST_N_FALL_CAUSE/MASK`; `BOOT_AND_RST_TOP_RST_MISC_PRSTN_GPIO_STATUS__VAL_MSK`
- DTS: `proj_infra/soc/dts/a1_hsi.dtsi` (`pcie_ep0` reg = PSOC_MISC block);
  `zephyr_apps/mng/boards/el_arm_a1_palladium.overlay` (`interrupts = <0xA8 0>`,
  `interrupt-names = "prstn_deassertion"`)
