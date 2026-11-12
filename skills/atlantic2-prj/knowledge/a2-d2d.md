# A2 (Atlantic2) — D2D Sub-System

- **Recorded:** 2026-08-30
- **Updated:** 2026-11-11
- **Status:** Spec, CSR headers and FW source; later project decisions confirmed by the user
- **A2-vs-A1 delta:** The architect's earlier guidance was to retain the core D2D design. The
  checked A2 wrapper CSR adds CDUP, which the user confirms is used and exposed through D2D
  driver APIs; the project also uses both 5nm and N3 PHY variants.
- **RTL branch/tag:** RTL diff done (mostly "align to a2"). n/a for spec.
- **Tags:** d2d, ucie, phy, 5nm, n3, aw, alphawave, cdup, firmware api, chiplet, die-to-die

## Architect input (AUTHORITATIVE — Maxim Leshin, 2026-08-30)
The FW planner emailed the architect: "I don't see any initiatives open for D2D at
all. Also the Rev 2 specification is an exact copy of Rev 1. What should be done on FW side on D2D
for A2?" Architect reply:
> **"We are keeping the D2D as is. The FW should be updated only if the new AW IP requires a
> different configuration."**

Interpretation:
- This is guidance to reuse the core design, not proof that there are no A2 wrapper, register
  map, topology or firmware changes. The CDUP CSR/FW evidence and subsequent user confirmation
  below establish a D2D driver API requirement.
- The user confirmed that A2 D2D uses **both 5nm and N3 PHYs** in two physical projects. An N3
  PHY also being present on another path does not disprove the D2D variant.

## Scope / Question
What changes for D2D in A2 vs A1, from the D2D Rev2 spec, the A2 Overview, and the user's PHY note.

## Findings (A2)
- **A2 D2D = UCIe-based, 12× UCIe 2.0 Standard Package**, BW/dir = 12 × 16b @ 32 Gbps =
  **768 GB/s** (A2 Overview para 128-129). Low-latency controller ~15 ns/direction (D2D spec).
- UCIe stack: Protocol (PCIe / CXL / streaming) → controller → PHY; standard & advanced package;
  UCIe 1.1 & **2.0** enhancements; UCIe-3D; UCIe manageability; UCIe RAS.
- **Two PHY IP versions in A2** (per user, authoritative):
  1. **5nm PHY** — same family as A1 (AlphaWave **AresCORE** PHY / **GammaCORE** controller /
     **GammaCORE32** N5 subsystem). Possibly minor logic changes vs A1.
  2. **Brand-new 3nm (N3) PHY** — NEW for A2. No N3 datasheet in `~/docs/D2D/` yet (only N5 docs:
     `arco_arescore_ucie_datasheet_n5...`, `Eland_gammaN64B1Tn5_...`). Add when available.
- The user confirmed a determined 12-instance topology, two tape-outs and different ROMs for
  the two physical variants. The instance mapping and any D2D-specific ROM differences are
  not documented by that confirmation.
- **ROM sequence ownership:** the user confirmed D2D owns the D2D sequence in ROM. The proposed
  `[A2] D2D: ROM sequence` Epic is planned for `iridium-system-sw-9.0`; each implementation step
  will be a Story, with details to be refined later. This remains a plan item only; no Jira issue
  has been created. HSM integration, implementation stage and variant-specific differences remain
  open.

## A2 vs A1 (Iridium) delta
- **D2D spec content is near-identical A1→A2** — `D2D Specification Rev 2.0.0.docx` (A2) vs
  `D2D Specification Rev1.docx` (A1) differ only in figure/table caption numbering (verified by
  full paragraph diff: 1016 paras each, no substantive text change).
- **PHY delta:** support both 3nm and 5nm variants. The exact per-variant firmware,
  configuration and initialization differences remain to be confirmed.

## CDUP placement — CSR and FW evidence (2026-11-11)

- **A1 D2D-wrapper CSR does not contain a CDUP block.** In A1, the D2D-facing CDUP registers
  are in the NoC `d2d_ch_mng` block (`CDUP_0` and `CDUP_1`), not in the D2D wrapper.
- **A2 adds a D2D-wrapper CDUP register block** in the checked D2D CSR tag
  `1_3_0_1p3_870`, beginning at `D2D_CDUP_GENERAL` (`0x800`). It includes duplication masks,
  destination-coordinate and inter-sled tables, credit/scale-up controls, statistics,
  response-error registers and rate limiting. The `GENERAL` reset value sets `DUP_DISABLE`.
- **A1 FW ownership is split by block, not one universal CDUP owner.** The NoC driver programs
  PRTR C2C CDUP coordinate maps and restores them after die reset; SE supplies the topology map.
  The CCE driver initializes aligner and PRTR-router CDUP, and explicitly sets the disable bit
  in the NoC D2D-channel CDUP blocks.
- **A2 usage and API ownership:** the user confirmed that D2D-wrapper CDUP is used and the D2D
  driver will provide its APIs. The caller, configuration policy, reset/replay ownership and
  freeze timing were not specified. No A1 D2D-FW implementation was identified in the checked
  workspace. CSR/FW evidence alone does not establish A2 RTL intent.

## CDUP findings — sources

- CSR headers, A1: `~/projects/iridium/two/fw/build_artifacts/include/ips/d2d/v1/3_0_11_3p0_1389/prototype/d2d_regs.h`
  (no CDUP symbols); NoC D2D-channel CDUP at
  `~/projects/iridium/two/fw/build_artifacts/include/ips/noc/v1/3_0_11_3p0_1389/prototype/d2d_ch_mng_regs.h`
  (`D2D_CH_MNG_CDUP_0_GENERAL`, line 473; corresponding `CDUP_1` block).
- CSR headers, A2: `~/projects/iridium/two/fw/build_artifacts/include/ips/d2d/v2/1_3_0_1p3_870/prototype/d2d_regs.h`
  (`D2D_CDUP_GENERAL`, line 993); `d2d_regs_attr.h` in the same directory
  (`D2D_CDUP_GENERAL__RST`, line 600).
- FW: `~/projects/iridium/one/fw/ip/noc/drivers/el_noc/el_noc_cdup.c` (PRTR programming at
  line 246; map restore at line 359); `~/projects/iridium/one/fw/ip/se/drivers/scale_engine/el_se_cdup.c`
  (SE supplies the topology map, line 67); `~/projects/iridium/one/fw/ip/cce/drivers/el_cce/el_cce.c`
  (D2D-channel duplication disabled, lines 743–752; PRTR and aligner setup, lines 757 and 780).
- Evidence is from the checked CSR headers and firmware source only; **RTL behavior/intent has
  not been independently confirmed**.
- User input, 2026-11-11: "it is used. since its in D2D Wrapper, D2D driver will give the APIs
  for it."

## Sources
- Doc: `~/docs/A2/Atlantic2 Overview.docx` (D2D 231-235; feature set 127-129).
- Doc: `~/docs/D2D/D2D Specification Rev 2.0.0.docx` (A2) vs `D2D Specification Rev1.docx` (A1) —
  paragraph diff showed no substantive content change.
- PHY node facts: user statement (5nm same-as-A1 + new 3nm). See `d2d-ip` skill for PHY/controller
  datasheets and register maps.
- User input, 2026-11-11: "we own the d2d sequence in ROM"; "epic should just generally mention D2D ROM sequence, every step there will be a story."
- **Historical RTL note:** earlier research recorded hard-macro PHY stubs and alignment changes.
  Its revision and current model availability have not been independently reverified here; ask
  Neo for current RTL questions. The CSR and user-confirmed CDUP delta above must not be reduced
  to a PHY-only firmware change.
