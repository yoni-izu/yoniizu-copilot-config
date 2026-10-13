# PERST source select: GPIO pad vs reset-manager unit-reset (index 16)

- **Recorded:** 2026-09-14
- **Status:** Verified from RTL
- **RTL branch/tag:** iridium (A1 baseline); mux identical structure expected on main — re-verify for A2
- **Tags:** prst, perst, prst_select_source, prst_select_source_gpio, pci_rst_unit_idx,
  async_prstn, async_unit_rst_out_n, async_pcie_rst_n, async_pep_rst_n, reset manager,
  unit reset, gpio pad, async_psoc2pep_perst_n, async_psoc2pcie_prst_n, perst source,
  rom bring-up, warm reset, special signal, ep perst isr

## Scope / Question
When the PERST source is the **reset manager** (not the external GPIO pad), does the PCIe
EP just get a **unit reset** on a PERST event — i.e. it can't treat PERST as a distinct
"special" signal with its edge ISR? (User claim to validate.)

## Findings
**Yes — essentially correct.** PERST delivered to the EP is a MUX output of the PSOC reset
manager, and the reset-manager branch of that mux **is a PCIe unit-reset output**.

`psoc_boot_rst_mng_top.v` (PERST handling):
```
async_prstn_ns = prst_select_source_gpio_data_r
               ? (prstn_gpio | prstn_gpio_force_val_data_r)          // 1 = external GPIO pad
               : async_unit_rst_out_n[pci_rst_unit_idx_index_data_r] // 0 = reset-manager unit reset
;                                                                    //   comment: "pci = index 16"
```
- `async_prstn` (this mux) → SoC net `async_psoc2pcie_prst_n` → EP port
  `async_psoc2pep_perst_n` (the PERST the controller/PIW see). Active-low.
- **Select polarity:** `prst_select_source_gpio = 1` → GPIO pad; `= 0` → reset-manager.
  ROM bring-up writes `BOOT_AND_RST_TOP_0.PRST_SELECT_SOURCE = 0x0` → **reset-manager
  source is the ROM-configured mode** (see `prstn-psoc-fuse-masked.md`; with the PSOC
  PRSTn fuse-masked, the external pad is dead anyway).
- **Index 16 = a PCIe unit reset.** `rst_comp.txt`: unit indices **16,17 = `async_pcie_rst_n[1:0]`**
  (18–23 = `async_pci_if_rst_n[5:0]`). The PERST-source index (`pci_rst_unit_idx`, default 16)
  is `async_pcie_rst_n[0]` — the same reset-manager output that resets the PCIe unit.
- The EP receives PERST and the PEP unit reset on **separate ports**
  (`pcie_ep_cls.v`: `async_psoc2pep_perst_n` and `async_pep_rst_n`), BUT in reset-manager
  mode the **PERST line is literally the unit-reset[16] net**. So when the reset manager
  runs a reset flow that asserts the PCIe unit (idx 16), that one net asserts **both** the
  block's unit reset and PERST together.

**Consequence:** In reset-manager-source mode, a PERST event coincides with the PCIe unit
being reset — the EP is held in reset and cannot service it as a discrete, live PERST edge
IRQ (`el_pcie_ep_perstn_deassertion_isr`). It's a block reset with PERST as a side-effect.
The "special" PERST handling (edge ISR while the block stays alive; SW-visible
assert→deassert sequence) is only meaningful when PERST is sourced from the **external GPIO
pad**, independent of the block's unit reset. The HW **PRST-triggered PHY-FW copy FSM**
(`fw_sram_copy_handler`, see `pcie-ep-ip/knowledge/phy-fw-copy-fsm.md`) still re-copies PHY
FW autonomously on the PERST rising edge, provided the PIW copy engine's own reset domain
(`async_piw_rst_n`) is not held by the same flow.

**Caveat / one inference:** verified that idx-16 is `async_pcie_rst_n[0]` and is the PERST
source; the exact idx-16 → `async_pep_rst_n` (which resets the ISR-running logic) mapping is
inferred from naming + the 2-bit `async_pcie_rst_n` grouping, not traced net-by-net through
chip-top integration. The architectural conclusion (PERST-from-rstmgr == a PCIe unit reset)
holds regardless.

## Sources
- RTL: `Elmnt-Internal/ip.psoc.rtl` @ `iridium`
  - `psoc_boot/rtl/psoc_boot_rst_mng_top.v` (`async_prstn_ns` mux, `prst_select_source_gpio`,
    `pci_rst_unit_idx`, `//pci = index 16`)
  - `psoc_boot/misc/rst_comp.txt` (idx 16,17=`async_pcie_rst_n`; 18-23=`async_pci_if_rst_n`)
  - `top/rtl/psoc_cls.v` (`async_psoc2pcie_prst_n`, `async_unit_rst_out_n[127:0]` outputs)
- RTL: `Elmnt-Internal/ip.pcie.rtl` @ `iridium` → `cluster/rtl/pcie_ep_cls.v`
  (separate ports `async_pep_rst_n`, `async_psoc2pep_perst_n`)
- Related KB: `prstn-psoc-fuse-masked.md` (PRST_SELECT_SOURCE=0 → PSOC source, fuse mask),
  `pcie-ep-ip/knowledge/phy-reset.md`, `phy-fw-copy-fsm.md`,
  `prstn-edge-irq-assert-deassert.md`.
