# PRSTn to PSOC is masked by default (eFuse) in Iridium — bring-up sequence impact

- **Recorded:** 2026-09-08
- **Status:** Reported by owner (Yoni, HSI/FW lead) — authoritative; pending doc/eFuse-bit citation
- **Tags:** prstn, perst, psoc, fuse, efuse, masked, prst_select_source, boot_and_rst_top, rom bring-up, link training, gpio37, phy fw copy, a1_link_scale_dbg, prstn toggle

## Scope / Question
Does toggling PRSTn during the PCIe EP ROM/PHY-FW bring-up sequence actually reach PSOC, and where
must the toggle be placed?

## Findings
- **On Iridium, PRSTn to PSOC is masked by default through a fuse.** The PRSTn line into PSOC does
  not propagate unless that fuse mask is changed.
- The ROM/PHY bring-up swaps the **PRSTn source to PSOC** (`BOOT_AND_RST_TOP_0.PRST_SELECT_SOURCE`,
  offset `0xdac`; a1 abs `0x100000000019dac` → `0x0`). **Because the PSOC PRSTn is fuse-masked,
  once the source is PSOC the PRSTn is effectively masked** — a PRSTn toggle *after* the source
  swap does nothing.
- **Sequencing rule:** perform any meaningful PRSTn toggle **before** the PRSTn source is swapped
  to PSOC. A PRSTn toggle placed after the PHY-FW copy setup (which does the swap) is **redundant**.
- Applied to `a1_link_scale_dbg`: `PRSTN_TOGGLE` (GPIO37 assert→wait→deassert) runs at the start,
  before `PHY_PRE_FW_COPY_CFG` swaps the source; the previous post-copy PRSTn toggle was removed.
- Relates to `prstn-edge-irq-assert-deassert.md`: the EP's PRSTn deassertion ISR / warm-reset path
  only sees PRSTn edges when the PRSTn actually reaches PSOC — the fuse-masked default and the
  source-swap timing govern when that happens.

## Sources
- Owner FYI: Yoni Izutskiver (HSI/FW team lead), 2026-09-08 — "PRSTn to PSOC is masked by default
  through a fuse; when the ROM sequence swaps the PRSTn source to PSOC it is essentially masked,
  so mid-way PRSTn toggles are redundant — toggle at the beginning before the source is swapped."
- Reg: `BOOT_AND_RST_TOP_0.PRST_SELECT_SOURCE` (offset `0xdac`; a1 abs `0x100000000019dac`).
- Seq: `a1_link_scale_dbg/` (`PRSTN_TOGGLE`, `PHY_PRE_FW_COPY_CFG`, `MAIN`).
