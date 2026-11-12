# A2 D2D N3 ROM boot (ROM/SRAM select, MST_SLV_DIE, poll SBINIT bit 26, clear MNG_TO_SB); N3P-only PHY (EW/NS); PLDM = real AW digital, no PHY bypass; 2-die image coming

- **Recorded:** 2026-11-11
- **Status:** Neo RTL-verified; node question resolved 2026-11-11 (N3P only)
- **Tags:** a2, d2d, rom, boot, n3, n3p, mcu, rom_sram_sel, mst_slv_die, sbinit, emulation, pldm, palladium, fake phy, phy bypass, fdi bridge, cfg_data

## Scope / Question
How the AW MCU boots on A2, which PHY ROMs RTL contains, and what the emulation model offers.

## Findings

- MCU boots from ROM or SRAM, selected by `ROM_SRAM_SEL` XOR a strap.
- ROM sequence (verif scripts `d2d_fc_flow/d2d_init_die0_die1.lua`, `d2d_rom_boot_flow/`): START_SEL → FETCH_EN / RESET_N → LCNTRL → SCRATCH3[19:16] → PLL ref clock → interrupt masks → `MST_SLV_DIE` on die 0 → poll `REG_GLOBAL_INTR1_SUMMARY` bit 26 (SBINIT) → clear `MNG_TO_SB`.
- **RTL contains only the N3 ROM (`eland_n3_bootrom.hex`); no other-node PHY/ROM exists (single N3P die per back-end flow).** **Resolved 2026-11-11:** Neo confirms a single N3P node with EW/NS orientation variants; no other D2D PHY node (see `a2-physical-variants-and-cdup.md`).
- **User 2026-11-11:** a dual-die PLDM image lands soon; testing planned up to 4 dies. Emulation content is not a FW decision.
- Emulation (PLDM): real AW **digital** logic, no analog front end; dies wired back to back. **No PHY bypass** exists. Full-chip simulation stubs the PHY with an FDI bridge instead.
- Only 1-die PLDM compiles are checked in; a 4-die image is [unverified].
- `Cfg_Data` at MCU-local 0x80000088. Link speeds 4–64 GT/s, default 32. Degrade/repair FW-controlled, unchanged from A1.

## A2 vs A1

A1 uses the same AW MCU boot model.

## Sources

- Neo (neo-ask --chip a2), A2 evidence base: ip.d2d.rtl@9fc7ec3, ip.d2d.csr@5e98931, project.atlantic2.csr@2a12a536, project.atlantic2.rtl@875d851, ip.infra.rtl.shared.atlantic2@9bc4f713, ip.psoc.rtl@2bcf174c; A1: ip.d2d.rtl@88de8f9, ip.d2d.csr@9161c6b, project.iridium.csr@79631f4. Mirror snapshot 2026-11-09. Paraphrased, no HDL.
- A2 spec Rev 2.0.0 ¶1193 (40 Gbps/lane).
