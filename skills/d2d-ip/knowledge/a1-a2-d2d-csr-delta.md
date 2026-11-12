# A1→A2 D2D CSR delta: field widths (deskew 2→4b, RXDATCHK ERR_CNT 16→32b), new LOCKP/ECC_CLR SERR-DERR/RX_DESKEW_STS/VAL_ERR_CNT/ISO_WD_CNT; RTL delta tiny

- **Recorded:** 2026-11-11
- **Status:** CSR headers verified; Neo partly contradicted
- **Tags:** a1, a2, d2d, csr delta, field width, rx_deskew_max_count, err_cnt, lockp, ecc_clr, iso_wd_cnt, rx_deskew_sts, fiu, trace memory, alphawave

## Scope / Question
What changed in the D2D register model and RTL between A1 and A2.

## Findings

- Header comparison (A1 `3_0_11_3p0_1389` vs A2 `1_3_0_1p3_870`):
  - `RX_DESKEW_MAX_COUNT`: 2 → **4 bits**.
  - `AW_RX0/1 RXDATCHK_RDREG2 ERR_CNT`: 16 → **32 bits**; new `RXDATCHK_RDREG15 VAL_ERR_CNT` (32 b).
  - `D2D_PL_*ERROR_CNT`: 32 b in both (unchanged).
  - PLL `LOCKP` fields: 0 → 64 symbols. ECC_CLR fields: ADDR → SERR/DERR.
  - CDUP: 0 → 760 symbols; OUTSTANDING: 0 → 26.
  - New `REG_PHY_MMPL_RX_DESKEW_STS`; new FIU_SLV SEI `ISO_WD_CNT`.
- **Correction:** Neo (a3) said deskew is 4 b and ERROR_CNT 32 b in both chips and that CDUP/outstanding are not new. The generated headers contradict this; trust the headers for field widths.
- RTL (Neo): `ip.d2d.rtl` A1→A2 differs only in `d2d_wrap.v` and `d2d_fiu_top.v`: new FIU trace memory (`FIU_CFG_TRC_mem`), CSR-to-memory path, FIU serr/derr interrupts moved to the slave-CSR interface.
- A1 also uses AlphaWave (`alpha_adapter.v` in both); no DWC_ucie in either.
- Tag 1047 splits the wrapper header into CMN/MID/SIDE (see `a2-cdup-mid-only-and-1047-removals.md`).

## A2 vs A1

This file is the delta.

## Sources

- Neo (neo-ask --chip a2), A2 evidence base: ip.d2d.rtl@9fc7ec3, ip.d2d.csr@5e98931, project.atlantic2.csr@2a12a536, project.atlantic2.rtl@875d851, ip.infra.rtl.shared.atlantic2@9bc4f713, ip.psoc.rtl@2bcf174c; A1: ip.d2d.rtl@88de8f9, ip.d2d.csr@9161c6b, project.iridium.csr@79631f4. Mirror snapshot 2026-11-09. Paraphrased, no HDL.
- Headers: ~/projects/iridium/two/fw/build_artifacts/include/ips/d2d/v1/3_0_11_3p0_1389/prototype/, ~/projects/iridium/two/fw/build_artifacts/include/ips/d2d/v2/1_3_0_1p3_870/prototype/, ~/projects/iridium/three/fw/build_artifacts/include/ips/d2d/v2/1_4_0_1p4_1047/prototype/.
