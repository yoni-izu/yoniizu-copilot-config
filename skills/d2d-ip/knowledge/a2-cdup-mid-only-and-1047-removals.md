# A2 wrapper CDUP: mid instances only at D2D_MID+0x800, ≤2 copies, DUP_DISABLE=1 reset, MC_MASK.DIE before enable; tag 1047 removes 17 CDUP regs

- **Recorded:** 2026-11-11
- **Status:** Neo RTL-verified + CSR headers
- **Tags:** a2, d2d, cdup, multicast, mid, dup_disable, mc_mask, rsp_err, sei, csr 1047, project_topology, driver api

## Scope / Question
Where the A2 D2D CDUP block lives, how to enable it safely, and how the CSR changed at tag 1047.

## Findings

- CDUP exists **only in mid wrappers** (VD2D), at `D2D_MID` + 0x800. At most 2 copies.
- `DUP_DISABLE` resets to 1. Program `MC_MASK.DIE` (and the mask/coord tables) **before** clearing `DUP_DISABLE`, with no traffic in flight.
- CDUP response errors raise `RSP_ERR` → SEI cause bit 9; cause/mask in `INTERRUPT_RSP_ERR_CAUSE/MASK`.
- **Tag 1047 (`1_4_0_1p4_1047`) vs 1.3 (`1_3_0_1p3_870`)**: `d2d_regs.h` is split into `d2d_cmn_regs.h` / `d2d_mid_regs.h` / `d2d_side_regs.h`; 17 CDUP registers are removed: `D2D_CDUP_ADDR_DECODE`, `CDUP_CSU_CRDT_THRESHOLD_0..3`, `CDUP_SCALE_UP_CFG`, `CDUP_SCALE_UP_POLYNUM_0..10`; one is added: `D2D_MID_CDUP_PROJECT_TOPOLOGY`.
- Remaining CDUP regs at 1047 (mid): GENERAL, MC_MASK, MC_MASK_TRAY, VCORE_COORD, CHIP_GRP_MASK/DST_COORD_0..3, CHIP_DST_COORD/INTERSLED_TRANS_TABLE_0..15, STAT_SILENT_DROP_CNT, STAT_RSP_ERR_INFO, CCE_SLAVE_COORD, INTERRUPT_RSP_ERR_CAUSE/MASK.
- Neo looked at ip.d2d.csr@5e98931; which tag that corresponds to is unknown — the map is still moving.

## A2 vs A1

A1 wrapper has no CDUP; A1 D2D-channel CDUP is in the NoC `d2d_ch_mng` block.

## Sources

- Neo (neo-ask --chip a2), A2 evidence base: ip.d2d.rtl@9fc7ec3, ip.d2d.csr@5e98931, project.atlantic2.csr@2a12a536, project.atlantic2.rtl@875d851, ip.infra.rtl.shared.atlantic2@9bc4f713, ip.psoc.rtl@2bcf174c; A1: ip.d2d.rtl@88de8f9, ip.d2d.csr@9161c6b, project.iridium.csr@79631f4. Mirror snapshot 2026-11-09. Paraphrased, no HDL.
- Headers: ~/projects/iridium/two/fw/build_artifacts/include/ips/d2d/v2/1_3_0_1p3_870/prototype/d2d_regs.h; ~/projects/iridium/three/fw/build_artifacts/include/ips/d2d/v2/1_4_0_1p4_1047/prototype/d2d_mid_regs.h (diffed 2026-11-11).
