# A2 D2D instance topology: 4 wrappers/die (2 mid=VD2D, 2 side=HD2D), 4 MNG windows/die; 12 connected per spec vs 16 in RTL

- **Recorded:** 2026-11-11
- **Status:** Neo RTL-verified (connectivity partly unverified)
- **Tags:** a2, atlantic2, d2d, topology, instances, hd2d, vd2d, mid, side, mng window, bases, edge die, bump map, 12 instances

## Scope / Question
Which D2D instances exist on A2, how they map to wrappers/AW windows, and how the 12-instance figure reconciles with RTL.

## Findings

- Per die: **4 D2D wrappers**, each with its own UCIe controller + PHY + AW window. `d2d_mid_cls` holds two mid wrappers (`d2d_m_top_0/1`, whoami VD2D_0/1); `d2d_side_cls_0/1` each hold one side wrapper (`d2d_s_wrap`, HD2D_0/1).
- A2 MNG windows: HD2D_0 0x600000, HD2D_1 0x680000, VD2D_0 0x700000, VD2D_1 0x780000 (0x80000 stride). HD2D_2/3 and VD2D_2/3 (0x6c0000/0x7c0000 style, 0x40000 stride) are **A1's table**; A2's FIU mass-write script marks them stale.
- RTL has 4 × 4 = **16 link instances**. Edge dies: dies 0 and 3 have no West HD2D, dies 1 and 2 have no East HD2D (FIU mass-write skip list), so 4 horizontal ends are unconnected → 12 connected. Whether every VD2D is connected is [unverified]; the package bump-map script is the authority.
- The **12 instances** figure is user-confirmed and matches the A2 overview ("12× UCIe 2.0 Standard Package", 768 GB/s). FW DT/SV should model 16 slots with 4 disabled, or 12, per the bump map.
- No off-package D2D in RTL.

## A2 vs A1

A1 table has 8 windows/die at a 0x40000 stride (HD2D_0..3, VD2D_0..3); A2 has 4 at a 0x80000 stride.

## Sources

- Neo (neo-ask --chip a2), A2 evidence base: ip.d2d.rtl@9fc7ec3, ip.d2d.csr@5e98931, project.atlantic2.csr@2a12a536, project.atlantic2.rtl@875d851, ip.infra.rtl.shared.atlantic2@9bc4f713, ip.psoc.rtl@2bcf174c; A1: ip.d2d.rtl@88de8f9, ip.d2d.csr@9161c6b, project.iridium.csr@79631f4. Mirror snapshot 2026-11-09. Paraphrased, no HDL.
- RTL refs (via Neo): d2d_mid_cls.v:723,1000; d2d_side_cls.v:405; a2_chip.v:16516-16517; ral_block_MNG.sv:525-555; global_pkg.v:1684-1695; fiu_mass_write.lua:304-323,351.
- A2 overview: atlantic2-prj `a2-overview.md:38`, `a2-d2d.md:30`.
- Supersedes the unqualified topology note in `a2-physical-variants-and-cdup.md`.
