# PSOC IP Knowledge Base — Index

Expert memory for the `psoc-ip` skill. Records verified findings about the PSOC spec (uC cluster,
boot/reset, slow IOs, GPIO/eFuse, crypto, HSM/Caliptra, platform guidelines) and the PSOC RTL
delta (`ip.psoc.rtl` `iridium..main`).

## How to use this file
1. **Before researching**, scan the table and full-text search `knowledge/`.
2. **After verifying** a non-trivial finding, create/update `knowledge/<slug>.md`, add a table row
   (newest at top), and cite sources.
3. **If contradicted later**, correct the topic file.

## Recorded Topics

<!-- Newest at top. Keep Tags lowercase and comma-separated for easy text search. -->

| Topic | File | Tags | Status | Recorded |
|-------|------|------|--------|----------|
| Hot-reset bring-up masks: PCIe #403 was reverted by #417; normal PSOC init can undo an EP-start mask. Experimental #563 keeps HOT's MSK/D2D_MSK set; skip-init images need complementary EP-start coverage. | [hot-reset-mask-bringup.md](hot-reset-mask-bringup.md) | hot reset, sbr, msk, d2d_msk, el_enable_reset, skip-init, ep start | Verified from PR diffs + FW; hardware untested | 2026-10-04 |
| **PERST source select — GPIO pad vs reset-manager unit-reset (idx 16)** — PERST to the EP (`async_psoc2pep_perst_n`) is the reset-manager mux `async_prstn = prst_select_source_gpio ? gpio_pad : async_unit_rst_out_n[pci_rst_unit_idx]` (default idx **16** = `async_pcie_rst_n[0]`, a real PCIe **unit reset**; `rst_comp.txt` 16,17=pcie_rst, 18-23=pci_if_rst). Select polarity: **1=GPIO, 0=reset-manager**; ROM writes `PRST_SELECT_SOURCE=0` → reset-manager source is the ROM mode. In reset-manager mode the PERST line **is** the PCIe unit-reset[16] net → a PERST event coincides with the block being unit-reset, so the EP **cannot** service it as a live/"special" PERST edge IRQ (that only works with GPIO-pad source); it's a block reset with PERST as a side-effect. HW PRST-copy FSM still re-copies PHY FW autonomously if PIW reset domain isn't held. Confirms user claim. | [perst-source-select.md](perst-source-select.md) | prst, perst, prst_select_source, prst_select_source_gpio, pci_rst_unit_idx, async_prstn, async_unit_rst_out_n, async_pcie_rst_n, async_pep_rst_n, reset manager, unit reset, gpio pad, perst source, warm reset, special signal, ep perst isr, rom bring-up | Verified from RTL | 2026-09-14 |
| **PRSTn to PSOC masked by default (eFuse)** — PRSTn into PSOC does not propagate unless the fuse mask is changed. ROM bring-up swaps PRSTn source→PSOC (`BOOT_AND_RST_TOP_0.PRST_SELECT_SOURCE` 0xdac, a1 `0x100000000019dac`=0); **after the swap the PRSTn is effectively masked**. Meaningful PRSTn edge into PSOC must occur **before** the source swap; PRSTn edge IRQs (`PSOC_MISC PRST_N_RISE/FALL`) only fire when PRSTn actually reaches PSOC. | [prstn-psoc-fuse-masked.md](prstn-psoc-fuse-masked.md) | prstn, perst, psoc, fuse, efuse, masked, prst_select_source, boot_and_rst_top, reset source, reset manager, el_reset, gpio37 | Reported by owner (Yoni) — authoritative | 2026-09-08 |
| PSOC uC cluster (3× Cortex-M7 + TuC) — no PCIe uC | [uc-cluster.md](uc-cluster.md) | psoc, uc, cortex-m7, muc, ptuc, ruc, tuc, cluster, pcie uc, mailbox, no pcie uc | Verified from spec | 2026-08-30 |
| PSOC A2 overview + Rev2-vs-Rev1 delta | [a2-psoc-overview.md](a2-psoc-overview.md) | psoc, a2, overview, crypto, sha, ecc p-384, hsm, caliptra, boot, reset, slow io, delta | Verified from spec | 2026-08-30 |

## Topic File Template

```markdown
# <Topic Title>

- **Recorded:** YYYY-MM-DD
- **Status:** Verified from spec | Verified from RTL | Partial / needs follow-up
- **RTL branch/tag:** main (A2) | iridium (A1 baseline) | n/a
- **Tags:** keyword, keyword, keyword

## Scope / Question
<what this entry covers>

## Findings
<verified facts — concise, durable>

## Sources
- Doc: `PSOC Specification Rev2.0.1.docx` — para/table <n> (section "<heading>")
- RTL: `Elmnt-Internal/ip.psoc.rtl` — `compare/iridium...main` (file/commit <sha>)
```
