# PRSTn to PSOC is masked by default (eFuse) in Iridium

- **Recorded:** 2026-09-08
- **Status:** Reported by owner (HSI/FW lead) — authoritative; pending doc/eFuse-bit citation
- **Tags:** prstn, perst, psoc, fuse, efuse, masked, prst_select_source, boot_and_rst_top, rom bring-up, reset source, link training, gpio37

## Scope / Question
Behavior of the PRSTn input to PSOC on Iridium during ROM bring-up, and what it means for
sequences that toggle PRSTn.

## Findings
- **On Iridium, PRSTn to PSOC is masked by default through a fuse.** The PRSTn line into PSOC does
  not propagate unless that fuse mask is changed.
- The ROM bring-up sequence swaps the **PRSTn source to PSOC** (`BOOT_AND_RST_TOP_0.PRST_SELECT_SOURCE`,
  offset `0xdac`, written to `0x0`, abs `0x100000000019dac` on a1). **Because the PSOC PRSTn is
  fuse-masked, once the source is swapped to PSOC the PRSTn is effectively masked** — a PRSTn
  toggle *after* the source swap has **no effect**.
- **Consequence for sequences:** any meaningful PRSTn toggle must be done **before** the source is
  swapped to PSOC (i.e. while the source is still the default/non-masked one). A mid-/late-sequence
  PRSTn toggle placed after the source swap is **redundant / dead** and should be removed.
- Applies to the `a1_link_scale_dbg` register bring-up sequence: the early
  assert/deassert (`PRSTN_TOGGLE`, GPIO37) is the meaningful edge and belongs **before**
  `PHY_PRE_FW_COPY_CFG` (which performs the source swap); the post-copy toggle was dropped.

## Sources
- Owner FYI: FW team lead note, 2026-09-08 — "PRSTn to PSOC is masked by default
  through a fuse; when the ROM sequence swaps the PRSTn source to PSOC it is essentially masked, so
  mid-way PRSTn toggles are redundant — toggle at the beginning before the source is swapped."
- Source (ROM src swap): `PHY_PRE_FW_COPY_CFG` seq / `BOOT_AND_RST_TOP_0.PRST_SELECT_SOURCE`
  (`0x100000000019dac`, a1).
- Related: `iridium-prj/knowledge/reset-domains.md`; `pcie-ep-ip/knowledge/prstn-edge-irq-assert-deassert.md`.
