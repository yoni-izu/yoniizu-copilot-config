# Iridium SCHD top-level SEI cause map

- **Recorded:** 2026-10-28
- **Status:** Verified from A1 FW source and generated CSR headers
- **Tags:** iridium, schd, sei, cause, strmr, psm, fiu

## Scope / Question

Decode the `cause` value logged by `el_schd_sei_isr` and distinguish it from the
sub-block cause logged by `el_schd_strmr_sei_info`.

## Findings

`el_schd_sei_isr` reads `SCHD_TOP_MNG_INTERRUPT_SEI_CAUSE`, a top-level
source bitmap. In the inspected A1 SCHD regmap: bit 0 `FIU_MEM`, bit 1
`FIU_CTRL`, bit 2 `FIU_MNG`, bit 3 `STRMR`, bit 4 `PSM`, bit 5 `DUP_SP`,
bit 6 `DUP_0`, and bit 7 `DUP_1`. Thus top-level `0x8` means the STRMR
source.

The STRMR handler separately reads `STRMR_MNG_INTERRUPT_SEI_CAUSE`. Its
bit meanings differ: `0x8` is `DIS_DESC_SIZE_ZERO` (bit 3); `0x11` is
`DIS_ILLEGAL_OPCODE` (bit 0) plus `DIS_DI_PASS_PI` (bit 4). Top-level
`0x11`, by contrast, means FIU_MEM plus PSM. Interpret a `cause` value only
after identifying which handler/register produced the log. `inst=N`
identifies the SCHD instance, not a cause bit.

## Sources

- Source: `fw/ip/schd/drivers/el_schd/el_schd.c`
  (`el_schd_sei_isr`, `el_schd_strmr_sei_info`)
- CSR: `fw/build_artifacts/include/ips/schd/v1/3_0_11_3p0_1389/prototype/schd_top_mng_regs.h`
- CSR: `fw/build_artifacts/include/ips/schd/v1/3_0_11_3p0_1389/prototype/strmr_mng_regs.h`
- User-provided CSR snapshot: `file_strmr_mng_regs.h` (`STRMR_MNG_INTERRUPT_SEI_CAUSE` fields)
