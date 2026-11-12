# A2 D2D Physical Variants and Wrapper CDUP

- **Recorded:** 2026-11-11 (corrected 2026-11-11)
- **Status:** Project facts confirmed by user; register placement verified from CSR headers and A1 FW; A2 RTL intent not independently verified
- **Tags:** a2, atlantic2, d2d, n3p, ew, ns, orientation, phy, physical variant, tapeout, rom, cdup, multicast, driver api

## Scope / Question
Record the A2 D2D physical-variant and wrapper-CDUP facts that correct earlier project
assumptions in the D2D IP skill.

## Findings

- A2 D2D is **TSMC N3P only, single die design (`die0`)**; the D2D PHY is the Alphawave N3P macro
  in **EW** (side) and **NS** (mid) orientations. The 3 nm vs 2 nm question is back-end only;
  the back-end flow (`project.atlantic2.be.die0/atlantic2_tech.yml`) shows N3P with
  `aresSE36n3_b_top_wrap` / `aresSN36n3_e_top_wrap` (v0p5p0). `Tn5` files are A1 leftovers.
- **One ROM** (inferred from the single die; not separately confirmed). D2D owns the D2D
  sequence in ROM; HSM integration and implementation stage remain unspecified.
- Variant/build behaviour differences are a **FW source-segregation** concern; which blocks go
  into emulation is not a FW decision.
- The A2 D2D wrapper has a CDUP register block in the checked CSR tag
  `1_3_0_1p3_870` (`D2D_CDUP_GENERAL`, offset `0x800`; `DUP_DISABLE` reset bit set).
  The A1 D2D wrapper CSR header has no CDUP symbols; A1 D2D-channel CDUP registers are in
  the NoC `d2d_ch_mng` header.
- The user confirmed A2 uses D2D-wrapper CDUP and that the **D2D driver provides its APIs**.
  The API consumer, topology/configuration source, reset/replay contract and freeze timing
  are not established by this finding.
- The A2 D2D topology is **12 instances and determined**, per the user. The exact mapping is
  not recorded here.
- **Planning decision:** a D2D ROM-sequence Epic exists in the A2 plan, with each sequence step
  as a Story. Milestone is deliberately unassigned for now.
- **Correction 2026-11-11 (Neo):** CDUP is in **mid wrappers only** (≤2 copies); tag 1047 removes
  17 CDUP registers — see `a2-cdup-mid-only-and-1047-removals.md`.
- **Resolved 2026-11-11 (Neo, user asked to confirm):** the A2 D2D PHY is a **single node, TSMC N3P**
  (Alphawave gammaCORE, `ucie_3nm` tags) in two **orientation** variants: **EW** (`aresSE36n3`,
  `gammaE64B1Tn3_d`) for side clusters via `d2d_side_phy_macro`, and **NS** (`aresSN36n3`,
  `gammaN64B1Tn3_e`) for mid clusters via `d2d_mid_phy_macro`/`_small`. **No 2nm and no 5nm D2D PHY.**
  `Tn5` remnants (`alpha_adapter.v`, an sdc, a tcl) are unused leftovers. Samsung SF2P appears only as
  a backend-flow setup in `ip.infra.be.flow` with no D2D tie [unverified]. RTL does not show a
  die→node mapping. Back-end flow confirms a single N3P die.
- **Devicetree (user, 2026-11-11):** DT describes only the **12 connected** instances.
- **Topology 2026-11-11:** 12 connected per spec vs 16 link slots in RTL — see
  `a2-topology-and-mng-windows.md`.

## A2 vs A1

A1 has no CDUP block in the D2D wrapper; A2's checked wrapper CSR does. This is a new D2D
driver API responsibility, even though A1 firmware programs other CDUP blocks in NoC/CCE.
Earlier D2D skill text that assigned the N3 PHY exclusively to the SU/DMP path is incorrect
for the D2D project: the user confirmed the N3 D2D variant. Do not infer the A2 RTL behavior
or the exact N3/A1-family implementation delta from the CSR and FW evidence alone.

## Sources

- User confirmation, 2026-11-11: A2 uses both D2D PHYs in two tape-outs/physical projects
  under one logical project; the physical variants have different ROMs; the 12-instance
  topology is determined; D2D wrapper CDUP is used and the D2D driver provides its APIs; D2D
  owns the D2D sequence in ROM; a ROM-sequence Epic with one Story per step is planned (milestone TBD).
- A2 CSR: `~/projects/iridium/two/fw/build_artifacts/include/ips/d2d/v2/1_3_0_1p3_870/prototype/d2d_regs.h`
  (`D2D_CDUP_GENERAL`, line 993) and `d2d_regs_attr.h` (`D2D_CDUP_GENERAL__RST`, line 600).
- A1 CSR: `~/projects/iridium/two/fw/build_artifacts/include/ips/d2d/v1/3_0_11_3p0_1389/prototype/d2d_regs.h`
  (no CDUP symbols); NoC D2D-channel CDUP is in
  `~/projects/iridium/two/fw/build_artifacts/include/ips/noc/v1/3_0_11_3p0_1389/prototype/d2d_ch_mng_regs.h`.
- A1 FW reference: `~/projects/iridium/one/fw/ip/cce/drivers/el_cce/el_cce.c`
  (`el_cce_disable_d2d_dups`, line 743); `~/projects/iridium/one/fw/ip/noc/drivers/el_noc/el_noc_cdup.c`
  (`el_noc_cdup_write_die`, line 246).
- **No A2 RTL behavior or intent is asserted here.** RTL questions must follow the VM-only
  Neo policy.
