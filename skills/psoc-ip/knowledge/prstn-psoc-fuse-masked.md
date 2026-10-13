# PRSTn to PSOC is masked by default (eFuse) in Iridium

- **Recorded:** 2026-09-08
- **Status:** Reported by owner (Yoni, HSI/FW lead) — authoritative; pending doc/eFuse-bit citation
- **RTL branch/tag:** iridium (A1 baseline)
- **Tags:** prstn, perst, psoc, fuse, efuse, masked, prst_select_source, boot_and_rst_top, reset source, reset manager, el_reset, link training, gpio37, rom bring-up

## Scope / Question
Whether the PRSTn input to PSOC is active on Iridium, and what it implies for PSOC reset-source /
reset-manager handling and for bring-up sequences that toggle PRSTn.

## Findings
- **On Iridium, PRSTn to PSOC is masked by default through a fuse** — the PRSTn line into PSOC does
  not propagate unless the fuse mask is changed.
- ROM bring-up swaps the **PRSTn source to PSOC** via `BOOT_AND_RST_TOP_0.PRST_SELECT_SOURCE`
  (offset `0xdac`; a1 abs `0x100000000019dac`, written `0x0`). **With the PSOC PRSTn fuse-masked,
  after this swap the PRSTn is effectively masked** — toggling PRSTn afterwards does nothing at PSOC.
- **Implication:** a meaningful PRSTn edge into PSOC must occur **before** the source is swapped to
  PSOC. Any PRSTn toggle after the swap is dead. This is why the `a1_link_scale_dbg` debug sequence
  toggles PRSTn (GPIO37) at the very start, before `PHY_PRE_FW_COPY_CFG` performs the source swap,
  and drops the later mid-sequence toggle.
- Cross-check with the PSOC reset manager / `el_reset` PRSTn edge IRQ handling: the PRSTn
  rise/fall edge interrupts (`PSOC_MISC INTERRUPT PRST_N_RISE/FALL`) are only observable when the
  PSOC PRSTn is actually driven; a fuse-masked default means the default state delivers no PRSTn
  edges to PSOC until unmasked / source configured appropriately.

## Sources
- Owner FYI: Yoni Izutskiver (HSI/FW team lead), 2026-09-08 — "PRSTn to PSOC is masked by default
  through a fuse; when the ROM sequence swaps the PRSTn source to PSOC it is essentially masked,
  so mid-way PRSTn toggles are redundant — toggle at the beginning before the source is swapped."
- Reg: `BOOT_AND_RST_TOP_0.PRST_SELECT_SOURCE` (offset `0xdac`; a1 abs `0x100000000019dac`).
- Related: `pcie-ep-ip/knowledge/prstn-edge-irq-assert-deassert.md`;
  `iridium-prj/knowledge/prstn-psoc-fuse-masked.md`.
